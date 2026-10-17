"""Write one MCP server entry into an agent's config, without disturbing it.

Each agent names and nests this differently, so there is no single shape to
target. Three shapes cover the agents the app offers to write for:

* ``mcpServers`` — a flat object of name to ``{command, args}``. Claude Desktop,
  Claude Code and WorkBuddy all use this.
* ``mcp.servers`` — ZCode nests it and adds ``type``, ``enabled`` and
  ``timeoutMs`` beside the command.
* ``[mcp_servers.<name>]`` — Codex uses TOML rather than JSON.

Nothing here is generic config editing. The only thing that changes is the one
entry named by the caller; everything else in the file is read, carried through
and written back. And because these files hold credentials and other people's
settings, the write is hedged three ways:

1. the original is copied to a timestamped ``.bak`` beside it first;
2. the result is re-read and checked to still parse and to actually contain the
   entry;
3. if either the write or that check fails, the backup is restored and the
   error is raised.

A malformed file is never repaired: it is reported. Silently rewriting something
we could not parse would be the worst possible outcome for a file we do not own.

File contents are deliberately never logged. Several of these configs contain
API tokens (a GitHub token was found in one while building this), so the log
receives paths and outcomes only.
"""

from __future__ import annotations

import json
import shutil
import time
import tomllib
from pathlib import Path
from typing import Any

# The entry name written into every agent's config. A name rather than a path,
# because that is what these formats key on, and it is what the user sees in
# their agent's tool list.
SERVER_NAME = "pdfxlate"

# Where the entry goes, which is also how the format is identified. Three
# values cover every agent the app offers to write for:
#   mcpServers      — flat object; Claude Desktop, Claude Code, WorkBuddy
#   mcp.servers     — ZCode's nesting, which also wants a transport type
#   toml            — Codex, whose `[mcp_servers.<name>]` table lives in TOML
PLAIN_JSON_KEY = "mcpServers"
ZCODE_KEY = "mcp.servers"
TOML_KEY = "toml"

# ZCode wants a transport type spelled out, and a timeout. Ten minutes because a
# first run may download the layout model and then translate a long document.
_ZCODE_TIMEOUT_MS = 600_000


class McpConfigError(Exception):
    """A config file could not be written safely."""


