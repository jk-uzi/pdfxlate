"""Find the agent CLIs installed on this machine and probe their state.

Resolution order, per the feature brief:

1. an explicit path from ``agents.paths.<id>`` in config.yaml;
2. an environment override (``PDFXLATE_CLAUDE_BIN`` and friends);
3. ``shutil.which`` for the executable (plus any fallback names);
4. platform standard install directories, and on Windows the uninstall
   registry entries written by the agents' own installers.

Everything here is read-only and failure-tolerant: a probe that hangs, crashes
or prints garbage degrades to "unknown" rather than taking the settings panel
down with it.

Security note
-------------
A login probe's output is **never** returned, logged or embedded in an error.
``codex login status`` prints a partially masked API key on stderr, so the only
thing that leaves this module is one of ``ok`` / ``missing`` / ``unknown``.
Version output is parsed into a version string and discarded.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

from pdfxlate.agents.defs import AGENT_DEFS, AgentDef, get_agent_def

logger = logging.getLogger(__name__)

#: How long a version probe may take.  Version output is one short line, so a
#: slow answer means the binary is wedged, not that it is thinking.
VERSION_TIMEOUT = 4.0
#: A login probe is local-only (no model call), so five seconds is generous.
AUTH_TIMEOUT = 5.0
#: Model enumeration is a local subcommand too, but it may start a Node runtime.
MODELS_TIMEOUT = 15.0
#: Detection results are reused for this long; ``refresh`` bypasses the cache.
DETECT_TTL = 30.0
#: Total wall-clock budget for ``agents.list``.
DETECT_BUDGET = 8.0

_VERSION_RE = re.compile(
    r"(?<![\d.])(\d+\.\d+(?:\.\d+)?(?:[-+][0-9A-Za-z.]+)?)"
)


@dataclass(frozen=True)
class ResolvedAgent:
    """A resolved executable, before any version/login probing."""

    defn: AgentDef
    installed: bool
    command: tuple[str, ...] | None
    path: str | None
    reason: str | None
    node: str | None = None

    @property
    def executable(self) -> str | None:
        return self.command[0] if self.command else None


@dataclass(frozen=True)
class AgentDetection:
    """What ``agents.list`` reports for one agent."""

    id: str
    name: str
    installed: bool
    path: str | None
    version: str | None
    auth: str
    reason: str | None
    install_url: str
    docs_url: str
    supported: bool
    command: tuple[str, ...] | None = None
    node: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "installed": self.installed,
            "path": self.path,
            "version": self.version,
            "auth": self.auth,
            "reason": self.reason,
            "install_url": self.install_url,
            "docs_url": self.docs_url,
            "supported": self.supported,
        }


_RESOLVE_CACHE: dict[tuple, tuple[float, ResolvedAgent]] = {}
_DETECT_CACHE: dict[tuple, tuple[float, AgentDetection]] = {}

#: Path overrides for the duration of an adapter construction.
#:
#: ``build_translator`` may be handed a config object that did not come from the
#: default location (CLI ``--config``, tests, MCP).  The adapter is constructed
#: synchronously, so a contextvar carries that config's ``agents.paths`` into
#: detection without threading an extra parameter through every adapter
#: constructor.
_CONFIGURED_PATHS_OVERRIDE: contextvars.ContextVar[
    Mapping[str, str] | None
] = contextvars.ContextVar("pdfxlate_agent_paths", default=None)


@contextlib.contextmanager
def use_configured_paths(paths: Mapping[str, str] | None) -> Iterator[None]:
    """Use ``paths`` for detection performed inside the ``with`` block."""
    token = _CONFIGURED_PATHS_OVERRIDE.set(dict(paths or {}))
    try:
        yield
    finally:
        _CONFIGURED_PATHS_OVERRIDE.reset(token)


def _cache_key(defn: AgentDef, paths: Mapping[str, str] | None) -> tuple:
    # The path overrides are part of the key: editing ``agents.paths`` must take
    # effect immediately instead of waiting out the 30-second TTL.
    return (defn.id, tuple(sorted((paths or {}).items())))


def configured_paths() -> dict[str, str]:
    """The ``agents.paths`` overrides, or an empty mapping if unavailable.

    Loaded lazily to keep this module importable without a config file (tests,
    and the settings panel before the engine has started).  A surrounding
    :func:`use_configured_paths` wins, so an adapter built from a non-default
    config still sees that config's overrides.
    """
    override = _CONFIGURED_PATHS_OVERRIDE.get()
    if override is not None:
        return {str(key): str(value) for key, value in override.items() if value}
    try:
        from pdfxlate import config as config_mod

        cfg = config_mod.load()
    except Exception:  # noqa: BLE001 - discovery must never be fatal
        logger.debug("could not read agents.paths from config", exc_info=True)
        return {}
    agents = getattr(cfg, "agents", None)
    if agents is None:
        return {}
    return {str(key): str(value) for key, value in agents.paths.items() if value}


def _spawn_kwargs() -> dict[str, Any]:
    """Hide console windows for short probes on Windows."""
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if flags:
            return {"creationflags": flags}
    return {}


def _run_probe(
    command: tuple[str, ...] | list[str],
    args: list[str],
    timeout: float,
) -> tuple[int | None, str, str]:
    """Run one short probe, returning ``(rc, stdout, stderr)``.

    Never raises: an ``OSError`` (missing binary) and a timeout both come back as
    ``rc is None``.  The caller must not log the returned text.
    """
    try:
        completed = subprocess.run(
            [*command, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            **_spawn_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return None, "", ""
    return completed.returncode, completed.stdout, completed.stderr


def parse_version(text: str) -> str | None:
    """First semantic-ish version in a ``--version`` line."""
    if not text:
        return None
    match = _VERSION_RE.search(text)
    return match.group(1) if match else None


def parse_claude_auth(payload: str) -> str:
    """Map ``claude auth status`` JSON to ``ok`` / ``missing`` / ``unknown``.

    Only the boolean is read; the rest of the payload (auth method, provider) is
    not needed and is discarded.
    """
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return "unknown"
    if not isinstance(data, dict):
        return "unknown"
    logged_in = data.get("loggedIn")
    if logged_in is True:
        return "ok"
    if logged_in is False:
        return "missing"
    return "unknown"


def parse_codex_auth(text: str, returncode: int | None) -> str:
    """Map ``codex login status`` text to a status.

    The CLI prints its state (and a masked key) on **stderr**, so the caller has
    to pass both streams concatenated.  A non-zero exit with no recognisable
    text is treated as "not logged in" because that is the only failure this
    command has.
    """
    lowered = (text or "").lower()
    if "not logged in" in lowered or "logged out" in lowered:
        return "missing"
    if "logged in" in lowered:
        return "ok"
    if returncode not in (None, 0):
        return "missing"
    return "unknown"


def parse_codebuddy_auth(text: str, returncode: int | None) -> str:
    """WorkBuddy's auth probe is best-effort; unknown is a valid answer."""
    lowered = (text or "").lower()
    if "not logged in" in lowered or "logged out" in lowered:
        return "missing"
    if "logged in" in lowered:
        return "ok"
    return "unknown"


