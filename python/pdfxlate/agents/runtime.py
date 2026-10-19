"""Spawn one agent CLI, feed it a batch, and parse the answer.

This is the only module that touches processes.  Its rules come straight from
the feature brief:

* every run gets an **empty temporary working directory** under the cache, so an
  agent that decides to write files writes them somewhere disposable;
* the child's whole process tree is killed on timeout or cancellation (Windows
  ``taskkill /F /T``, POSIX process-group terminate then kill);
* output is parsed tolerantly, then strictly, then one paragraph at a time;
* an authentication failure is classified as permanent and carries a fix hint;
* the prompt, the environment and raw probe output are never logged.

The output contract is a JSON object with a ``translations`` array.  Agents are
chatty and inconsistent, though, so the parser also accepts an index-keyed
object, a bare array of the right length, and (for a single paragraph) plain
text.  The parser is a pure function so the fallbacks can be tested without
starting a process.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from string import Template
from typing import Any, Iterable, Sequence

import yaml

from pdfxlate import paths
from pdfxlate.agents import catalog
from pdfxlate.agents.defs import AgentDef
from pdfxlate.models.base import AgentAuthError

logger = logging.getLogger(__name__)

#: One batch may take a while — an agent CLI is an LLM call plus its own startup.
DEFAULT_TIMEOUT = 300.0
#: How often the wait loop checks cancellation and the timeout.
POLL_INTERVAL = 0.2
#: stderr text included in an error.  Enough to diagnose, short enough that a
#: stack trace or a key cannot fill the UI.
STDERR_LIMIT = 300
#: Agent ids that use a tool-capable CLI.  Kept for the hints below.
_AUTH_HINTS = {
    "claude": "run `claude` in a terminal and complete the login, then try again",
    "codex": "run `codex login` in a terminal, then try again",
    "workbuddy": "run `codebuddy` in a terminal and log in, then try again",
    "zcode": "open ZCode and make sure it is signed in, then try again",
    "dsh": "make sure the DeepSeek Harness profile has a working model configured",
}

#: Claude's structured-output schema for a batch.
CLAUDE_TRANSLATIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "translations": {"type": "array", "items": {"type": "string"}}
    },
    "required": ["translations"],
    "additionalProperties": False,
}

_ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)
_AUTH_PATTERNS = (
    "not logged in",
    "logged out",
    "please log in",
    "please login",
    "sign in",
    "unauthorized",
    "unauthenticated",
    "authentication failed",
    "invalid api key",
    "api key is invalid",
    "invalid token",
    "token expired",
    "no credentials",
    "credentials not found",
    "permission denied",
    "401",
    "403",
)


# --- errors ----------------------------------------------------------------


class AgentError(RuntimeError):
    """Base class for a failure while driving an external agent CLI."""


class AgentNotInstalledError(AgentError):
    """The configured agent's executable could not be found."""


class AgentTimeoutError(AgentError):
    """The CLI did not answer within the allotted time.  Retryable."""


class AgentProcessError(AgentError):
    """The CLI exited non-zero (or reported an error envelope).  Retryable."""


class AgentOutputError(AgentError):
    """The CLI's output could not be turned into one translation per input."""


# --- prompts ---------------------------------------------------------------


def strip_ansi(text: str) -> str:
    """Remove terminal escape sequences from CLI output."""
    return _ANSI_RE.sub("", text or "")


def build_batch_prompt(texts: Sequence[str], lang_out: str, strict: bool = False) -> str:
    """The default prompt for a multi-paragraph batch."""
    lines = [
        f"Translate every string in the JSON array below into {lang_out}.",
        "Keep any formula placeholder such as {v0} or {v12} exactly as written, in place.",
        "Do not merge, split, add or drop items.",
        'Reply with ONLY a JSON object of the form {"translations": ["...", ...]}, '
        "in the same order and with the same number of items, with no commentary.",
    ]
    if strict:
        lines.append(
            f"IMPORTANT: your previous reply could not be parsed. Reply with exactly "
            f"{len(texts)} translated strings inside that JSON object, and nothing else."
        )
    return "\n".join(lines) + "\n\n" + json.dumps(list(texts), ensure_ascii=False)


def build_single_prompt(text: str, lang_out: str, strict: bool = False) -> str:
    """The prompt used after a batch is split into single paragraphs."""
    prompt = (
        f"Translate the following text into {lang_out}. Keep any formula "
        "placeholder such as {v0} exactly as written, in place. Reply with ONLY "
        "the translated text, with no explanation.\n\n" + text
    )
    if strict:
        prompt += "\n\nReply with the translated text only — no JSON, no commentary."
    return prompt


