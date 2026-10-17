"""``--mcp`` turns the sidecar binary into the MCP server.

This is what makes a *packaged* install reachable by an agent. A PyInstaller
onedir bundle has no `python -m` to offer — the executable is the interpreter
with one entry point baked in — so the agent has to launch that binary with a
flag. These tests run the same launch path the agent will, against the module
form here (the packaged binary is the same code with a different argv[0]).
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("mcp", reason="the optional 'mcp' extra is not installed")

import pdfxlate  # noqa: E402

_PACKAGE_ROOT = Path(pdfxlate.__file__).resolve().parent.parent


def _env(config_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(_PACKAGE_ROOT), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    env["PDFXLATE_CONFIG_DIR"] = str(config_dir)
    return env


def _launch(args: list[str], config_dir: Path) -> subprocess.Popen:
    """Start the sidecar with `args` and return the process."""
    return subprocess.Popen(
        [sys.executable, "-m", "pdfxlate.sidecar", *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=_env(config_dir),
    )


class _Client:
    """Minimal MCP client over a subprocess's pipes.

    Reads on a background thread into a queue rather than with `readline` on the
    main one: a notification has no reply, so a blocking read for one would hang
    forever. This is the same shape the existing MCP tests use.
    """

    def __init__(self, args: list[str], config_dir: Path) -> None:
        self.proc = _launch(args, config_dir)
        self._lines: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self._id = 0

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self._lines.put(line)

    def request(self, method: str, params: dict | None = None) -> dict:
        """Send a request and wait for its reply."""
        assert self.proc.stdin is not None
        self._id += 1
        payload: dict = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            payload["params"] = params
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()
        line = self._lines.get(timeout=60)
        reply = json.loads(line)
        assert reply.get("id") == self._id, f"unexpected reply: {reply}"
        return reply

    def notify(self, method: str) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.proc.stdin.flush()

    def close(self) -> None:
        self.proc.kill()

    def first_line(self) -> dict:
        """The next raw line, whatever it is — used to tell the two modes apart."""
        return json.loads(self._lines.get(timeout=60))


_INIT = {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {"name": "test", "version": "1"},
}


def test_mcp_flag_starts_an_mcp_handshake(tmp_path):
    """With --mcp the process speaks MCP: initialize, then tools/list.

    The sidecar's own protocol starts by announcing ``{"event": "ready"}``, so
    the very first reply proves which mode was entered.
    """
    client = _Client(["--mcp", "--config-dir", str(tmp_path)], tmp_path)
    try:
        reply = client.request("initialize", _INIT)
        assert "result" in reply, f"no result in {reply}"
        # An MCP initialize result carries the server's own identity.
        assert reply["result"]["serverInfo"]["name"] == "pdfxlate"

        # And the tools are really there, which is what an agent will call.
        client.notify("notifications/initialized")
        listing = client.request("tools/list")
        names = {tool["name"] for tool in listing["result"]["tools"]}
        assert names == {
            "list_providers",
            "extract_segments",
            "typeset_translations",
            "translate_pdf",
        }
    finally:
        client.close()


def test_without_the_flag_it_is_still_the_sidecar(tmp_path):
    """The default must not change: the desktop app depends on it."""
    client = _Client(["--config-dir", str(tmp_path)], tmp_path)
    try:
        first = client.first_line()
        assert first["event"] == "ready"
        assert "version" in first

        # And it answers its own protocol, not JSON-RPC.
        assert client.proc.stdin is not None
        client.proc.stdin.write(json.dumps({"id": 1, "method": "ping", "params": {}}) + "\n")
        client.proc.stdin.flush()
        reply = json.loads(client._lines.get(timeout=60))
        assert reply["id"] == 1
        assert "config_dir" in reply["result"]
    finally:
        client.close()


def test_mcp_flag_can_appear_in_any_position(tmp_path):
    """Argparse-free pass-through: the agent writes the argv, we tolerate it."""
    client = _Client(["--config-dir", str(tmp_path), "--mcp"], tmp_path)
    try:
        reply = client.request("initialize", _INIT)
        assert reply["result"]["serverInfo"]["name"] == "pdfxlate"
    finally:
        client.close()