def derive_install_dir(display_icon: str | None) -> Path | None:
    """Turn a Windows ``DisplayIcon`` value into the install directory.

    The registry frequently omits ``InstallLocation`` but still records
    ``D:\\ZCode\\uninstallerIcon.ico`` or ``D:\\WorkBuddy\\WorkBuddy.exe,0``.
    Only the parent directory is useful; a bare directory is returned as-is.
    """
    if not display_icon:
        return None
    raw = display_icon.strip().strip('"')
    if not raw:
        return None
    # Strip a trailing ",0" icon index (but never a drive letter).
    if "," in raw:
        raw = raw.rsplit(",", 1)[0]
    raw = raw.strip().strip('"')
    if not raw:
        return None
    path = Path(os.path.expandvars(raw)).expanduser()
    if path.suffix:
        return path.parent
    return path


def _windows_uninstall_entries() -> list[dict[str, str]]:
    """Read the uninstall registry (read-only, silently on failure)."""
    if sys.platform != "win32":
        return []
    try:
        import winreg
    except ImportError:  # pragma: no cover - only possible on a broken Windows
        return []

    roots = (
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        ),
    )
    entries: list[dict[str, str]] = []
    for root, subkey in roots:
        try:
            key = winreg.OpenKey(root, subkey)
        except OSError:
            continue
        try:
            index = 0
            while True:
                try:
                    name = winreg.EnumKey(key, index)
                except OSError:
                    break
                index += 1
                try:
                    child = winreg.OpenKey(key, name)
                except OSError:
                    continue
                try:
                    entry: dict[str, str] = {}
                    for field, value_name in (
                        ("name", "DisplayName"),
                        ("install_location", "InstallLocation"),
                        ("display_icon", "DisplayIcon"),
                    ):
                        try:
                            value, _kind = winreg.QueryValueEx(child, value_name)
                        except OSError:
                            continue
                        if isinstance(value, str) and value.strip():
                            entry[field] = value.strip()
                    if entry.get("name"):
                        entries.append(entry)
                finally:
                    try:
                        winreg.CloseKey(child)
                    except OSError:
                        pass
        finally:
            try:
                winreg.CloseKey(key)
            except OSError:
                pass
    return entries


