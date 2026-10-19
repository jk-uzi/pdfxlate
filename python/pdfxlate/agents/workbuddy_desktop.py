"""Drive the signed-in WorkBuddy desktop app as a translation backend.

The desktop application is not a CLI: it has no ``-p`` mode and no way to pick a
model on a command line.  What it *does* have is a signed-in session, stored in
its own auth file, and the exact same HTTP gateway its chat panel talks to.  On
a machine with only the desktop app installed this module is therefore the
backend: it reads the app's at-rest-encrypted credential, asks the gateway for
the model catalog, and streams translations back over ``/v2/chat/completions``.

Security contract
-----------------
* The access token, refresh token, app secret and decrypted payload are held in
  memory and **never** written to the config, a cache, a fixture or a log.
* The auth file and ``~/.codebuddy`` are only ever read.  A refreshed token is
  cached in this process; it is not written back to the app's own file.
* Error messages name the failure, never the credential.  The at-rest key probe
  launches the installed app as plain Node and reads only its stdout.
* No agent user id, nickname or token leaves this module except through the
  network request that needs it; the sidecar only sees ``available`` / ``auth``.

The HTTP shapes are the ones the desktop client uses (and the ones a working
read-only spike on a real machine confirmed):

* catalog: ``GET /v3/config`` on ``copilot.tencent.com`` for a CN login, falling
  back to ``/v2/enterprises/personal/models``; the CLI roster is
  ``agents[name="cli"].models`` intersected with ``models``;
* chat: ``POST /v2/chat/completions`` with ``stream: true`` and a leading
  ``system`` message (the gateway rejects a user-first conversation);
* refresh: ``POST /v2/plugin/auth/token/refresh`` with ``X-Refresh-Token`` and
  ``X-Auth-Refresh-Source: workbuddy``.  The token in the response replaces the
  in-memory copy only.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import struct
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Sequence

import requests

from pdfxlate.agents import detect

logger = logging.getLogger(__name__)

#: Explicit override for the desktop executable (same name the reference uses).
APP_EXECUTABLE_ENV = "WORKBUDDY_APP_EXECUTABLE"
#: Explicit override for the desktop auth file.
AUTH_FILE_ENV = "WORKBUDDY_AUTH_FILE"
#: Optional proxy for networks where the gateway is only reachable through one.
PROXY_ENV = "WORKBUDDY_PROXY"

#: The live auth file written by the desktop app.
LIVE_AUTH_FILENAME = "workbuddy-desktop.info"
#: Both Windows builds are legitimately named differently.
APP_EXECUTABLE_NAMES = ("WorkBuddy.exe", "WorkBuddyAI.exe")

#: How long the app may take to answer the at-rest-key probe.
KEY_TIMEOUT = 15.0
#: Catalog and refresh calls are metadata calls: they should be quick.
METADATA_TIMEOUT = 20.0
#: A chat completion is a real model call and may prefill for a long time.
CHAT_TIMEOUT = 300.0
#: Token freshness is checked slightly ahead of the wire to avoid an expiry
#: landing between the check and the request.
EXPIRY_SKEW_MS = 60_000

CN_CHAT_BASE = "https://copilot.tencent.com"
CN_BILLING_BASE = "https://www.codebuddy.cn"
GLOBAL_BASE = "https://www.workbuddy.ai"
GLOBAL_CODEBUDDY_BASE = "https://www.codebuddy.ai"
#: The CLI channel's user agent, used for the CN catalog (desktop parity).
CLI_USER_AGENT = "CLI/2.63.2 CodeBuddy/2.63.2"
#: The desktop channel's user agent, used for the international catalog.
DESKTOP_USER_AGENT = "WorkBuddy/5.5.2"

#: Used only when the caller supplies no system message.  The gateway requires
#: the conversation to open with one, and a minimal placeholder is better than
#: a guaranteed 400.
SYSTEM_PROMPT = "You are a helpful assistant that translates documents faithfully."

#: The app's own public binding for the build-time key.  The payload is JSON
#: containing ``atRestSecretKey``; it is asked from the installed build rather
#: than copied here, so a build-specific constant never enters this repo.
_KEY_PROBE_SOURCE = (
    "try{process.stdout.write(process._linkedBinding("
    "'electron_browser_workbuddy_storage').loggerGet())}"
    "catch(e){process.exitCode=3;process.stderr.write(String(e&&e.message||e))}"
)

_AAD_DOMAIN = b"WB-AAD\0"
_AAD_FORMAT_ID = "WBEV1"
_AAD_SCHEME = "sym-v1"
_FIELD_FRAMING = 2

_ERROR_BODY_LIMIT = 300
_ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")

#: Markers the upstream uses for an account-level exhausted quota.
_CREDIT_MARKERS = (
    "insufficient credit",
    "no credit",
    "credit exhausted",
    "credits exhausted",
    "out of credit",
    "quota exceeded",
    "payment required",
    "积分不足",
    "额度不足",
    "余额不足",
    "积分用完",
    "额度用尽",
)
_AUTH_MARKERS = (
    "unauthorized",
    "unauthenticated",
    "not logged in",
    "logged out",
    "session not found",
    "invalid token",
    "token expired",
    "offline user session",
    "12153",
    "14018",
)


# --- errors ----------------------------------------------------------------


class WorkBuddyError(RuntimeError):
    """A failure while using the WorkBuddy desktop session.

    ``kind`` is what the adapter uses to choose the matching pipeline error:

    ``app_missing``  the desktop executable could not be found;
    ``key_unavailable`` the app is installed but its credential key could not
    be read / the field could not be decrypted;
    ``not_logged_in`` the auth file is absent or carries no access token;
    ``auth``          the gateway rejected the credential;
    ``rate_limit``    the account or model is throttled / out of credits;
    ``transport``     the request failed or returned an unexpected document;
    ``output``        the answer could not be parsed as translations.
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


# --- small utilities -------------------------------------------------------


def _truncate(text: Any, limit: int = _ERROR_BODY_LIMIT) -> str:
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    rendered = _ANSI_RE.sub("", str(text or "")).strip()
    return rendered if len(rendered) <= limit else rendered[:limit] + "…"


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _clean_text(value: Any) -> str | None:
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return None