def render_custom_prompt(
    template: str, text: str, lang_in: str, lang_out: str
) -> str:
    """Render the user's own prompt template for one paragraph.

    Mirrors :meth:`ModelAdapter.build_messages`: ``safe_substitute`` so a
    malformed ``$`` in the template does not blow up a job.
    """
    return Template(template).safe_substitute(
        lang_in=lang_in, lang_out=lang_out, text=text
    )


# --- parsing ---------------------------------------------------------------


def _strip_fences(text: str) -> str:
    match = _FENCE_RE.search(text)
    return match.group(1).strip() if match else text.strip()


def _index_keyed(payload: dict, count: int) -> list[str] | None:
    """Accept ``{"0": "...", "1": "..."}`` (and the 1-based spelling)."""
    by_index: dict[int, str] = {}
    for key, value in payload.items():
        if isinstance(key, int):
            index = key
        elif isinstance(key, str) and key.strip().isdigit():
            index = int(key.strip())
        else:
            continue
        if not isinstance(value, str):
            return None
        by_index[index] = value
    if len(by_index) != count:
        return None
    if set(by_index) == set(range(count)):
        return [by_index[i] for i in range(count)]
    if set(by_index) == set(range(1, count + 1)):
        return [by_index[i] for i in range(1, count + 1)]
    return None


def parse_translations(payload: Any, texts: Sequence[str]) -> list[str] | None:
    """Turn a CLI reply into exactly ``len(texts)`` strings, or ``None``.

    Priority (from the brief): ``{"translations": [...]}`` → index-keyed object
    → a bare array of the right length that is not an echo of the input → a
    single plain-text reply.  ``None`` means "not parseable", which is what
    triggers the strict retry and then the per-paragraph split.
    """
    count = len(texts)
    if count == 0:
        return []

    if isinstance(payload, str):
        raw = strip_ansi(payload).strip()
        if not raw:
            return None
        # A JSON document may be wrapped in a markdown fence or preceded by
        # chatter; try the whole text first, then the fenced block.
        for candidate in (raw, _strip_fences(raw)):
            try:
                parsed = json.loads(candidate)
            except ValueError:
                continue
            if isinstance(parsed, (dict, list)):
                return parse_translations(parsed, texts)
        # Plain text is only unambiguous for a single paragraph.
        if count == 1:
            return [raw]
        return None

    if isinstance(payload, dict):
        translations = payload.get("translations")
        if isinstance(translations, list):
            if len(translations) != count:
                return None
            if not all(isinstance(item, str) for item in translations):
                return None
            return list(translations)
        return _index_keyed(payload, count)

    if isinstance(payload, list):
        if len(payload) != count:
            return None
        if not all(isinstance(item, str) for item in payload):
            return None
        values = list(payload)
        # An array identical to the input is an echo, not a translation.  A
        # legitimate translation of a paragraph is essentially never byte-equal
        # across every item, so rejecting this is safe.
        if count and values == list(texts):
            return None
        return values

    return None


def _payload_text(payload: Any) -> str:
    """A short, log-safe rendering of a payload for error messages."""
    if isinstance(payload, str):
        text = payload
    else:
        try:
            text = json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(payload)
    return _truncate(strip_ansi(text))


def _truncate(text: str, limit: int = STDERR_LIMIT) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _looks_like_auth(text: str) -> bool:
    lowered = (text or "").lower()
    return any(pattern in lowered for pattern in _AUTH_PATTERNS)


# --- process helpers -------------------------------------------------------


def _spawn_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if flags:
            kwargs["creationflags"] = flags
    else:
        # A new session makes the child a process-group leader, so the whole
        # tree can be signalled instead of just the direct child.
        kwargs["start_new_session"] = True
    return kwargs