def _standard_bases() -> list[Path]:
    if sys.platform == "win32":
        bases = [
            Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
            / "Programs",
            Path(os.environ.get("PROGRAMFILES") or r"C:\Program Files"),
            Path(os.environ.get("PROGRAMFILES(X86)") or r"C:\Program Files (x86)"),
        ]
    elif sys.platform == "darwin":
        bases = [Path("/Applications"), Path.home() / "Applications"]
    else:
        bases = [
            Path.home() / ".local" / "share",
            Path("/opt"),
            Path("/usr/local/lib"),
        ]
    return [base for base in bases if base]


def _install_dirs(defn: AgentDef) -> list[Path]:
    """Candidate install directories from the registry and standard roots."""
    dirs: list[Path] = []
    if defn.app_names:
        wanted = [name.lower() for name in defn.app_names]
        for entry in _windows_uninstall_entries():
            if not any(name in entry.get("name", "").lower() for name in wanted):
                continue
            location = entry.get("install_location")
            candidate = Path(location) if location else derive_install_dir(
                entry.get("display_icon")
            )
            if candidate:
                dirs.append(candidate)
        for base in _standard_bases():
            for name in defn.app_names:
                dirs.append(base / name)
    # De-duplicate while keeping order.
    seen: set[str] = set()
    unique: list[Path] = []
    for directory in dirs:
        key = str(directory).lower()
        if key not in seen:
            seen.add(key)
            unique.append(directory)
    return unique


def _find_script(defn: AgentDef) -> Path | None:
    """Locate the agent's script/binary inside a standard install directory."""
    relative = defn.script_relative or (
        defn.standard_relative[0] if defn.standard_relative else None
    )
    if relative is None:
        return None
    for directory in _install_dirs(defn):
        candidate = directory / relative
        if candidate.is_file():
            return candidate
    return None


def _find_desktop_only(defn: AgentDef) -> Path | None:
    """An installed desktop app whose bundled CLI is known not to work."""
    if not defn.app_names:
        return None
    for directory in _install_dirs(defn):
        if directory.is_dir():
            return directory
    return None


def _which(name: str) -> str | None:
    if not name:
        return None
    try:
        return shutil.which(name)
    except OSError:  # pragma: no cover - defensive
        return None