def _spawn_kwargs() -> dict[str, Any]:
    """Hide a console window for the short key probe on Windows."""
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if flags:
            return {"creationflags": flags}
    return {}


# --- app / auth discovery --------------------------------------------------


def _macos_bundle_executable(bundle: Path) -> Path | None:
    """The binary named by a bundle's own ``Info.plist``."""
    try:
        plist = (bundle / "Contents" / "Info.plist").read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return None
    match = re.search(
        r"<key>\s*CFBundleExecutable\s*</key>\s*<string>([^<]*)</string>",
        plist,
    )
    name = (match.group(1) if match else "").strip()
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        return None
    candidate = bundle / "Contents" / "MacOS" / name
    return candidate if candidate.is_file() else None


def _candidate_app_paths() -> list[Path]:
    """Every place the desktop executable is known to be installed."""
    candidates: list[Path] = []
    if sys.platform == "win32":
        bases: list[Path] = []
        for variable, fallback in (
            ("LOCALAPPDATA", Path.home() / "AppData" / "Local"),
            ("APPDATA", Path.home() / "AppData" / "Roaming"),
            ("ProgramFiles", Path(r"C:\Program Files")),
            ("ProgramFiles(x86)", Path(r"C:\Program Files (x86)")),
        ):
            raw = os.environ.get(variable)
            bases.append(Path(raw) if raw else fallback)
        subdirectories = (
            ("Programs", "WorkBuddy"),
            ("WorkBuddy",),
            ("Programs", "WorkBuddyAI"),
            ("WorkBuddyAI",),
        )
        for base in bases:
            for parts in subdirectories:
                directory = base.joinpath(*parts)
                for name in APP_EXECUTABLE_NAMES:
                    candidates.append(directory / name)

        # The installer records where it actually put the binary, which is the
        # only way to find an app installed outside the default layout.
        try:
            entries = detect._windows_uninstall_entries()
        except Exception:  # noqa: BLE001 - discovery must never be fatal
            entries = []
        for entry in entries:
            name = str(entry.get("name") or "").lower()
            if "workbuddy" not in name and "codebuddy" not in name:
                continue
            icon = str(entry.get("display_icon") or "").strip().strip('"')
            if icon:
                if "," in icon:
                    icon = icon.rsplit(",", 1)[0]
                executable = Path(os.path.expandvars(icon.strip().strip('"')))
                if executable.suffix and executable.name.lower() in {
                    name.lower() for name in APP_EXECUTABLE_NAMES
                }:
                    candidates.append(executable)
                elif executable.is_dir():
                    for exe_name in APP_EXECUTABLE_NAMES:
                        candidates.append(executable / exe_name)
            location = str(entry.get("install_location") or "").strip()
            if location:
                directory = Path(os.path.expandvars(location))
                for exe_name in APP_EXECUTABLE_NAMES:
                    candidates.append(directory / exe_name)
    elif sys.platform == "darwin":
        for root in (Path("/Applications"), Path.home() / "Applications"):
            for bundle_name in ("WorkBuddy.app", "WorkBuddy AI.app"):
                executable = _macos_bundle_executable(root / bundle_name)
                if executable is not None:
                    candidates.append(executable)
    return candidates


def workbuddy_app_path() -> Path | None:
    """The installed desktop executable, or ``None``.

    ``WORKBUDDY_APP_EXECUTABLE`` wins even when it points at a path that does
    not exist: silently falling through to another install would make a typo
    look like "the app is missing" instead of naming the bad override.
    """
    override = (os.environ.get(APP_EXECUTABLE_ENV) or "").strip()
    if override:
        path = Path(os.path.expandvars(override)).expanduser()
        return path if path.is_file() else None
    for candidate in _candidate_app_paths():
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def _auth_directories() -> list[Path]:
    if sys.platform == "win32":
        bases: list[Path] = []
        for variable, fallback in (
            ("LOCALAPPDATA", Path.home() / "AppData" / "Local"),
            ("APPDATA", Path.home() / "AppData" / "Roaming"),
        ):
            raw = os.environ.get(variable)
            bases.append(Path(raw) if raw else fallback)
        return [
            base / "CodeBuddyExtension" / "Data" / "Public" / "auth"
            for base in bases
        ]
    if sys.platform == "darwin":
        return [
            Path.home()
            / "Library"
            / "Application Support"
            / "CodeBuddyExtension"
            / "Data"
            / "Public"
            / "auth"
        ]
    raw = os.environ.get("XDG_CONFIG_HOME")
    base = Path(raw) if raw else Path.home() / ".config"
    return [base / "CodeBuddyExtension" / "Data" / "Public" / "auth"]


def _backups_beside(path: Path) -> list[Path]:
    """Timestamped ``*.info`` siblings, newest first by filename."""
    try:
        entries = sorted(
            (
                entry
                for entry in path.parent.iterdir()
                if entry.name != path.name
                and entry.name.endswith(".info")
                and entry.is_file()
            ),
            key=lambda entry: entry.name,
            reverse=True,
        )
    except OSError:
        return []
    return entries


def workbuddy_auth_candidates() -> list[Path]:
    """Every auth file to try: the live file first, then newest backups."""
    override = (os.environ.get(AUTH_FILE_ENV) or "").strip()
    if override:
        path = Path(os.path.expandvars(override)).expanduser()
        return [path, *_backups_beside(path)]
    candidates: list[Path] = []
    for directory in _auth_directories():
        live = directory / LIVE_AUTH_FILENAME
        candidates.append(live)
        candidates.extend(_backups_beside(live))
    return candidates


def workbuddy_auth_path() -> Path | None:
    """The first readable auth file on this machine."""
    for candidate in workbuddy_auth_candidates():
        try:
            if candidate.is_file():
                with candidate.open("rb") as stream:
                    stream.read(1)
                return candidate
        except OSError:
            continue
    return None


# --- at-rest key / field decryption ---------------------------------------


@dataclass(frozen=True)
class AtRestKey:
    """The derived 32-byte field key and the envelope's key id."""

    key: bytes = field(repr=False)
    key_id: str = ""


