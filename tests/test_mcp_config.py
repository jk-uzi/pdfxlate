"""Writing the MCP entry into an agent's config.

These files belong to other programs and hold credentials, so the bar is not
"does it add the entry" but "does it leave everything else alone, and does it
fail without damage". Every test here is about that second half.
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdfxlate import mcp_config  # noqa: E402

CMD = "/opt/pdfxlate/pdfxlate-mcp"
ARGS = ["--config-dir", "/home/u/.config/pdfxlate"]


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# --- the three shapes -------------------------------------------------------


def test_plain_json_adds_the_entry(tmp_path):
    path = _write(tmp_path, "claude_desktop_config.json", '{"mcpServers": {}}')
    result = mcp_config.install(str(path), mcp_config.PLAIN_JSON_KEY, CMD, ARGS)

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["mcpServers"]["pdfxlate"] == {"command": CMD, "args": ARGS}
    assert result["changed"] is True
    assert Path(result["backup"]).exists()


def test_zcode_entry_has_the_fields_zcode_requires(tmp_path):
    """ZCode rejects an entry without a transport type and an enabled flag."""
    path = _write(tmp_path, "config.json", '{"mcp": {"servers": {}}}')
    mcp_config.install(str(path), mcp_config.ZCODE_KEY, CMD, ARGS)

    entry = json.loads(path.read_text(encoding="utf-8"))["mcp"]["servers"]["pdfxlate"]
    assert entry["type"] == "stdio"
    assert entry["enabled"] is True
    assert entry["timeoutMs"] > 0
    assert entry["command"] == CMD
    assert entry["args"] == ARGS


def test_toml_adds_a_table(tmp_path):
    path = _write(tmp_path, "config.toml", "[other]\nkey = 1\n")
    mcp_config.install(str(path), mcp_config.TOML_KEY, CMD, ARGS)

    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    assert parsed["mcp_servers"]["pdfxlate"]["command"] == CMD
    assert parsed["mcp_servers"]["pdfxlate"]["args"] == ARGS
    # The unrelated table is still there.
    assert parsed["other"]["key"] == 1


def test_toml_into_an_empty_file(tmp_path):
    path = _write(tmp_path, "config.toml", "")
    mcp_config.install(str(path), mcp_config.TOML_KEY, CMD, ARGS)
    assert tomllib.loads(path.read_text(encoding="utf-8"))["mcp_servers"]["pdfxlate"]["command"] == CMD


# --- leaving everything else alone -----------------------------------------


def test_existing_servers_are_preserved(tmp_path):
    """Someone else's entries must survive untouched: this file is not ours."""
    original = {
        "mcpServers": {
            "github": {"command": "/usr/bin/gh-mcp", "args": ["--token", "x"]},
            "other": {"command": "other", "args": [], "env": {"A": "1"}},
        },
        "unrelatedTopLevel": {"keep": True},
    }
    path = _write(tmp_path, "claude_desktop_config.json", json.dumps(original))
    mcp_config.install(str(path), mcp_config.PLAIN_JSON_KEY, CMD, ARGS)

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["mcpServers"]["github"] == original["mcpServers"]["github"]
    assert document["mcpServers"]["other"] == original["mcpServers"]["other"]
    assert document["unrelatedTopLevel"] == {"keep": True}


def test_toml_neighbours_and_comments_survive(tmp_path):
    """TOML is edited as text, so comments and unrelated tables must persist."""
    text = (
        "# my codex config\n"
        "model = 'gpt-5'\n"
        "\n"
        "[mcp_servers.other]\n"
        "command = 'other'\n"
        "\n"
        "[other_section]\n"
        "value = 2\n"
    )
    path = _write(tmp_path, "config.toml", text)
    mcp_config.install(str(path), mcp_config.TOML_KEY, CMD, ARGS)

    after = path.read_text(encoding="utf-8")
    assert "# my codex config" in after
    assert "model = 'gpt-5'" in after
    assert "[other_section]" in after
    parsed = tomllib.loads(after)
    assert parsed["mcp_servers"]["other"]["command"] == "other"
    assert parsed["other_section"]["value"] == 2


def test_other_zcode_servers_survive(tmp_path):
    original = {
        "mcp": {"servers": {"ansys": {"type": "stdio", "command": "python", "args": ["x.py"]}}},
        "plugins": {"enabledPlugins": {"github@x": True}},
    }
    path = _write(tmp_path, "config.json", json.dumps(original))
    mcp_config.install(str(path), mcp_config.ZCODE_KEY, CMD, ARGS)

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["mcp"]["servers"]["ansys"]["command"] == "python"
    assert document["plugins"]["enabledPlugins"] == {"github@x": True}