def _resolve_explicit(defn: AgentDef, raw: str) -> ResolvedAgent:
    """Resolve a user-supplied path (config override or environment variable)."""
    path = Path(os.path.expandvars(str(raw))).expanduser()
    if defn.id == "zcode":
        if path.is_dir():
            path = path / (defn.script_relative or "resources/glm/zcode.cjs")
        node = _which("node")
        if node is None:
            return ResolvedAgent(
                defn=defn,
                installed=False,
                command=None,
                path=str(path),
                reason="node_missing",
            )
        if not path.is_file():
            return ResolvedAgent(
                defn=defn,
                installed=False,
                command=None,
                path=str(path),
                reason="invalid_path",
            )
        return ResolvedAgent(
            defn=defn,
            installed=True,
            command=(node, str(path)),
            path=str(path),
            reason=None,
            node=node,
        )

    if path.is_dir():
        # Be forgiving: a user may paste the install directory instead of the
        # executable.  Prefer the expected binary name, then any platform shim.
        candidates = [path / defn.bin]
        if sys.platform == "win32":
            candidates += [path / f"{defn.bin}.cmd", path / f"{defn.bin}.exe"]
        candidates += [path / name for name in defn.fallback_bins]
        for candidate in candidates:
            if candidate.is_file():
                path = candidate
                break
        else:
            return ResolvedAgent(
                defn=defn,
                installed=False,
                command=None,
                path=str(path),
                reason="invalid_path",
            )

    if not path.is_file():
        return ResolvedAgent(
            defn=defn,
            installed=False,
            command=None,
            path=str(path),
            reason="invalid_path",
        )
    if defn.id == "workbuddy" and path.name.lower() in {"workbuddy.exe", "workbuddy"}:
        # The desktop app's own binary is not the CodeBuddy CLI; running it would
        # do nothing useful and might open a GUI.
        return ResolvedAgent(
            defn=defn,
            installed=False,
            command=None,
            path=str(path),
            reason="app_only",
        )
    return ResolvedAgent(
        defn=defn,
        installed=True,
        command=(str(path),),
        path=str(path),
        reason=None,
    )


def _resolve_uncached(defn: AgentDef, paths: Mapping[str, str] | None) -> ResolvedAgent:
    override = (paths or {}).get(defn.id)
    if override:
        return _resolve_explicit(defn, override)

    env_value = os.environ.get(defn.env_var) if defn.env_var else None
    if env_value:
        return _resolve_explicit(defn, env_value)

    if defn.id == "zcode":
        node = _which("node")
        script = _find_script(defn)
        if script is None:
            # Some installs put a launcher on PATH; accept it when it exists and
            # is something Node can actually execute (not a .cmd shim).
            launcher = _which("zcode")
            if (
                launcher
                and node
                and Path(launcher).suffix.lower() in {".js", ".cjs", ".mjs"}
            ):
                return ResolvedAgent(
                    defn=defn,
                    installed=True,
                    command=(node, launcher),
                    path=launcher,
                    reason=None,
                    node=node,
                )
            return ResolvedAgent(
                defn=defn,
                installed=False,
                command=None,
                path=None,
                reason="not_installed",
            )
        if node is None:
            return ResolvedAgent(
                defn=defn,
                installed=False,
                command=None,
                path=str(script),
                reason="node_missing",
            )
        return ResolvedAgent(
            defn=defn,
            installed=True,
            command=(node, str(script)),
            path=str(script),
            reason=None,
            node=node,
        )

    for name in (defn.bin, *defn.fallback_bins):
        found = _which(name)
        if found:
            return ResolvedAgent(
                defn=defn,
                installed=True,
                command=(found,),
                path=found,
                reason=None,
            )

    # ZCode and DSH are not usually on PATH; the registry/standard directory is
    # the normal way to find them.  The same lookup gives WorkBuddy its
    # "desktop app installed, CLI missing" answer.
    script = _find_script(defn)
    if script is not None:
        return ResolvedAgent(
            defn=defn,
            installed=True,
            command=(str(script),),
            path=str(script),
            reason=None,
        )

    desktop = _find_desktop_only(defn)
    if desktop is not None and defn.id == "workbuddy":
        return ResolvedAgent(
            defn=defn,
            installed=False,
            command=None,
            path=str(desktop),
            reason="app_only",
        )
    return ResolvedAgent(
        defn=defn,
        installed=False,
        command=None,
        path=None,
        reason="not_installed",
    )