def derive_key_id(key: bytes) -> str:
    """The first 16 hex characters of the key's SHA-256 (the envelope id)."""
    return hashlib.sha256(key).hexdigest()[:16]


def derive_at_rest_key(payload_text: str) -> AtRestKey:
    """Derive the field key from the app's key payload JSON.

    The app hashes the payload's ``atRestSecretKey`` **string**, not its decoded
    bytes; doing anything else produces a key every envelope rejects.
    """
    try:
        payload = json.loads(payload_text)
    except (TypeError, ValueError):
        raise WorkBuddyError(
            "key_unavailable", "the WorkBuddy app returned an unreadable key payload"
        ) from None
    if not isinstance(payload, dict):
        raise WorkBuddyError(
            "key_unavailable", "the WorkBuddy app returned an unexpected key payload"
        )
    secret = payload.get("atRestSecretKey")
    if not isinstance(secret, str) or not secret:
        raise WorkBuddyError(
            "key_unavailable", "the WorkBuddy app returned no credential key"
        )
    key = hashlib.sha256(secret.encode("utf-8")).digest()
    return AtRestKey(key=key, key_id=derive_key_id(key))


_KEY_CACHE_LOCK = threading.Lock()
_KEY_CACHE: dict[str, AtRestKey] = {}


def read_at_rest_key(app_path: Path | str | None = None) -> AtRestKey:
    """Ask the installed app for its at-rest field key (cached per process).

    The app is launched as plain Node (``ELECTRON_RUN_AS_NODE=1``), so no window
    opens and no GUI state is touched.  The payload is parsed and discarded; only
    the derived key stays in memory.
    """
    executable = Path(app_path) if app_path is not None else workbuddy_app_path()
    if executable is None:
        raise WorkBuddyError(
            "app_missing",
            "the WorkBuddy desktop app was not found; set "
            f"{APP_EXECUTABLE_ENV} or install WorkBuddy",
        )
    cache_key = str(executable)
    with _KEY_CACHE_LOCK:
        cached = _KEY_CACHE.get(cache_key)
        if cached is not None:
            return cached

    env = dict(os.environ)
    env["ELECTRON_RUN_AS_NODE"] = "1"
    try:
        completed = subprocess.run(
            [str(executable), "-e", _KEY_PROBE_SOURCE],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=KEY_TIMEOUT,
            **_spawn_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise WorkBuddyError(
            "key_unavailable",
            f"the WorkBuddy desktop app did not provide its credential key "
            f"({type(exc).__name__})",
        ) from exc

    payload = (completed.stdout or "").strip()
    if completed.returncode != 0 or not payload:
        raise WorkBuddyError(
            "key_unavailable",
            "the WorkBuddy desktop app did not provide its credential key "
            f"(exit code {completed.returncode})",
        )
    key = derive_at_rest_key(payload)
    with _KEY_CACHE_LOCK:
        _KEY_CACHE[cache_key] = key
    return key


def _is_encrypted_field(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"$wbEncrypted", "envelope"}
        and value.get("$wbEncrypted") == 1
        and isinstance(value.get("envelope"), str)
    )


def _length_prefixed(value: str) -> bytes:
    raw = value.encode("utf-8")
    return struct.pack(">I", len(raw)) + raw


def field_aad(suite: int, key_id: str) -> bytes:
    """The authenticated additional data for one field-framed envelope."""
    return (
        _AAD_DOMAIN
        + b"\x01"
        + _length_prefixed(_AAD_FORMAT_ID)
        + _length_prefixed(_AAD_SCHEME)
        + struct.pack(">I", suite)
        + _length_prefixed(key_id)
        + bytes([_FIELD_FRAMING])
        + b"\x00"
        + b"\x00"
    )


def open_field(value: Any, at_rest: AtRestKey | None) -> str:
    """Open one credential field, tolerating the plain-string (older) shape."""
    if isinstance(value, str):
        return value
    if not _is_encrypted_field(value):
        raise WorkBuddyError(
            "key_unavailable", "the stored WorkBuddy credential has an unknown shape"
        )
    if at_rest is None:
        raise WorkBuddyError(
            "key_unavailable",
            "the WorkBuddy credential is encrypted but its key is unavailable",
        )
    try:
        envelope = json.loads(base64.b64decode(str(value["envelope"])))
    except (ValueError, TypeError):
        raise WorkBuddyError(
            "key_unavailable", "the stored WorkBuddy credential envelope is malformed"
        ) from None
    if not isinstance(envelope, dict):
        raise WorkBuddyError(
            "key_unavailable", "the stored WorkBuddy credential envelope is malformed"
        )
    suite = envelope.get("suite")
    key_id = envelope.get("keyId")
    nonce = envelope.get("nonce")
    auth_tag = envelope.get("authTag")
    ciphertext = envelope.get("ciphertext")
    if (
        not isinstance(suite, int)
        or not isinstance(key_id, str)
        or not isinstance(nonce, str)
        or not isinstance(auth_tag, str)
        or not isinstance(ciphertext, str)
    ):
        raise WorkBuddyError(
            "key_unavailable", "the stored WorkBuddy credential envelope is incomplete"
        )
    if key_id != at_rest.key_id:
        raise WorkBuddyError(
            "key_unavailable",
            "the stored WorkBuddy credential belongs to a different app install",
        )
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise WorkBuddyError(
            "key_unavailable",
            "reading the WorkBuddy desktop credential needs the 'cryptography' package",
        ) from None
    try:
        plain = AESGCM(at_rest.key).decrypt(
            base64.b64decode(nonce),
            base64.b64decode(ciphertext) + base64.b64decode(auth_tag),
            field_aad(suite, key_id),
        )
    except Exception:  # noqa: BLE001 - InvalidTag and malformed base64 alike
        raise WorkBuddyError(
            "key_unavailable",
            "the stored WorkBuddy credential could not be decrypted; sign in again "
            "in the WorkBuddy app",
        ) from None
    try:
        return plain.decode("utf-8")
    except UnicodeDecodeError:
        raise WorkBuddyError(
            "key_unavailable", "the stored WorkBuddy credential is not valid UTF-8"
        ) from None