# --- running it twice ------------------------------------------------------


@pytest.mark.parametrize(
    "key,name,text",
    [
        (mcp_config.PLAIN_JSON_KEY, "a.json", '{"mcpServers": {}}'),
        (mcp_config.ZCODE_KEY, "b.json", '{"mcp": {"servers": {}}}'),
        (mcp_config.TOML_KEY, "c.toml", ""),
    ],
)
def test_installing_twice_is_idempotent(tmp_path, key, name, text):
    """Writing again must not duplicate the entry or turn it into a list."""
    path = _write(tmp_path, name, text)
    mcp_config.install(str(path), key, CMD, ARGS)
    first = path.read_text(encoding="utf-8")
    result = mcp_config.install(str(path), key, CMD, ARGS)
    second = path.read_text(encoding="utf-8")

    assert first == second
    # And it says so rather than claiming to have changed something.
    assert result["changed"] is False
    if key == mcp_config.TOML_KEY:
        assert second.count("[mcp_servers.pdfxlate]") == 1
    else:
        document = json.loads(second)
        servers = document["mcpServers"] if key == mcp_config.PLAIN_JSON_KEY else document["mcp"]["servers"]
        assert isinstance(servers["pdfxlate"], dict)


def test_toml_rewrites_an_existing_stale_entry(tmp_path):
    """An entry pointing at a moved installation is updated, not duplicated."""
    path = _write(
        tmp_path,
        "config.toml",
        "[mcp_servers.pdfxlate]\ncommand = '/old/path'\nargs = ['--old']\n",
    )
    mcp_config.install(str(path), mcp_config.TOML_KEY, CMD, ARGS)

    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    assert parsed["mcp_servers"]["pdfxlate"]["command"] == CMD
    assert "/old/path" not in path.read_text(encoding="utf-8")


# --- failing without damage ------------------------------------------------


@pytest.mark.parametrize(
    "key,name,broken",
    [
        (mcp_config.PLAIN_JSON_KEY, "bad.json", "{not json at all"),
        (mcp_config.ZCODE_KEY, "bad2.json", "{oops"),
        (mcp_config.TOML_KEY, "bad.toml", "[unclosed\nkey = "),
    ],
)
def test_a_broken_file_is_left_exactly_as_it_was(tmp_path, key, name, broken):
    """Repairing a file we cannot parse would be the worst outcome.

    It belongs to another program and holds its settings; guessing at the
    intended content is not ours to do.
    """
    path = _write(tmp_path, name, broken)
    with pytest.raises(mcp_config.McpConfigError):
        mcp_config.install(str(path), key, CMD, ARGS)

    assert path.read_text(encoding="utf-8") == broken
    # And no stray backup: nothing was touched, so there is nothing to restore.
    assert not list(tmp_path.glob("*.bak-*"))


def test_missing_file_is_reported(tmp_path):
    with pytest.raises(mcp_config.McpConfigError, match="does not exist"):
        mcp_config.install(str(tmp_path / "nope.json"), mcp_config.PLAIN_JSON_KEY, CMD, ARGS)


def test_a_non_object_namespace_is_refused(tmp_path):
    """`"mcpServers": []` is a shape we cannot write into; say so."""
    path = _write(tmp_path, "odd.json", '{"mcpServers": []}')
    with pytest.raises(mcp_config.McpConfigError, match="not an object"):
        mcp_config.install(str(path), mcp_config.PLAIN_JSON_KEY, CMD, ARGS)
    assert path.read_text(encoding="utf-8") == '{"mcpServers": []}'


def test_backup_matches_the_original(tmp_path):
    original = '{"mcpServers": {"keep": {"command": "k", "args": []}}}'
    path = _write(tmp_path, "a.json", original)
    result = mcp_config.install(str(path), mcp_config.PLAIN_JSON_KEY, CMD, ARGS)

    assert Path(result["backup"]).read_text(encoding="utf-8") == original


def test_creates_a_missing_namespace(tmp_path):
    """A config that has no `mcpServers` key yet gets one, not an error."""
    path = _write(tmp_path, "claude.json", '{"numStartups": 7}\n')
    mcp_config.install(str(path), mcp_config.PLAIN_JSON_KEY, CMD, ARGS)

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["numStartups"] == 7
    assert document["mcpServers"]["pdfxlate"]["command"] == CMD


# --- the snippet -----------------------------------------------------------


def test_snippet_is_pasteable_json():
    text = mcp_config.snippet(CMD, ARGS)
    parsed = json.loads(text)
    assert parsed == {"mcpServers": {"pdfxlate": {"command": CMD, "args": ARGS}}}