def resolve_agent(
    defn: AgentDef,
    paths: Mapping[str, str] | None = None,
    refresh: bool = False,
) -> ResolvedAgent:
    """Resolve the executable for one agent (cached for :data:`DETECT_TTL`)."""
    key = _cache_key(defn, paths)
    now = time.monotonic()
    if not refresh:
        cached = _RESOLVE_CACHE.get(key)
        if cached and now - cached[0] < DETECT_TTL:
            return cached[1]
    resolved = _resolve_uncached(defn, paths)
    _RESOLVE_CACHE[key] = (now, resolved)
    return resolved


def _probe_version(command: tuple[str, ...]) -> str | None:
    returncode, stdout, stderr = _run_probe(command, ["--version"], VERSION_TIMEOUT)
    if returncode is None:
        return None
    return parse_version(f"{stdout}\n{stderr}")


def check_auth(defn: AgentDef, command: tuple[str, ...] | None) -> str:
    """Probe login state, returning only ``ok`` / ``missing`` / ``unknown``."""
    if not command or defn.login_probe == "none":
        return "unknown"

    if defn.login_probe == "zcode_doctor":
        # ZCode has no machine-readable login state; doctor only proves the
        # runtime can start.  The brief deliberately reports "unknown" here.
        _run_probe(command, ["doctor"], AUTH_TIMEOUT)
        return "unknown"

    if defn.login_probe == "claude_json":
        returncode, stdout, stderr = _run_probe(command, ["auth", "status"], AUTH_TIMEOUT)
        if returncode is None:
            return "unknown"
        status = parse_claude_auth(stdout) if stdout.strip() else "unknown"
        if status == "unknown":
            status = parse_claude_auth(stderr)
        return status

    if defn.login_probe == "codex_text":
        returncode, stdout, stderr = _run_probe(command, ["login", "status"], AUTH_TIMEOUT)
        if returncode is None:
            return "unknown"
        return parse_codex_auth(f"{stdout}\n{stderr}", returncode)

    if defn.login_probe == "codebuddy_text":
        returncode, stdout, stderr = _run_probe(command, ["auth", "status"], AUTH_TIMEOUT)
        if returncode is None:
            return "unknown"
        return parse_codebuddy_auth(f"{stdout}\n{stderr}", returncode)

    return "unknown"


def blank_detection(defn: AgentDef, reason: str | None = "not_installed") -> AgentDetection:
    return AgentDetection(
        id=defn.id,
        name=defn.name,
        installed=False,
        path=None,
        version=None,
        auth="unknown",
        reason=reason,
        install_url=defn.install_url,
        docs_url=defn.docs_url,
        supported=defn.supported,
    )


def provisional_detection(
    defn: AgentDef, paths: Mapping[str, str] | None = None
) -> AgentDetection:
    """A detection for a probe that ran out of time.

    Resolving the executable is cheap (PATH/registry lookups only), so this can
    still say "installed" — it just cannot report a version or login state.
    Reporting an installed agent as missing because its *probe* was slow would
    be actively misleading.
    """
    resolved = resolve_agent(defn, paths)
    if not resolved.installed:
        return blank_detection(defn, resolved.reason or "not_installed")
    return AgentDetection(
        id=defn.id,
        name=defn.name,
        installed=True,
        path=resolved.path,
        version=None,
        auth="unknown",
        reason=None,
        install_url=defn.install_url,
        docs_url=defn.docs_url,
        supported=defn.supported,
        command=resolved.command,
        node=resolved.node,
    )