def has_encrypted_fields(text: str) -> bool:
    """Whether reading this document needs the desktop app's key."""
    try:
        document = json.loads(text)
    except (TypeError, ValueError):
        return False
    if not isinstance(document, dict):
        return False
    auth = document.get("auth")
    if not isinstance(auth, dict):
        auth = document
    identity = document.get("account")
    if not isinstance(identity, dict):
        identity = document
    return any(
        _is_encrypted_field(value)
        for value in (
            auth.get("accessToken"),
            auth.get("refreshToken"),
            auth.get("domain"),
            identity.get("uid"),
            identity.get("enterpriseId"),
            identity.get("nickname"),
            identity.get("uin"),
        )
    )


# --- credential ------------------------------------------------------------


@dataclass(frozen=True)
class WorkBuddyCredential:
    """One decrypted desktop login, held in memory only."""

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False, default="")
    expires_at_ms: int = 0
    refresh_expires_at_ms: int | None = None
    last_refresh_at_ms: int | None = None
    domain: str = ""
    uid: str = ""
    enterprise_id: str | None = None
    nickname: str | None = None
    uin: str | None = None
    file_path: str = ""

    @property
    def region(self) -> str:
        return region_of(self.domain)

    @property
    def expired(self) -> bool:
        return bool(self.expires_at_ms) and time.time() * 1000 >= self.expires_at_ms


def expiry_to_ms(value: Any) -> int:
    """Normalise an expiry that may arrive in seconds or milliseconds."""
    number = _as_int(value)
    if number is None or number <= 0:
        return 0
    return number if number > 1_000_000_000_000 else number * 1000


def _optional_field(value: Any, at_rest: AtRestKey | None) -> str | None:
    if isinstance(value, str):
        return value or None
    if not _is_encrypted_field(value):
        return None
    if at_rest is None:
        return None
    try:
        return open_field(value, at_rest)
    except WorkBuddyError:
        return None


def parse_auth(
    text: str,
    at_rest: AtRestKey | None = None,
    file_path: str = "",
) -> WorkBuddyCredential | None:
    """Parse an auth document in either the nested or the flat shape.

    Returns ``None`` when the document carries no access token (an empty or
    signed-out file).  A required field that is encrypted and cannot be opened
    raises :class:`WorkBuddyError`: that is "unreadable", not "signed out", and
    the two need different advice in the UI.
    """
    try:
        document = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    auth = document.get("auth")
    if not isinstance(auth, dict):
        auth = document
    identity = document.get("account")
    if not isinstance(identity, dict):
        identity = document

    raw_access = auth.get("accessToken")
    if raw_access is None or raw_access == "":
        return None
    access_token = open_field(raw_access, at_rest)
    if not access_token:
        return None
    refresh_token = ""
    if auth.get("refreshToken") is not None:
        refresh_token = _optional_field(auth.get("refreshToken"), at_rest) or ""
    domain = _optional_field(auth.get("domain"), at_rest) or ""
    uid = _optional_field(identity.get("uid"), at_rest) or ""
    enterprise_id = _optional_field(identity.get("enterpriseId"), at_rest)
    nickname = _optional_field(identity.get("nickname"), at_rest)
    uin = _optional_field(identity.get("uin"), at_rest)
    return WorkBuddyCredential(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at_ms=expiry_to_ms(auth.get("expiresAt")),
        refresh_expires_at_ms=expiry_to_ms(auth.get("refreshExpiresAt")) or None,
        last_refresh_at_ms=expiry_to_ms(auth.get("lastRefreshTime")) or None,
        domain=domain,
        uid=uid,
        enterprise_id=enterprise_id,
        nickname=nickname,
        uin=uin,
        file_path=file_path,
    )


_CREDENTIAL_LOCK = threading.RLock()
_CREDENTIAL_CACHE: WorkBuddyCredential | None = None
_CREDENTIAL_SIGNATURE: tuple[str, int, int] | None = None


def _auth_signature() -> tuple[str, int, int] | None:
    path = workbuddy_auth_path()
    if path is None:
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    return (str(path), stat.st_mtime_ns, stat.st_size)


def _load_from_disk() -> WorkBuddyCredential:
    app = workbuddy_app_path()
    if app is None:
        raise WorkBuddyError(
            "app_missing",
            "the WorkBuddy desktop app was not found; install WorkBuddy or set "
            f"{APP_EXECUTABLE_ENV}",
        )

    at_rest: AtRestKey | None = None
    key_error: WorkBuddyError | None = None
    for path in workbuddy_auth_candidates():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if has_encrypted_fields(text) and at_rest is None and key_error is None:
            try:
                at_rest = read_at_rest_key(app)
            except WorkBuddyError as exc:
                key_error = exc
        try:
            credential = parse_auth(text, at_rest, file_path=str(path))
        except WorkBuddyError as exc:
            # Keep the key probe's own message when it failed: "the key could
            # not be read" is more actionable than the generic "encrypted but
            # no key" a parse without one reports.
            if key_error is None:
                key_error = exc
            continue
        if credential is not None:
            return credential

    if key_error is not None:
        raise key_error
    raise WorkBuddyError(
        "not_logged_in",
        "the WorkBuddy desktop app is not signed in; open WorkBuddy and sign in, "
        "then click Rescan",
    )


def _store_credential(credential: WorkBuddyCredential) -> WorkBuddyCredential:
    global _CREDENTIAL_CACHE, _CREDENTIAL_SIGNATURE
    with _CREDENTIAL_LOCK:
        _CREDENTIAL_CACHE = credential
        _CREDENTIAL_SIGNATURE = _auth_signature()
    return credential


def workbuddy_credential(
    refresh: bool = False,
    allow_refresh: bool = True,
) -> WorkBuddyCredential:
    """The current desktop login, reading or refreshing it when necessary.

    ``refresh=True`` re-reads the auth file (the Rescan button says the user
    just signed in).  An expired access token is refreshed in memory; the auth
    file is never rewritten.
    """
    global _CREDENTIAL_CACHE
    with _CREDENTIAL_LOCK:
        cached = _CREDENTIAL_CACHE
        if (
            not refresh
            and cached is not None
            and _CREDENTIAL_SIGNATURE is not None
            and (_auth_signature() == _CREDENTIAL_SIGNATURE)
        ):
            credential = cached
        else:
            credential = _load_from_disk()
            _store_credential(credential)

    if (
        allow_refresh
        and credential.expires_at_ms
        and time.time() * 1000 >= credential.expires_at_ms - EXPIRY_SKEW_MS
    ):
        credential = refresh_credential(credential)
        _store_credential(credential)
    return credential


