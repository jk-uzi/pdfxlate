"""Test a ZCode model through ZCode's own stdio app-server.

The ZCode CLI has no ``--model`` and the desktop app only runs its current
session model.  The app-server, however, exposes the official
``provider/testModelConnectivity`` method: it asks the runtime to make one tiny
model call with an arbitrary provider/model selection, in a throwaway
workspace, without creating a session.  That is exactly what the Agents panel's
manual "Test" button needs.

This module is intentionally test-only.  Translation still uses the CLI's
current model; nothing here changes the ZCode current model,
``provider_config.json``, credentials, or the session DB (verified against the
real v0.16.9 app-server: current model, config, credentials and DB hashes were
unchanged after a real ``hy3`` probe).

The ZCode Protocol used over stdio is *JSON-RPC shaped but without the
``jsonrpc`` field*: requests/responses are one JSON object per line, e.g.
``{"id": 2, "method": "provider/testModelConnectivity", "params": {...}}`` and
``{"id": 2, "result": {"success": true}}``.  Notifications such as
``startup/storageState`` arrive on the same stream and are ignored.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

logger = logging.getLogger(__name__)

#: The app-server's own connectivity timeout is 60 s; leave room for startup.
DEFAULT_TIMEOUT = 90.0
#: Startup (storageState + provider registry) took ~5 s on the reference machine.
STARTUP_TIMEOUT = 20.0
_WORKSPACE_KEY = "pdfxlate-agent-test"


class ZCodeAppServerError(RuntimeError):
    """A readable failure from the ZCode app-server."""


@dataclass(frozen=True)
class ServerPaths:
    """Everything needed to start the app-server in the user's environment."""

    command: tuple[str, ...]
    builtin_config: Path
    personal_config: Path
    data_base: Path


def _is_script(path: Path) -> bool:
    return path.suffix.lower() in {".cjs", ".js", ".mjs"}


def resolve_paths(
    paths: Mapping[str, str] | None = None,
    *,
    cli_path: str | None = None,
    home: Path | None = None,
) -> ServerPaths:
    """Locate the CLI bundle, the built-in provider config and the user home.

    ``cli_path``/``home`` exist for tests; production resolves the ZCode CLI
    exactly like the rest of the agent code (PATH/env/agents.paths/registry).
    """
    if cli_path is None:
        from pdfxlate.agents.detect import get_agent_def, resolve_agent

        resolved = resolve_agent(get_agent_def("zcode"), paths or {})
        if not resolved.installed or not resolved.command:
            raise ZCodeAppServerError(
                "ZCode is not installed or its CLI could not be resolved"
            )
        command = tuple(resolved.command)
        cli = Path(resolved.path or command[-1])
    else:
        cli = Path(cli_path)
        node = shutil.which("node")
        if node is None:
            raise ZCodeAppServerError("Node.js was not found on PATH")
        command = (node, str(cli))

    root = cli.parent.parent
    candidates = (
        root / "config" / "provider" / "zcode-builtin.json",
        cli.parent / "provider" / "zcode-builtin.json",
        root / "provider" / "zcode-builtin.json",
        root.parent / "config" / "provider" / "zcode-builtin.json",
    )
    builtin = next((candidate for candidate in candidates if candidate.is_file()), None)
    if builtin is None:
        raise ZCodeAppServerError(
            "could not find ZCode's built-in provider config "
            "(resources/config/provider/zcode-builtin.json)"
        )

    base = home or Path.home()
    personal = base / ".zcode" / "v2" / "provider_config.json"
    if not personal.is_file():
        raise ZCodeAppServerError("could not find ~/.zcode/v2/provider_config.json")
    return ServerPaths(
        command=command,
        builtin_config=builtin,
        personal_config=personal,
        data_base=base,
    )