def detect_agent(
    defn: AgentDef,
    paths: Mapping[str, str] | None = None,
    refresh: bool = False,
) -> AgentDetection:
    """Resolve and version-probe one agent (cached).

    Login probing is deliberately *not* part of detection.  The settings panel
    shows installed agents and the user verifies a model manually with the
    Test button; probing every CLI's login state on launch made opening the
    panel look like an automatic test and left DSH/ZCode stuck on
    "unverified".  ``check_auth()`` stays available for explicit callers.
    """
    key = _cache_key(defn, paths)
    now = time.monotonic()
    if not refresh:
        cached = _DETECT_CACHE.get(key)
        if cached and now - cached[0] < DETECT_TTL:
            return cached[1]

    resolved = resolve_agent(defn, paths, refresh=refresh)
    if not resolved.installed:
        detection = AgentDetection(
            id=defn.id,
            name=defn.name,
            installed=False,
            path=resolved.path,
            version=None,
            auth="unknown",
            reason=resolved.reason or "not_installed",
            install_url=defn.install_url,
            docs_url=defn.docs_url,
            supported=defn.supported and resolved.reason != "app_only",
            command=None,
            node=resolved.node,
        )
    else:
        detection = AgentDetection(
            id=defn.id,
            name=defn.name,
            installed=True,
            path=resolved.path,
            version=_probe_version(resolved.command or ()),
            auth="unknown",
            reason=None,
            install_url=defn.install_url,
            docs_url=defn.docs_url,
            supported=defn.supported,
            command=resolved.command,
            node=resolved.node,
        )
    _DETECT_CACHE[key] = (now, detection)
    return detection


def _stale_detection(defn: AgentDef, paths: Mapping[str, str] | None) -> AgentDetection | None:
    cached = _DETECT_CACHE.get(_cache_key(defn, paths))
    return cached[1] if cached else None


def stale_detection(
    defn: AgentDef, paths: Mapping[str, str] | None = None
) -> AgentDetection | None:
    """The last cached detection, even if its TTL has expired."""
    return _stale_detection(defn, paths)


def detect_all(
    paths: Mapping[str, str] | None = None,
    refresh: bool = False,
    budget: float = DETECT_BUDGET,
) -> list[AgentDetection]:
    """Detect every agent in parallel, returning within ``budget`` seconds.

    A probe that misses the budget degrades to the last cached result, or to a
    plain "not installed" entry, so the settings panel always renders.  The
    worker threads are bounded by their own subprocess timeouts.
    """
    paths = configured_paths() if paths is None else paths
    pool = ThreadPoolExecutor(max_workers=len(AGENT_DEFS), thread_name_prefix="agent-detect")
    futures = {
        defn.id: pool.submit(detect_agent, defn, paths, refresh) for defn in AGENT_DEFS
    }
    done, _pending = wait(list(futures.values()), timeout=budget)
    results: list[AgentDetection] = []
    for defn in AGENT_DEFS:
        future = futures[defn.id]
        if future in done:
            try:
                results.append(future.result())
                continue
            except Exception:  # noqa: BLE001 - detection must not break the list
                logger.debug("agent detection failed for %s", defn.id, exc_info=True)
        else:
            future.cancel()
        results.append(
            stale_detection(defn, paths) or provisional_detection(defn, paths)
        )
    pool.shutdown(wait=False, cancel_futures=True)
    return results


__all__ = [
    "AUTH_TIMEOUT",
    "AgentDetection",
    "DETECT_BUDGET",
    "DETECT_TTL",
    "MODELS_TIMEOUT",
    "ResolvedAgent",
    "VERSION_TIMEOUT",
    "blank_detection",
    "check_auth",
    "configured_paths",
    "derive_install_dir",
    "detect_agent",
    "detect_all",
    "get_agent_def",
    "parse_claude_auth",
    "parse_codebuddy_auth",
    "parse_codex_auth",
    "parse_version",
    "provisional_detection",
    "resolve_agent",
    "stale_detection",
    "use_configured_paths",
]