def clear_caches() -> None:
    """Drop the in-memory key/credential (tests and diagnostics only)."""
    global _CREDENTIAL_CACHE, _CREDENTIAL_SIGNATURE
    with _CREDENTIAL_LOCK:
        _CREDENTIAL_CACHE = None
        _CREDENTIAL_SIGNATURE = None
    with _KEY_CACHE_LOCK:
        _KEY_CACHE.clear()
    close_chat_sessions()


# --- region / HTTP plumbing ------------------------------------------------


def region_of(domain: str) -> str:
    """The region a login domain belongs to (unrecognised means CN)."""
    lowered = (domain or "").strip().lower()
    if lowered == "workbuddy.ai" or lowered.endswith(".workbuddy.ai"):
        return "global"
    if lowered == "codebuddy.ai" or lowered.endswith(".codebuddy.ai"):
        return "global"
    return "cn"


def global_base(domain: str) -> str:
    lowered = (domain or "").strip().lower()
    if lowered == "codebuddy.ai" or lowered.endswith(".codebuddy.ai"):
        return GLOBAL_CODEBUDDY_BASE
    return GLOBAL_BASE


def _chat_base(credential: WorkBuddyCredential) -> str:
    return global_base(credential.domain) if credential.region == "global" else CN_CHAT_BASE


def _origin(credential: WorkBuddyCredential) -> str:
    return global_base(credential.domain) if credential.region == "global" else CN_BILLING_BASE


def _request_error(exc: Exception) -> WorkBuddyError:
    if isinstance(exc, requests.exceptions.ProxyError):
        return WorkBuddyError(
            "transport",
            "could not reach the WorkBuddy service (the system proxy was "
            "unreachable; WorkBuddy connects directly, or set "
            f"{PROXY_ENV} to a working proxy)",
        )
    return WorkBuddyError(
        "transport",
        f"could not reach the WorkBuddy service ({type(exc).__name__})",
    )


def _http_session() -> requests.Session:
    """A session that ignores the OS proxy unless the user names one.

    ``requests`` falls back to the Windows registry proxy even when no
    ``*_PROXY`` environment variable is set.  A stale Clash/V2Ray entry there
    (the usual ``127.0.0.1:7890``) makes every WorkBuddy metadata and chat call
    fail with ``ProxyError`` while the desktop app itself reaches the gateway
    directly.  Match the desktop client: connect directly, unless the user
    explicitly points ``WORKBUDDY_PROXY`` at a working proxy.
    """
    session = requests.Session()
    session.trust_env = False
    proxy = os.environ.get(PROXY_ENV, "").strip()
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}
    return session


# Chat is the hot path: one batch is one request, and a fresh TLS handshake for
# every batch is pure latency.  Each worker thread keeps its own session (a
# requests.Session is not documented as thread-safe), and a transport failure
# drops that session so the next request reconnects cleanly.
_CHAT_LOCAL = threading.local()
_CHAT_SESSIONS: list[requests.Session] = []
_CHAT_SESSIONS_LOCK = threading.Lock()
_CHAT_GENERATION = 0


def _drop_chat_session(session: requests.Session) -> None:
    with _CHAT_SESSIONS_LOCK:
        try:
            _CHAT_SESSIONS.remove(session)
        except ValueError:
            pass
    try:
        session.close()
    except Exception:  # noqa: BLE001 - a fake/foreign session must not crash
        pass
    if getattr(_CHAT_LOCAL, "session", None) is session:
        _CHAT_LOCAL.session = None


def _chat_session() -> requests.Session:
    """Return this thread's keep-alive chat session, creating it if needed."""
    proxy = os.environ.get(PROXY_ENV, "").strip()
    generation = _CHAT_GENERATION
    session = getattr(_CHAT_LOCAL, "session", None)
    if session is not None and (
        getattr(_CHAT_LOCAL, "proxy", None) != proxy
        or getattr(_CHAT_LOCAL, "generation", None) != generation
    ):
        _drop_chat_session(session)
        session = None
    if session is None:
        session = _http_session()
        _CHAT_LOCAL.session = session
        _CHAT_LOCAL.proxy = proxy
        _CHAT_LOCAL.generation = generation
        with _CHAT_SESSIONS_LOCK:
            _CHAT_SESSIONS.append(session)
    return session


def _reset_chat_session() -> None:
    session = getattr(_CHAT_LOCAL, "session", None)
    if session is not None:
        _drop_chat_session(session)


def close_chat_sessions() -> None:
    """Close every keep-alive chat session; later calls start fresh ones."""
    global _CHAT_GENERATION
    with _CHAT_SESSIONS_LOCK:
        _CHAT_GENERATION += 1
        sessions = list(_CHAT_SESSIONS)
        _CHAT_SESSIONS.clear()
    for session in sessions:
        try:
            session.close()
        except Exception:  # noqa: BLE001 - closing must never mask the job error
            pass


def _classify_body(status: int, text: str) -> WorkBuddyError:
    lowered = (text or "").lower()
    if status in (401, 403) or "12153" in lowered or "offline user session" in lowered:
        return WorkBuddyError(
            "auth",
            "the WorkBuddy desktop login was rejected by the gateway; sign in "
            "again in the WorkBuddy app",
        )
    if status == 429 or any(marker in lowered for marker in _CREDIT_MARKERS):
        return WorkBuddyError(
            "rate_limit",
            "the WorkBuddy account is out of credits or temporarily throttled; "
            "check the WorkBuddy app and try again",
        )
    if any(marker in lowered for marker in _AUTH_MARKERS):
        return WorkBuddyError(
            "auth",
            "the WorkBuddy desktop login is no longer valid; sign in again in the "
            "WorkBuddy app",
        )
    detail = _truncate(text)
    return WorkBuddyError(
        "transport",
        f"the WorkBuddy gateway returned HTTP {status}" + (f": {detail}" if detail else ""),
    )