def _is_cancelled(cancel_event: Any) -> bool:
    if cancel_event is None:
        return False
    try:
        return bool(cancel_event.is_set())
    except Exception:  # noqa: BLE001 - a foreign event object must not crash a job
        return False


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the child and everything it spawned."""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=10,
                **_spawn_kwargs(),
            )
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            proc.kill()
        except OSError:
            pass
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            try:
                proc.terminate()
            except OSError:
                pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                try:
                    proc.kill()
                except OSError:
                    pass
    try:
        proc.wait(timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        pass


def _read_stream(stream, sink: list[str]) -> None:
    try:
        sink.append(stream.read())
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except (OSError, ValueError):
            pass


def _run_process(
    argv: Sequence[str],
    prompt: str | None,
    workdir: Path,
    env: dict[str, str],
    timeout: float,
    cancel_event: Any,
) -> tuple[int, str, str]:
    """Run one CLI, returning ``(returncode, stdout, stderr)``.

    stdout/stderr are drained on threads so a chatty child cannot fill a pipe
    and deadlock while we are polling.  The wait loop checks cancellation and
    the deadline every :data:`POLL_INTERVAL` seconds.
    """
    kwargs: dict[str, Any] = {
        "cwd": str(workdir),
        "env": env,
        "stdin": subprocess.PIPE if prompt is not None else subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        **_spawn_kwargs(),
    }
    try:
        proc = subprocess.Popen(list(argv), **kwargs)
    except OSError as exc:
        raise AgentProcessError(
            f"could not start the agent CLI ({type(exc).__name__}: {exc})"
        ) from exc

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    out_thread = threading.Thread(
        target=_read_stream, args=(proc.stdout, stdout_chunks), daemon=True
    )
    err_thread = threading.Thread(
        target=_read_stream, args=(proc.stderr, stderr_chunks), daemon=True
    )
    out_thread.start()
    err_thread.start()

    if prompt is not None:
        def write_prompt() -> None:
            try:
                assert proc.stdin is not None
                proc.stdin.write(prompt)
                proc.stdin.close()
            except (OSError, ValueError):
                pass

        threading.Thread(target=write_prompt, daemon=True).start()

    started = time.monotonic()
    try:
        while proc.poll() is None:
            if _is_cancelled(cancel_event):
                _kill_tree(proc)
                raise asyncio.CancelledError("task cancelled")
            if time.monotonic() - started > timeout:
                _kill_tree(proc)
                raise AgentTimeoutError(
                    f"the agent CLI did not finish within {timeout:g}s; "
                    "it was stopped. Try again, or choose a smaller/faster model."
                )
            time.sleep(POLL_INTERVAL)
    finally:
        if proc.poll() is None:
            _kill_tree(proc)
        out_thread.join(timeout=5)
        err_thread.join(timeout=5)

    return (proc.returncode if proc.returncode is not None else 0), "".join(
        stdout_chunks
    ), "".join(stderr_chunks)


def _make_workdir() -> Path:
    """An empty, disposable working directory for one CLI run."""
    try:
        root = paths.cache_dir() / "agents"
        root.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix="run-", dir=root))
    except OSError as exc:
        raise AgentError(
            f"could not create a temporary working directory for the agent CLI: {exc}"
        ) from exc


def _zcode_env(command: Sequence[str], base: dict[str, str]) -> dict[str, str]:
    """Point ZCode's built-in provider config at the copy beside its script."""
    env = dict(base)
    script = Path(command[-1])
    builtin = script.parent.parent / "config" / "provider" / "zcode-builtin.json"
    if builtin.is_file():
        # These are the two names the bundled CLI reads (see the ZCode bundle's
        # resolveNodeProviderRuntimePaths).  Setting only the active path is
        # enough; the bundled one keeps its refresh logic from looking elsewhere.
        env["ZCODE_BUILTIN_PROVIDER_CONFIG_FILE"] = str(builtin)
        env["ZCODE_BUILTIN_PROVIDER_BUNDLED_CONFIG_FILE"] = str(builtin)
    return env


def split_agent_model(
    model: str | None,
    command: Sequence[str] | None = None,
) -> tuple[str | None, str]:
    """Split a configured DSH model id into ``(provider, model)``.

    The Agents panel stores the composite ``provider/model`` spelling so that
    two providers serving the same bare id stay distinguishable.  A bare id
    (hand-written config, or the headless dump) falls back to the desktop
    profile's default provider, and finally to the lazily-read dump mapping.
    Model ids that themselves contain ``/`` are recognised from the desktop
    profile before the first slash is treated as a separator.
    """
    chosen = (model or "").strip()
    if not chosen or chosen == "default":
        return None, chosen
    owner = catalog.desktop_provider_for_model(chosen)
    if owner:
        return owner, chosen
    if "/" in chosen:
        provider, _, tail = chosen.partition("/")
        if provider and tail:
            return provider, tail
    provider = catalog.provider_for_model("dsh", chosen, command=command)
    if not provider:
        default = catalog.dsh_default_model()
        if default:
            provider = default[0]
    return provider, chosen