class _StdioServer:
    """Minimal line-oriented client for the ZCode Protocol app-server."""

    def __init__(self, command: Sequence[str], env: Mapping[str, str], cwd: str) -> None:
        self.process = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self.messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self.stderr: list[str] = []
        self._threads = [
            threading.Thread(target=self._read_stdout, daemon=True),
            threading.Thread(target=self._read_stderr, daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                logger.debug("ignoring non-JSON app-server output: %s", line[:200])
                continue
            if isinstance(message, dict):
                self.messages.put(message)

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        for line in self.process.stderr:
            self.stderr.append(line.strip())
            if len(self.stderr) > 40:
                del self.stderr[:10]

    def _error_tail(self) -> str:
        tail = [line for line in self.stderr if line][-4:]
        return "; ".join(tail)

    def send(self, message: Mapping[str, Any]) -> None:
        assert self.process.stdin is not None
        try:
            self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise ZCodeAppServerError(
                f"could not write to the ZCode app-server: {exc}"
            ) from exc

    def wait_for(self, request_id: int, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                message = self.messages.get(timeout=0.2)
            except queue.Empty:
                if self.process.poll() is not None:
                    tail = self._error_tail()
                    raise ZCodeAppServerError(
                        "the ZCode app-server exited before answering"
                        + (f": {tail}" if tail else "")
                    )
                continue
            if message.get("id") != request_id:
                continue
            error = message.get("error")
            if isinstance(error, dict):
                text = str(error.get("message") or error)
                raise ZCodeAppServerError(f"ZCode app-server rejected the test: {text}")
            result = message.get("result")
            if isinstance(result, dict):
                return result
            raise ZCodeAppServerError("the ZCode app-server returned an empty result")
        tail = self._error_tail()
        raise ZCodeAppServerError(
            "the ZCode app-server did not answer in time"
            + (f": {tail}" if tail else "")
        )

    def stop(self) -> None:
        try:
            if self.process.stdin is not None:
                self.process.stdin.close()
        except OSError:
            pass
        if self.process.poll() is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self.process.pid)],
                    capture_output=True,
                    check=False,
                )
            else:
                try:
                    self.process.terminate()
                except OSError:
                    pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                self.process.kill()
            except OSError:
                pass
        for stream in (self.process.stdout, self.process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass


def test_model(
    provider: str,
    model: str,
    *,
    paths: Mapping[str, str] | None = None,
    server_paths: ServerPaths | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Ask ZCode to make one tiny call with ``provider/model``.

    Returns ``{"ok": True, "latency_ms": int, "via": "app_server"}``.  Raises
    :class:`ZCodeAppServerError` with a human-readable message on failure.
    """
    provider = provider.strip()
    model = model.strip()
    if not provider or not model:
        raise ZCodeAppServerError("a ZCode model needs both a provider and a model id")
    server = server_paths or resolve_paths(paths)

    with tempfile.TemporaryDirectory(prefix="pdfxlate-zcode-test-") as tmp:
        workspace = Path(tmp) / "workspace"
        workspace.mkdir()
        env = dict(os.environ)
        env["ZCODE_DATA_BASE_DIR"] = str(server.data_base)
        env["ZCODE_BUILTIN_PROVIDER_CONFIG_FILE"] = str(server.builtin_config)
        env["ZCODE_PERSONAL_PROVIDER_CONFIG_FILE"] = str(server.personal_config)
        command = [*server.command, "app-server", "--stdio"]
        started = time.monotonic()
        client = _StdioServer(command, env, tmp)
        try:
            client.send(
                {"id": 1, "method": "runtime/capabilities", "params": {}}
            )
            client.wait_for(1, STARTUP_TIMEOUT)
            client.send(
                {
                    "id": 2,
                    "method": "provider/testModelConnectivity",
                    "params": {
                        "workspace": {
                            "workspacePath": str(workspace),
                            "workspaceKey": _WORKSPACE_KEY,
                        },
                        "selection": {
                            "providerId": provider,
                            "modelId": model,
                        },
                    },
                }
            )
            result = client.wait_for(2, timeout)
            if result.get("success") is not True:
                raise ZCodeAppServerError(
                    "ZCode reported that the model is not reachable"
                )
            return {
                "ok": True,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "via": "app_server",
            }
        finally:
            client.stop()