def _response_json(response: Any) -> dict[str, Any]:
    """Read one JSON envelope, mapping HTTP/business failures to errors."""
    status = int(getattr(response, "status_code", 0) or 0)
    text = ""
    try:
        document = response.json()
    except Exception:  # noqa: BLE001 - a non-JSON error body still needs reporting
        try:
            text = str(getattr(response, "text", "") or "")
        except Exception:  # noqa: BLE001
            text = ""
        raise _classify_body(status, text) from None
    if status >= 400:
        try:
            text = str(getattr(response, "text", "") or "")
        except Exception:  # noqa: BLE001
            text = ""
        if not text:
            try:
                text = json.dumps(document, ensure_ascii=False)
            except (TypeError, ValueError):
                text = str(document)
        raise _classify_body(status, text)
    if not isinstance(document, dict):
        raise WorkBuddyError(
            "transport", "the WorkBuddy gateway returned an unexpected document"
        )
    code = document.get("code")
    if code not in (0, None):
        message = str(
            document.get("msg")
            or document.get("message")
            or document.get("error")
            or code
        )
        raise WorkBuddyError("transport", f"WorkBuddy gateway error: {_truncate(message)}")
    data = document.get("data")
    return data if isinstance(data, dict) else document


def _get_json(url: str, headers: dict[str, str], timeout: float) -> dict[str, Any]:
    with _http_session() as session:
        try:
            response = session.get(url, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            raise _request_error(exc) from exc
        return _response_json(response)


def refresh_credential(
    credential: WorkBuddyCredential, timeout: float = METADATA_TIMEOUT
) -> WorkBuddyCredential:
    """Exchange the refresh token for a new access token (in memory only)."""
    if not credential.refresh_token:
        raise WorkBuddyError(
            "not_logged_in",
            "the WorkBuddy desktop login has expired and has no refresh token; "
            "sign in again in the WorkBuddy app",
        )
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Origin": _origin(credential),
        "Referer": f"{_origin(credential)}/",
        "User-Agent": CLI_USER_AGENT,
        "X-Requested-With": "XMLHttpRequest",
        "X-Refresh-Token": credential.refresh_token,
        "X-Auth-Refresh-Source": "workbuddy",
    }
    if credential.enterprise_id:
        headers["X-Enterprise-Id"] = credential.enterprise_id
    try:
        with _http_session() as session:
            response = session.post(
                f"{_chat_base(credential)}/v2/plugin/auth/token/refresh",
                headers=headers,
                timeout=timeout,
            )
            data = _response_json(response)
    except requests.RequestException as exc:
        raise _request_error(exc) from exc
    access_token = _clean_text(data.get("accessToken"))
    if not access_token:
        raise WorkBuddyError(
            "auth",
            "the WorkBuddy token refresh returned no access token; sign in again "
            "in the WorkBuddy app",
        )
    expires_in = _as_int(data.get("expiresIn")) or 0
    refresh_token = _clean_text(data.get("refreshToken")) or credential.refresh_token
    domain = _clean_text(data.get("domain")) or credential.domain
    return replace(
        credential,
        access_token=access_token,
        refresh_token=refresh_token,
        domain=domain,
        expires_at_ms=int(time.time() * 1000) + expires_in * 1000
        if expires_in > 0
        else 0,
    )


# --- model catalog ---------------------------------------------------------


def model_label(name: str, credit: str | None) -> str:
    """The model row's label: the desktop app's name plus its credit price."""
    cleaned_name = (name or "").strip()
    cleaned_credit = (credit or "").strip()
    if cleaned_name and cleaned_credit:
        return f"{cleaned_name} · {cleaned_credit}"
    return cleaned_name or cleaned_credit


def _normalize_model(entry: Any) -> dict[str, str] | None:
    if not isinstance(entry, dict) or entry.get("disabled") is True:
        return None
    model_id = _clean_text(entry.get("id"))
    if not model_id:
        return None
    name = _clean_text(entry.get("name")) or model_id
    credit_value = entry.get("credits")
    if isinstance(credit_value, (int, float)) and not isinstance(credit_value, bool):
        credit = f"x{credit_value:g}"
    else:
        credit = _clean_text(credit_value) or ""
    return {"id": model_id, "name": name, "credits": credit}


def select_cli_models(document: dict[str, Any]) -> list[dict[str, str]]:
    """Pick the CLI-usable roster out of a catalog document.

    ``agents[name="cli"].models`` is the channel's declared order and is a
    subset of ``models``; without a usable roster every parsed model is exposed
    rather than nothing, because an empty answer would silently disarm the
    backend.
    """
    raw_models = document.get("models")
    by_id: dict[str, dict[str, str]] = {}
    for entry in raw_models if isinstance(raw_models, list) else []:
        model = _normalize_model(entry)
        if model is not None:
            by_id.setdefault(model["id"], model)

    cli_ids: list[str] | None = None
    agents = document.get("agents")
    for agent in agents if isinstance(agents, list) else []:
        if not isinstance(agent, dict) or agent.get("name") != "cli":
            continue
        models = agent.get("models")
        if isinstance(models, list):
            cli_ids = [item for item in models if isinstance(item, str)]
            break
    ids = cli_ids if cli_ids else list(by_id)
    selected = [by_id[model_id] for model_id in ids if model_id in by_id]
    if not selected:
        raise WorkBuddyError(
            "transport", "the WorkBuddy model catalog contained no usable models"
        )
    return selected


def _catalog_headers(credential: WorkBuddyCredential) -> dict[str, str]:
    if credential.region == "global":
        headers = {
            "Authorization": f"Bearer {credential.access_token}",
            "Accept": "application/json",
            "Connection": "close",
            "User-Agent": DESKTOP_USER_AGENT,
            "X-Product": "SaaS",
            "X-Requested-With": "XMLHttpRequest",
        }
        if credential.uid:
            headers["X-User-Id"] = credential.uid
        if credential.domain:
            headers["X-Domain"] = credential.domain
        return headers
    return {
        "Authorization": f"Bearer {credential.access_token}",
        "Accept": "application/json",
        "Origin": CN_BILLING_BASE,
        "Referer": f"{CN_BILLING_BASE}/",
        "User-Agent": CLI_USER_AGENT,
    }