def build_dsh_patch(provider: str | None, model: str) -> str:
    """The ``--patch`` overlay that selects one DSH model.

    The overlay carries the provider's whole ``llm-pi-ai`` entry so the
    headless profile learns the endpoint, plus the usual
    ``agent-default-model`` override.  Provider configs are sanitised: only the
    ``apiKeyEnv`` *name* is ever written, never a key value.

    When the provider is unknown (an old config, or a DSH install without a
    desktop profile) the overlay degrades to the previous behaviour — just
    ``agent-default-model`` — and DSH itself reports the bad selection.
    """
    entries: list[dict[str, Any]] = []
    config = catalog.dsh_provider_config(provider)
    if config is not None:
        config = catalog.sanitize_provider_config(config)
        entries.append(
            {
                "id": "llm-pi-ai",
                "name": "@deepseek-ai/dsh-llm-pi-ai",
                "config": {"providers": {str(provider): config}},
            }
        )
    default_config: dict[str, Any] = {}
    if provider:
        default_config["provider"] = provider
    default_config["model"] = model
    entries.append(
        {
            "id": "agent-default-model",
            "name": "@deepseek-ai/dsh-agent-default-model",
            "config": default_config,
        }
    )
    return yaml.safe_dump(
        entries, sort_keys=False, allow_unicode=True, default_flow_style=False
    )


def _dsh_credentials_path() -> Path:
    """The credentials store beside the DSH profile (``$DSH_HOME`` aware)."""
    return catalog.dsh_home() / ".credentials.yaml"


def _find_named_string(payload: Any, name: str) -> str | None:
    """Find ``name``'s string value anywhere in a decoded YAML document."""
    if isinstance(payload, dict):
        direct = payload.get(name)
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        for value in payload.values():
            found = _find_named_string(value, name)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _find_named_string(value, name)
            if found:
                return found
    return None


def _dsh_credential(name: str) -> str | None:
    """Read one named credential from DSH's store.

    The desktop profile names an environment variable (``apiKeyEnv``).  DSH's
    desktop app stores the value in ``.credentials.yaml`` rather than the
    environment, so a headless child started by this process would otherwise
    not see it.  The value is returned only for child-env injection and is
    never logged or written anywhere.
    """
    if not name:
        return None
    path = _dsh_credentials_path()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    return _find_named_string(data, name)


def _inject_dsh_credentials(
    provider_config: dict[str, Any] | None, env: dict[str, str]
) -> dict[str, str]:
    """Add the selected provider's key to the child env when it is missing."""
    if not isinstance(provider_config, dict):
        return env
    name = str(provider_config.get("apiKeyEnv") or "").strip()
    if not name or env.get(name):
        return env
    value = _dsh_credential(name)
    if not value:
        # DSH will report the missing key itself; failing here would hide its
        # (more precise) message.
        return env
    child_env = dict(env)
    child_env[name] = value
    return child_env