def _backup(path: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    shutil.copy2(path, backup)
    return backup


def _entry(command: str, args: list[str]) -> dict[str, Any]:
    """The plain ``mcpServers`` shape."""
    return {"command": command, "args": list(args)}


def _apply_json(
    raw_text: str, target_key: str, command: str, args: list[str]
) -> tuple[str, str]:
    """Return ``(new_text, entry_description)`` for a JSON config.

    ``target_key`` is the dotted path to the servers object, which is also what
    says which shape the entry takes: ``mcpServers`` is the plain form, while
    ZCode's nested ``mcp.servers`` additionally wants a transport type and an
    enabled flag.
    """
    try:
        document = json.loads(raw_text) if raw_text.strip() else {}
    except json.JSONDecodeError as exc:
        raise McpConfigError(
            f"not valid JSON ({exc}); left unchanged. Fix it by hand, or copy "
            f"the snippet from the MCP panel instead."
        ) from exc
    if not isinstance(document, dict):
        raise McpConfigError("does not contain a JSON object; left unchanged.")

    keys = target_key.split(".")
    container = document
    for key in keys:
        nested = container.get(key)
        if nested is None:
            nested = {}
            container[key] = nested
        elif not isinstance(nested, dict):
            raise McpConfigError(
                f"{target_key} is not an object in this file; left unchanged."
            )
        container = nested

    if target_key == ZCODE_KEY:
        # ZCode wants the transport named and the server explicitly enabled.
        container[SERVER_NAME] = {
            "type": "stdio",
            "command": command,
            "args": list(args),
            "enabled": True,
            "timeoutMs": _ZCODE_TIMEOUT_MS,
        }
    else:
        container[SERVER_NAME] = _entry(command, args)

    # A JSON round trip reformats the file, but the document itself is
    # untouched: every other key survives with its value.
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n", target_key


def _toml_entry_block(command: str, args: list[str]) -> str:
    """The ``[mcp_servers.pdfxlate]`` table, as TOML text.

    Written by hand because the standard library reads TOML but does not write
    it. That is acceptable here precisely because the block is fixed in shape:
    this is not a general TOML serializer and never has to be.
    """
    lines = [f"[mcp_servers.{SERVER_NAME}]", f"command = {json.dumps(command)}"]
    if args:
        rendered = ", ".join(json.dumps(a) for a in args)
        lines.append(f"args = [{rendered}]")
    lines.append("enabled = true")
    return "\n".join(lines) + "\n"


def _apply_toml(raw_text: str, command: str, args: list[str]) -> tuple[str, str]:
    """Return ``(new_text, "mcp_servers.<name>")`` for a TOML config.

    The existing text is preserved and only the ``[mcp_servers.pdfxlate]``
    section is replaced or appended, so comments and other tables survive.
    """
    try:
        tomllib.loads(raw_text) if raw_text.strip() else {}
    except tomllib.TOMLDecodeError as exc:
        raise McpConfigError(
            f"not valid TOML ({exc}); left unchanged. Fix it by hand, or copy "
            f"the snippet from the MCP panel instead."
        ) from exc

    header = f"[mcp_servers.{SERVER_NAME}]"
    block = _toml_entry_block(command, args)
    lines = raw_text.splitlines(keepends=True)

    start = next((i for i, line in enumerate(lines) if line.strip() == header), None)
    if start is None:
        text = raw_text
        if text and not text.endswith("\n"):
            text += "\n"
        if text.strip():
            text += "\n"
        return text + block, f"mcp_servers.{SERVER_NAME}"

    # Replace up to the next table header, so neighbouring tables survive.
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].lstrip().startswith("["):
            end = i
            break
    # Keep a blank line between this table and the next one, matching how the
    # surrounding file is spaced.
    separator = "\n" if end < len(lines) else ""
    return (
        "".join(lines[:start]) + block + separator + "".join(lines[end:]),
        f"mcp_servers.{SERVER_NAME}",
    )


def _verify(new_text: str, target_key: str) -> None:
    """Confirm the written text parses and really contains our entry."""
    if target_key == TOML_KEY:
        found = tomllib.loads(new_text).get("mcp_servers", {}).get(SERVER_NAME)
    else:
        document = json.loads(new_text)
        found = document
        for key in target_key.split("."):
            found = found.get(key, {}) if isinstance(found, dict) else {}
        found = found.get(SERVER_NAME) if isinstance(found, dict) else None
    if not found:
        raise McpConfigError("entry not present after write")


def install(file: str, target_key: str, command: str, args: list[str]) -> dict[str, str]:
    """Add the ``pdfxlate`` server entry to an agent config.

    ``target_key`` identifies both the file format and where the entry goes
    inside it — see the module docstring for the three shapes.

    Returns ``{"path", "backup", "target", "changed"}``. Raises
    :class:`McpConfigError` for a file that is missing, unparseable or not
    writable; in every one of those cases the file on disk is left as it was.
    """
    target = Path(file).expanduser()
    if not target.exists():
        raise McpConfigError(f"{target} does not exist")
    if not target.is_file():
        raise McpConfigError(f"{target} is not a file")

    raw = target.read_text(encoding="utf-8")
    before = raw

    if target_key == TOML_KEY:
        new_text, where = _apply_toml(raw, command, args)
    else:
        new_text, where = _apply_json(raw, target_key, command, args)
    _verify(new_text, target_key)

    backup = _backup(target)
    try:
        target.write_text(new_text, encoding="utf-8")
        _verify(target.read_text(encoding="utf-8"), target_key)
    except Exception:
        # Put it back: a half-written config would break the user's agent, which
        # is a far worse outcome than this feature not working.
        shutil.copy2(backup, target)
        raise

    return {
        "path": str(target),
        "backup": str(backup),
        "target": where,
        # Lets the UI say "already configured" rather than claiming a change.
        "changed": new_text != before,
    }


def snippet(command: str, args: list[str]) -> str:
    """The JSON to paste into any agent, for the ones we do not write for."""
    return json.dumps(
        {"mcpServers": {SERVER_NAME: _entry(command, args)}},
        indent=2,
        ensure_ascii=False,
    )