def fetch_models(
    credential: WorkBuddyCredential,
    timeout: float = METADATA_TIMEOUT,
) -> list[dict[str, str]]:
    """The CLI model roster for the credential's region."""
    if not credential.access_token:
        raise WorkBuddyError(
            "not_logged_in", "the WorkBuddy desktop app is not signed in"
        )
    headers = _catalog_headers(credential)
    if credential.region == "global":
        document = _get_json(
            f"{global_base(credential.domain)}/v3/config", headers, timeout
        )
        return select_cli_models(document)

    try:
        document = _get_json(f"{CN_CHAT_BASE}/v3/config", headers, timeout)
        return select_cli_models(document)
    except WorkBuddyError as primary:
        # The legacy document is field-compatible and is the app's own fallback
        # source.  A signed-out/bad credential is not worth retrying, though.
        if primary.kind in {"auth", "not_logged_in"}:
            raise
        try:
            document = _get_json(
                f"{CN_CHAT_BASE}/v2/enterprises/personal/models", headers, timeout
            )
            return select_cli_models(document)
        except WorkBuddyError as fallback:
            raise WorkBuddyError(
                fallback.kind,
                f"{primary}; the fallback catalog also failed: {fallback}",
            ) from primary


def desktop_status() -> dict[str, Any]:
    """A token-free status for the Agents card.

    Reads only local state: whether the app is installed, whether an auth file
    exists, and whether it can be decrypted.  It deliberately performs no
    network request, so ``agents.list`` stays within its budget.
    """
    app = workbuddy_app_path()
    if app is None:
        return {"available": False, "auth": "unknown", "app_path": None}
    if workbuddy_auth_path() is None:
        return {"available": True, "auth": "missing", "app_path": str(app)}
    try:
        credential = workbuddy_credential(allow_refresh=False)
    except WorkBuddyError as exc:
        auth = "missing" if exc.kind == "not_logged_in" else "unknown"
        return {
            "available": True,
            "auth": auth,
            "app_path": str(app),
            "error": str(exc),
        }
    if credential.expired and not credential.refresh_token:
        return {
            "available": True,
            "auth": "missing",
            "app_path": str(app),
            "error": "the WorkBuddy desktop login expired; sign in again",
        }
    return {"available": True, "auth": "ok", "app_path": str(app)}


# --- chat ------------------------------------------------------------------


def _chat_headers(credential: WorkBuddyCredential) -> dict[str, str]:
    origin = _origin(credential)
    headers = {
        "Authorization": f"Bearer {credential.access_token}",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": origin,
        "Referer": f"{origin}/",
        "User-Agent": CLI_USER_AGENT,
        "X-Requested-With": "XMLHttpRequest",
        "X-Product": "SaaS",
        # The gateway requires the official client's conversation identity.
        "X-Conversation-ID": str(uuid.uuid4()),
        "X-Conversation-Request-ID": str(uuid.uuid4()),
        "X-Conversation-Message-ID": str(uuid.uuid4()),
    }
    if credential.uid:
        headers["X-User-Id"] = credential.uid
    else:
        headers["X-No-User-Id"] = "1"
    if credential.enterprise_id:
        headers["X-Enterprise-Id"] = credential.enterprise_id
    else:
        headers["X-No-Enterprise-Id"] = "1"
    if credential.domain:
        headers["X-Domain"] = credential.domain
    else:
        headers["X-No-Department-Info"] = "1"
    return headers


def _delta_text(chunk: Any) -> str:
    if not isinstance(chunk, dict):
        return ""
    choices = chunk.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    choice = choices[0]
    if not isinstance(choice, dict):
        return ""
    for container in (choice.get("delta"), choice.get("message")):
        if not isinstance(container, dict):
            continue
        content = container.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                part.get("text")
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            ]
            if parts:
                return "".join(parts)
    return ""


def _raise_for_stream_error(chunk: Any) -> None:
    if not isinstance(chunk, dict):
        return
    error = chunk.get("error")
    if error:
        message = error.get("message") if isinstance(error, dict) else str(error)
        raise _classify_body(int(chunk.get("status") or 0), str(message or ""))
    code = chunk.get("code")
    if code not in (0, None) and not chunk.get("choices"):
        message = chunk.get("msg") or chunk.get("message") or code
        raise _classify_body(int(chunk.get("status") or 0), str(message))


def _read_stream(response: Any, cancel_event: Any) -> str:
    chunks: list[str] = []
    try:
        for raw in response.iter_lines(decode_unicode=True):
            if cancel_event is not None:
                try:
                    cancelled = bool(cancel_event.is_set())
                except Exception:  # noqa: BLE001 - a foreign event must not crash
                    cancelled = False
                if cancelled:
                    raise asyncio.CancelledError("task cancelled")
            if raw is None:
                continue
            line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
            line = line.strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload:
                continue
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except ValueError:
                continue
            _raise_for_stream_error(chunk)
            text = _delta_text(chunk)
            if text:
                chunks.append(text)
    except requests.RequestException as exc:
        raise _request_error(exc) from exc
    answer = "".join(chunks).strip()
    if not answer:
        raise WorkBuddyError(
            "output", "the WorkBuddy desktop app returned an empty answer"
        )
    return answer


def _header(response: Any, name: str) -> str:
    headers = getattr(response, "headers", None)
    if headers is None:
        return ""
    try:
        return str(headers.get(name) or "")
    except Exception:  # noqa: BLE001 - a foreign response object must not crash
        return ""


def _read_json_answer(response: Any, status: int) -> str | None:
    """Handle a non-SSE answer (some gateway failures come back as JSON 200).

    Returns the answer text when the document is a normal completion, and
    raises a classified :class:`WorkBuddyError` when it is an error envelope.
    """
    try:
        document = response.json()
    except Exception:  # noqa: BLE001 - fall through to the body text
        document = None
    if isinstance(document, dict):
        _raise_for_stream_error(document)
        text = _delta_text(document)
        if text:
            return text
        data = document.get("data")
        if isinstance(data, dict):
            text = _delta_text(data)
            if text:
                return text
    try:
        body = str(getattr(response, "text", "") or "")
    except Exception:  # noqa: BLE001
        body = ""
    if not body and document is not None:
        try:
            body = json.dumps(document, ensure_ascii=False)
        except (TypeError, ValueError):
            body = str(document)
    raise _classify_body(status, body)