def _write_dsh_patch(workdir: Path, text: str) -> Path:
    """Write the overlay to a unique file inside the run's disposable cwd."""
    handle, name = tempfile.mkstemp(
        prefix="dsh-patch-", suffix=".yaml", dir=str(workdir)
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    except BaseException:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise
    return Path(name)


def _build_command(
    defn: AgentDef,
    command: Sequence[str],
    model: str | None,
    prompt: str,
    *,
    custom_prompt: bool,
    workdir: Path,
    last_message_path: Path,
) -> tuple[list[str], str | None, dict[str, str]]:
    """Return ``(argv, stdin_prompt, env)`` for one run.

    ``stdin_prompt`` is ``None`` for argv-mode agents (ZCode).
    """
    base = list(command)
    env = dict(os.environ)
    chosen = (model or "").strip()
    has_model = bool(chosen) and chosen != "default"

    if defn.id == "claude":
        argv = [
            *base,
            "-p",
            "--input-format",
            "text",
            "--output-format",
            "json",
        ]
        if not custom_prompt:
            # A custom template may ask for anything; forcing a translations
            # schema onto it would contradict the template.
            argv += [
                "--json-schema",
                json.dumps(CLAUDE_TRANSLATIONS_SCHEMA, separators=(",", ":")),
            ]
        argv += [
            "--tools",
            "",
            "--no-session-persistence",
            # Translation needs neither MCP servers nor skills; skipping both
            # avoids their startup/health-check cost in every batch process.
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
        ]
        if not custom_prompt:
            argv += ["--disable-slash-commands"]
        if has_model:
            argv += ["--model", chosen]
        return argv, prompt, env

    if defn.id == "codex":
        argv = [
            *base,
            "exec",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--ephemeral",
            "--output-last-message",
            str(last_message_path),
        ]
        if has_model:
            argv += ["-m", chosen]
        # No trailing "-": codex exec reads the prompt from stdin when no
        # positional prompt is given.
        return argv, prompt, env

    if defn.id == "workbuddy":
        argv = [*base, "-p", "--output-format", "text"]
        if has_model:
            argv += ["--model", chosen]
        return argv, prompt, env

    if defn.id == "zcode":
        # The CLI has no --model flag, and sending "/model" through -p would be
        # consumed as a real (billed) prompt.  The only honest thing to do with
        # a configured model is verify it against the model ZCode itself is
        # pointing at; a mismatch is reported instead of silently running the
        # wrong model.
        if has_model:
            current = catalog.zcode_current_model()
            if current is not None:
                expected = f"{current[0]}/{current[1]}"
                if chosen != expected and chosen != current[1]:
                    raise AgentError(
                        "ZCode CLI can only use its current model "
                        f"({expected}); switch models inside ZCode first"
                    )
        argv = [
            *base,
            "-p",
            prompt,
            "--no-color",
            "--mode",
            "plan",
            "--surface",
            "terminal",
            "--cwd",
            str(workdir),
        ]
        return argv, None, _zcode_env(base, env)

    if defn.id == "dsh":
        argv = [*base, "--profile", "headless"]
        if has_model:
            provider, bare_model = split_agent_model(chosen, command=base)
            patch_path = _write_dsh_patch(
                workdir, build_dsh_patch(provider, bare_model)
            )
            argv += ["--patch", str(patch_path)]
            # The desktop profile names the key's environment variable; its
            # value may live only in DSH's credential store.  Inject it into
            # this child's environment, never into the patch file or a log.
            env = _inject_dsh_credentials(
                catalog.dsh_provider_config(provider), env
            )
        argv += ["-"]
        return argv, prompt, env

    # Unknown ids are rejected by get_agent_def before we get here.
    raise AgentError(f"no command builder for agent {defn.id!r}")


def _claude_envelope(stdout: str) -> Any:
    """Parse Claude's JSON envelope, tolerating a trailing non-JSON line."""
    text = stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        for line in reversed(text.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


def _extract_payload(
    defn: AgentDef, stdout: str, stderr: str, last_message: str | None
) -> Any:
    """Pull the agent's actual answer out of its wrapper output."""
    if defn.id == "claude":
        envelope = _claude_envelope(stdout)
        if isinstance(envelope, dict):
            if envelope.get("is_error"):
                message = str(envelope.get("result") or envelope.get("error") or stderr)
                raise AgentProcessError(
                    f"{defn.name} reported an error: {_truncate(message)}"
                )
            if "result" in envelope:
                return envelope["result"]
            return envelope
        if envelope is not None:
            return envelope
        return stdout

    if defn.id == "codex":
        return last_message if last_message and last_message.strip() else stdout

    return stdout


def _classify_auth(defn: AgentDef, text: str) -> None:
    if _looks_like_auth(text):
        hint = _AUTH_HINTS.get(defn.id, "log in to the CLI and try again")
        raise AgentAuthError(
            f"{defn.name} is not logged in or rejected the request; {hint}. "
            f"({_truncate(text)})"
        )


def _run_attempt(
    defn: AgentDef,
    command: Sequence[str],
    model: str | None,
    texts: Sequence[str],
    lang_out: str,
    custom_prompt: str | None,
    timeout: float,
    cancel_event: Any,
    *,
    strict: bool,
    lang_in: str = "en",
) -> list[str]:
    """One CLI invocation.  Raises on process/auth/timeout/parse failure."""
    if custom_prompt is not None:
        prompt = render_custom_prompt(
            custom_prompt, texts[0], lang_in, lang_out
        )
    elif len(texts) == 1 and strict:
        prompt = build_single_prompt(texts[0], lang_out, strict=True)
    elif len(texts) == 1:
        prompt = build_batch_prompt(texts, lang_out, strict=False)
    else:
        prompt = build_batch_prompt(texts, lang_out, strict=strict)

    workdir = _make_workdir()
    last_message_path = workdir / "last-message.txt"
    try:
        argv, stdin_prompt, env = _build_command(
            defn,
            command,
            model,
            prompt,
            custom_prompt=custom_prompt is not None,
            workdir=workdir,
            last_message_path=last_message_path,
        )
        if stdin_prompt is not None and len(stdin_prompt) > 200_000:
            raise AgentOutputError(
                f"the prompt for {defn.name} is unreasonably large "
                f"({len(stdin_prompt)} characters); this paragraph is too long"
            )
        logger.debug(
            "agent %s: running batch of %d paragraph(s), model=%s",
            defn.id,
            len(texts),
            model or "default",
        )
        returncode, stdout, stderr = _run_process(
            argv, stdin_prompt, workdir, env, timeout, cancel_event
        )
        # Read Codex's last-message file before the working directory is
        # removed; the file lives inside it.
        last_message: str | None = None
        if defn.id == "codex":
            try:
                last_message = last_message_path.read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError:
                last_message = None
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    _classify_auth(defn, stderr)
    if returncode != 0:
        tail = _truncate(stderr or stdout)
        _classify_auth(defn, tail)
        raise AgentProcessError(
            f"{defn.name} exited with code {returncode}"
            + (f": {tail}" if tail else "")
        )

    try:
        payload = _extract_payload(defn, stdout, stderr, last_message)
    except AgentProcessError as exc:
        _classify_auth(defn, str(exc))
        raise

    translations = parse_translations(payload, texts)
    if translations is None:
        raise AgentOutputError(
            f"could not parse {defn.name}'s output into {len(texts)} "
            f"translation(s); output began: {_payload_text(payload)}"
        )
    return translations


def run_batch(
    defn: AgentDef,
    executable: Sequence[str] | str,
    model: str | None,
    texts: Iterable[str],
    lang_out: str,
    custom_prompt: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    cancel_event: Any = None,
    *,
    lang_in: str = "en",
) -> list[str]:
    """Translate ``texts`` with one agent CLI invocation (plus retries).

    ``executable`` is the command prefix resolved by the detector — for ZCode
    that is ``(node, zcode.cjs)``, otherwise the binary itself.  A custom prompt
    template forces one paragraph per call, because the template's own output
    contract cannot be batched safely.
    """
    texts = list(texts)
    if not texts:
        return []
    command = [executable] if isinstance(executable, str) else list(executable)
    if not command:
        raise AgentNotInstalledError(f"{defn.name} has no executable configured")

    if custom_prompt is not None:
        return [
            _run_attempt(
                defn,
                command,
                model,
                [text],
                lang_out,
                custom_prompt,
                timeout,
                cancel_event,
                strict=False,
                lang_in=lang_in,
            )[0]
            for text in texts
        ]

    try:
        return _run_attempt(
            defn,
            command,
            model,
            texts,
            lang_out,
            None,
            timeout,
            cancel_event,
            strict=False,
            lang_in=lang_in,
        )
    except AgentOutputError:
        # One stricter retry with an explicit "reply with only JSON" reminder.
        try:
            return _run_attempt(
                defn,
                command,
                model,
                texts,
                lang_out,
                None,
                timeout,
                cancel_event,
                strict=True,
                lang_in=lang_in,
            )
        except AgentOutputError:
            if len(texts) == 1:
                raise
            # A malformed batch is not a reason to lose the whole page: ask for
            # each paragraph separately.
            results: list[str] = []
            for text in texts:
                results.append(
                    _run_attempt(
                        defn,
                        command,
                        model,
                        [text],
                        lang_out,
                        None,
                        timeout,
                        cancel_event,
                        strict=True,
                        lang_in=lang_in,
                    )[0]
                )
            return results


__all__ = [
    "CLAUDE_TRANSLATIONS_SCHEMA",
    "DEFAULT_TIMEOUT",
    "AgentAuthError",
    "AgentError",
    "AgentNotInstalledError",
    "AgentOutputError",
    "AgentProcessError",
    "AgentTimeoutError",
    "build_batch_prompt",
    "build_dsh_patch",
    "build_single_prompt",
    "parse_translations",
    "render_custom_prompt",
    "run_batch",
    "split_agent_model",
    "strip_ansi",
]