def _resolve_model(credential: WorkBuddyCredential, model: str | None) -> str:
    chosen = (model or "").strip()
    if chosen and chosen != "default":
        return chosen
    models = fetch_models(credential)
    return models[0]["id"]


def _chat_once(
    credential: WorkBuddyCredential,
    model: str,
    prompt: str,
    timeout: float,
    cancel_event: Any,
) -> str:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": True,
    }
    headers = _chat_headers(credential)
    session = _chat_session()
    try:
        response = session.post(
            f"{_chat_base(credential)}/v2/chat/completions",
            headers=headers,
            json=body,
            timeout=(10, timeout),
            stream=True,
        )
    except requests.RequestException as exc:
        _reset_chat_session()
        raise _request_error(exc) from exc
    try:
        status = int(getattr(response, "status_code", 0) or 0)
        if status >= 400:
            try:
                text = str(getattr(response, "text", "") or "")
            except Exception:  # noqa: BLE001
                text = ""
            raise _classify_body(status, text)
        content_type = _header(response, "content-type").lower()
        if "json" in content_type and "event-stream" not in content_type:
            answer = _read_json_answer(response, status)
            if answer is not None:
                return answer
        return _read_stream(response, cancel_event)
    except WorkBuddyError as exc:
        if exc.kind == "transport":
            # A dropped connection may leave the pooled connection unusable;
            # the next request should start from a fresh one.
            _reset_chat_session()
        raise
    finally:
        try:
            response.close()
        except Exception:  # noqa: BLE001
            pass


def chat(
    credential: WorkBuddyCredential,
    model: str | None,
    prompt: str,
    *,
    timeout: float = CHAT_TIMEOUT,
    cancel_event: Any = None,
    allow_refresh: bool = True,
) -> str:
    """Send one prompt through the desktop app's gateway and return the text.

    A rejected credential is refreshed once, in memory, and the request is
    retried.  Nothing is written back to the app's auth file.
    """
    chosen = _resolve_model(credential, model)
    try:
        return _chat_once(credential, chosen, prompt, timeout, cancel_event)
    except WorkBuddyError as exc:
        if exc.kind != "auth" or not allow_refresh:
            raise
        refreshed = refresh_credential(credential)
        _store_credential(refreshed)
        return _chat_once(refreshed, chosen, prompt, timeout, cancel_event)


# --- batch translation -----------------------------------------------------


def _parse_answer(answer: str, texts: Sequence[str], name: str) -> list[str]:
    # Imported lazily: ``runtime`` imports ``catalog`` which imports this
    # module, so a top-level import would close the cycle.
    from pdfxlate.agents import runtime

    parsed = runtime.parse_translations(answer, texts)
    if parsed is None:
        raise WorkBuddyError(
            "output",
            f"could not parse the WorkBuddy desktop answer into {len(texts)} "
            f"translation(s); output began: {_truncate(answer)}",
        )
    return parsed


def translate_batch(
    model: str | None,
    texts: Sequence[str],
    lang_out: str,
    *,
    custom_prompt: str | None = None,
    timeout: float = CHAT_TIMEOUT,
    cancel_event: Any = None,
    lang_in: str = "en",
) -> list[str]:
    """Translate a batch through the desktop app, matching ``runtime.run_batch``.

    The prompt/parse contract is the same one the CLI path uses (lazy import of
    :mod:`pdfxlate.agents.runtime`): one JSON-object request, one strict retry,
    then per-paragraph requests.  A custom template runs one paragraph per call
    because its output shape is the user's, not ours.
    """
    from pdfxlate.agents import runtime

    batch = list(texts)
    if not batch:
        return []

    # Resolve "default" once: a split retry must not re-fetch the catalog for
    # every paragraph.
    concrete_model = _resolve_model(workbuddy_credential(), model)

    def ask(prompt: str) -> str:
        # Re-read the cached credential on every call: a token refresh during a
        # retry must reach the next request.
        return chat(
            workbuddy_credential(),
            concrete_model,
            prompt,
            timeout=timeout,
            cancel_event=cancel_event,
        )

    if custom_prompt is not None:
        results: list[str] = []
        for text in batch:
            prompt = runtime.render_custom_prompt(custom_prompt, text, lang_in, lang_out)
            results.append(_parse_answer(ask(prompt), [text], "custom")[0])
        return results

    def attempt(items: Sequence[str], strict: bool) -> list[str]:
        if len(items) == 1 and strict:
            prompt = runtime.build_single_prompt(items[0], lang_out, strict=True)
        else:
            prompt = runtime.build_batch_prompt(items, lang_out, strict=strict)
        return _parse_answer(ask(prompt), items, "batch")

    try:
        return attempt(batch, False)
    except WorkBuddyError as first:
        if first.kind != "output":
            raise
        try:
            return attempt(batch, True)
        except WorkBuddyError as second:
            if second.kind != "output":
                raise
            if len(batch) == 1:
                raise
            results: list[str] = []
            for text in batch:
                results.append(attempt([text], True)[0])
            return results


__all__ = [
    "AUTH_FILE_ENV",
    "APP_EXECUTABLE_ENV",
    "AtRestKey",
    "CHAT_TIMEOUT",
    "GLOBAL_BASE",
    "WorkBuddyCredential",
    "WorkBuddyError",
    "chat",
    "clear_caches",
    "derive_at_rest_key",
    "derive_key_id",
    "desktop_status",
    "expiry_to_ms",
    "fetch_models",
    "field_aad",
    "has_encrypted_fields",
    "model_label",
    "open_field",
    "parse_auth",
    "read_at_rest_key",
    "refresh_credential",
    "region_of",
    "select_cli_models",
    "translate_batch",
    "workbuddy_app_path",
    "workbuddy_auth_candidates",
    "workbuddy_auth_path",
    "workbuddy_credential",
]
