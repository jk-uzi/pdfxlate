"""Tests for the MCP server and the two-phase translation engine.

Three layers are covered:

* the pure two-phase primitives (fingerprint, collecting/dict translators, the
  determinism guard) that need no PDF;
* a real end-to-end stdio JSON-RPC handshake against the server launched as a
  subprocess, so the wire protocol is exercised for real;
* an offline end-to-end run of the two-phase engine over a sample PDF, with
  identity translations and no model API.

The end-to-end pieces skip with a clear reason when the sample PDF, the layout
model or the target font are absent, so the suite stays runnable offline.
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

# ``mcp`` is an optional extra; every test in this module needs it.
pytest.importorskip("mcp", reason="the optional 'mcp' extra is not installed")

import pdfxlate  # noqa: E402
from pdfxlate import config as config_mod  # noqa: E402
from pdfxlate import paths  # noqa: E402
from pdfxlate.pdf import two_phase  # noqa: E402

# Directory holding the ``pdfxlate`` package under test.  Passed to the server
# subprocess on PYTHONPATH so it imports *this* checkout, not the editable
# install (which points at the main repo).
_PACKAGE_ROOT = Path(pdfxlate.__file__).resolve().parent.parent

_WORKTREE_ROOT = Path(__file__).resolve().parents[1]


# --- pure two-phase primitives --------------------------------------------


def test_fingerprint_is_order_and_boundary_sensitive():
    assert two_phase.sequence_fingerprint(["ab", "c"]) != two_phase.sequence_fingerprint(
        ["a", "bc"]
    )
    assert two_phase.sequence_fingerprint(["a", "b"]) != two_phase.sequence_fingerprint(
        ["b", "a"]
    )
    assert two_phase.sequence_fingerprint([]) != two_phase.sequence_fingerprint([""])
    assert two_phase.sequence_fingerprint(["a"]) == two_phase.sequence_fingerprint(["a"])


def test_collecting_translator_records_and_returns_source():
    collector = two_phase._make_adapter(two_phase.CollectingTranslator, "en", "zh")
    assert collector.cache.enabled is False  # never touches the cache
    assert collector.translate("See {v0}.") == "See {v0}."
    assert collector.translate("Second.") == "Second."
    assert collector.texts == ["See {v0}.", "Second."]


def test_dict_translator_looks_up_and_records_seen():
    translator = two_phase._make_adapter(
        two_phase.DictTranslator, "en", "zh", {"hello": "你好"}
    )
    assert translator.translate("hello") == "你好"
    assert translator.seen == ["hello"]


def test_dict_translator_miss_is_not_an_Exception():
    """The miss must be a BaseException: the converter retries Exception forever."""
    translator = two_phase._make_adapter(two_phase.DictTranslator, "en", "zh", {})
    with pytest.raises(two_phase.MissingTranslationError) as exc:
        translator.translate("absent")
    assert not isinstance(exc.value, Exception)
    assert "absent" in str(exc.value)


def test_dict_translator_enforces_placeholders():
    translator = two_phase._make_adapter(
        two_phase.DictTranslator, "en", "zh", {"a {v0} b": "译文"}
    )
    with pytest.raises(two_phase.TranslationPlaceholderError) as exc:
        translator.translate("a {v0} b")
    assert exc.value.missing == {"0"}


def test_dict_translator_verify_detects_wrong_sequence():
    translator = two_phase._make_adapter(
        two_phase.DictTranslator, "en", "zh", {"a": "1", "b": "2"}
    )
    translator.translate("a")
    translator.translate("b")
    # Correct fingerprint passes.
    translator.verify(two_phase.sequence_fingerprint(["a", "b"]))
    # A fingerprint for a different sequence fails.
    with pytest.raises(two_phase.DeterminismError):
        translator.verify(two_phase.sequence_fingerprint(["b", "a"]))


def test_build_text_map_rejects_missing_and_unknown_ids():
    collection = two_phase.Collection(
        file="x.pdf", pages=None, mode="fast", texts=["a", "b"], fingerprint="f"
    )
    with pytest.raises(two_phase.SegmentMappingError, match="missing translations"):
        two_phase._build_text_map(collection, {"s0": "A"})
    with pytest.raises(two_phase.SegmentMappingError, match="unknown segment"):
        two_phase._build_text_map(collection, {"s0": "A", "s1": "B", "s9": "C"})


def test_build_text_map_rejects_conflicting_duplicate():
    collection = two_phase.Collection(
        file="x.pdf", pages=None, mode="fast", texts=["dup", "dup"], fingerprint="f"
    )
    with pytest.raises(two_phase.SegmentMappingError, match="conflicting"):
        two_phase._build_text_map(collection, {"s0": "A", "s1": "B"})


def test_job_table_roundtrip_and_unknown_job():
    collection = two_phase.Collection(
        file="x.pdf", pages=None, mode="fast", texts=["a"], fingerprint="f"
    )
    two_phase.store_job(collection)
    try:
        job = two_phase.get_job(collection.job_id)
        assert job.collection is collection
        with pytest.raises(KeyError, match="unknown job_id"):
            two_phase.get_job("nope")
    finally:
        two_phase.drop_job(collection.job_id)
    with pytest.raises(KeyError):
        two_phase.get_job(collection.job_id)


# --- stdio JSON-RPC handshake ---------------------------------------------


def _server_env(config_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    # Prepend this checkout so the child imports the code under test.
    env["PYTHONPATH"] = os.pathsep.join(
        [str(_PACKAGE_ROOT), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    env["PDFXLATE_CONFIG_DIR"] = str(config_dir)
    return env


class _StdioClient:
    """A minimal newline-delimited JSON-RPC client over a subprocess's pipes."""

    def __init__(self, config_dir: Path) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "pdfxlate.mcp_server", "--config-dir", str(config_dir)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=_server_env(config_dir),
        )
        self._lines: queue.Queue[str] = queue.Queue()
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()
        self._next_id = 0

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self._lines.put(line)

    def send(self, payload: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        req_id = self._next_id
        self.send(
            {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
        )
        return self._await_id(req_id)

    def notify(self, method: str, params: dict | None = None) -> None:
        self.send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _await_id(self, req_id: int, timeout: float = 30.0) -> dict:
        deadline_lines = []
        while True:
            try:
                line = self._lines.get(timeout=timeout)
            except queue.Empty:
                raise AssertionError(
                    f"no response for id {req_id}; got: {deadline_lines}"
                ) from None
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") == req_id:
                return message
            deadline_lines.append(message)

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            self.proc.kill()


@pytest.fixture
def mcp_client(tmp_path):
    client = _StdioClient(tmp_path / "cfg")
    try:
        yield client
    finally:
        client.close()


def _call_tool(client: _StdioClient, name: str, arguments: dict) -> dict:
    message = client.request(
        "tools/call", {"name": name, "arguments": arguments}
    )
    assert "error" not in message, message.get("error")
    result = message["result"]
    if result.get("isError"):
        raise AssertionError(f"tool {name} errored: {result}")
    return result


def _structured(result: dict) -> dict:
    """Return the structured payload, whichever channel the SDK used."""
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    for block in result.get("content", []):
        if block.get("type") == "text":
            return json.loads(block["text"])
    raise AssertionError(f"no structured payload in {result}")


def test_stdio_handshake_lists_tools_and_calls_list_providers(mcp_client):
    from mcp.types import LATEST_PROTOCOL_VERSION

    init = mcp_client.request(
        "initialize",
        {
            "protocolVersion": LATEST_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"},
        },
    )
    assert init["result"]["serverInfo"]["name"] == "pdfxlate"
    assert "tools" in init["result"]["capabilities"]

    mcp_client.notify("notifications/initialized")

    tools = mcp_client.request("tools/list")["result"]["tools"]
    names = {tool["name"] for tool in tools}
    assert {
        "list_providers",
        "extract_segments",
        "typeset_translations",
        "translate_pdf",
    } <= names

    payload = _structured(_call_tool(mcp_client, "list_providers", {}))
    assert "providers" in payload


def test_list_providers_never_leaks_the_api_key(tmp_path, monkeypatch):
    """The one config-facing tool must report key presence, never the key."""
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(
        "providers:\n"
        "  p:\n"
        "    kind: openai_compat\n"
        "    base_url: https://example.invalid/v1\n"
        "    api_key: super-secret-key\n"
        "    models:\n"
        "      chat: {model: m, temperature: 0.2}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PDFXLATE_CONFIG_DIR", str(cfg_dir))

    from pdfxlate import mcp_server

    server = mcp_server.build_server()
    tool = server._tool_manager.get_tool("list_providers")
    payload = tool.fn()

    provider = payload["providers"]["p"]
    assert provider["has_api_key"] is True
    assert provider["base_url"] == "https://example.invalid/v1"
    assert provider["models"]["chat"]["model"] == "m"
    assert provider["models"]["chat"]["temperature"] == 0.2
    # The key itself must not appear anywhere in the serialised payload.
    assert "super-secret-key" not in json.dumps(payload)


def test_unknown_provider_surfaces_config_message(mcp_client):
    mcp_client.request(
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"},
        },
    )
    mcp_client.notify("notifications/initialized")
    message = mcp_client.request(
        "tools/call",
        {
            "name": "translate_pdf",
            "arguments": {
                "file": __file__,
                "provider": "does-not-exist",
                "translator": "provider",
            },
        },
    )
    assert "error" not in message, message.get("error")
    result = message["result"]
    assert result["isError"] is True
    # resolve() raises a precise ValueError; the tool must surface it as is_error.
    text = json.dumps(result)
    assert "Unknown provider" in text and "does-not-exist" in text


def test_client_translator_requires_sampling_capability(mcp_client):
    """A client that never declared sampling must get a clear error, not a hang."""
    mcp_client.request(
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},  # no sampling
            "clientInfo": {"name": "pytest", "version": "0"},
        },
    )
    mcp_client.notify("notifications/initialized")
    message = mcp_client.request(
        "tools/call",
        {
            "name": "translate_pdf",
            "arguments": {"file": __file__, "translator": "client"},
        },
    )
    result = message["result"]
    assert result["isError"] is True
    assert "sampling" in json.dumps(result).lower()


# --- sampling batching -----------------------------------------------------


class _FakeSession:
    """Stands in for ``ctx.session``: returns a JSON array echoing its input."""

    def __init__(self) -> None:
        self.calls = 0
        self.max_tokens_seen: list[int] = []

    async def create_message(self, messages, *, max_tokens, **kwargs):
        from mcp.types import TextContent

        self.calls += 1
        self.max_tokens_seen.append(max_tokens)
        prompt = messages[0].content.text
        # Echo each source string with a marker so the mapping is observable.
        array_text = prompt.split("\n\n", 1)[1]
        items = json.loads(array_text)

        class _Result:
            content = TextContent(type="text", text=json.dumps([f"<{i}>" for i in items]))

        return _Result()


class _FakeContext:
    def __init__(self) -> None:
        self.session = _FakeSession()
        self.progress: list[tuple] = []

    async def report_progress(self, progress, total=None, message=None):
        self.progress.append((progress, total, message))


def test_sampling_batches_multiple_paragraphs_into_one_call():
    import anyio

    from pdfxlate import mcp_server

    texts = [f"p{i}" for i in range(mcp_server.SAMPLING_BATCH_SIZE * 2 + 3)]
    ctx = _FakeContext()

    async def run():
        return await mcp_server._translate_via_sampling(ctx, texts, "zh")

    translated = anyio.run(run)
    assert translated == [f"<{t}>" for t in texts]
    # Two full batches plus a remainder.
    assert ctx.session.calls == 3
    # A larger batch is budgeted more tokens than a one-item request.
    assert max(ctx.session.max_tokens_seen) > mcp_server._SAMPLING_MIN_TOKENS
    # Progress is reported once per batch.
    assert len(ctx.progress) == 3


def test_sampling_falls_back_to_per_paragraph_on_bad_batch():
    import anyio

    from pdfxlate import mcp_server
    from mcp.types import TextContent

    class _BadBatchSession(_FakeSession):
        async def create_message(self, messages, *, max_tokens, **kwargs):
            self.calls += 1
            prompt = messages[0].content.text
            if prompt.count("\n\n") and prompt.split("\n\n", 1)[1].strip().startswith("["):
                body = prompt.split("\n\n", 1)[1].strip()
                if body.startswith("[") and "p1" in body:
                    # Malformed response for the batch.
                    class _R:
                        content = TextContent(type="text", text="not json")

                    return _R()
            class _R:
                content = TextContent(type="text", text="SINGLE")

            return _R()

    ctx = _FakeContext()
    ctx.session = _BadBatchSession()

    async def run():
        return await mcp_server._translate_via_sampling(ctx, ["p1", "p2"], "zh")

    translated = anyio.run(run)
    assert translated == ["SINGLE", "SINGLE"]
    # One failed batch + two per-paragraph retries.
    assert ctx.session.calls == 3


# --- offline end-to-end two-phase engine ----------------------------------


def _find_sample_pdf() -> Path | None:
    # Relative to the test file, so the path resolves on any machine.
    return _WORKTREE_ROOT / "references/PDFMathTranslate/test/file/translate.cli.plain.text.pdf"


def _asset_ready(cfg: config_mod.AppConfig) -> bool:
    """True when the layout model and target font are already on disk."""
    if not cfg.layout.model_path:
        model = (
            paths.assets_dir()
            / "models"
            / "doclayout_yolo_docstructbench_imgsz1024.onnx"
        )
        if not model.exists():
            return False
    from pdfxlate.assets import font_asset

    return font_asset(cfg.translation.lang_out).target.exists()


_SAMPLE_PDF = _find_sample_pdf()
# A default config keeps the offline test off the user's real config file (and
# off any network download it might name).  The layout model and font still
# resolve through the shared assets directory, which is what we want.
_CFG = config_mod.AppConfig()
_OFFLINE_READY = _SAMPLE_PDF is not None and _asset_ready(_CFG)

_offline = pytest.mark.skipif(
    not _OFFLINE_READY,
    reason="sample PDF or layout model/font missing; offline end-to-end skipped",
)


@pytest.fixture(scope="module")
def collected(tmp_path_factory):
    """One real collection pass over the sample PDF, shared by the offline tests."""
    if not _OFFLINE_READY:
        pytest.skip("offline end-to-end prerequisites missing")
    workdir = tmp_path_factory.mktemp("offline")
    pdf = workdir / "sample.pdf"
    pdf.write_bytes(_SAMPLE_PDF.read_bytes())
    collection = two_phase.collect(str(pdf), _CFG, mode="fast")
    return collection


@_offline
def test_offline_two_phase_produces_mono_and_dual(collected, tmp_path):
    assert collected.texts, "the sample PDF should yield at least one paragraph"
    translations = {seg["id"]: seg["text"] for seg in collected.segments}

    mono, dual = two_phase.typeset(collected, translations, _CFG)
    assert mono[:5] == b"%PDF-"
    assert dual[:5] == b"%PDF-"
    assert len(mono) > 1000 and len(dual) > len(mono)

    mono_path, dual_path = two_phase.write_outputs(collected.file, mono, dual)
    assert Path(mono_path).exists() and Path(dual_path).exists()

    import pymupdf

    assert pymupdf.open(mono_path).page_count == 1
    assert pymupdf.open(dual_path).page_count == 2


@_offline
def test_offline_determinism_guard_fires(collected):
    """A collection whose fingerprint no longer matches pass 2 must abort."""
    tampered = two_phase.Collection(
        file=collected.file,
        pages=collected.pages,
        mode=collected.mode,
        texts=collected.texts,
        fingerprint="0" * 64,
    )
    translations = {seg["id"]: seg["text"] for seg in collected.segments}
    with pytest.raises(two_phase.DeterminismError):
        two_phase.typeset(tampered, translations, _CFG)


@_offline
def test_offline_typeset_rejects_missing_translation(collected):
    with pytest.raises(two_phase.SegmentMappingError, match="missing translations"):
        two_phase.typeset(collected, {}, _CFG)


@_offline
def test_extract_and_typeset_over_stdio(tmp_path):
    """The two tools round-trip over the real wire: collect, then typeset."""
    pdf = tmp_path / "wire.pdf"
    pdf.write_bytes(_SAMPLE_PDF.read_bytes())

    client = _StdioClient(tmp_path / "cfg")
    try:
        client.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        )
        client.notify("notifications/initialized")

        extracted = _structured(
            _call_tool(client, "extract_segments", {"file": str(pdf), "mode": "fast"})
        )
        assert extracted["job_id"]
        segments = extracted["segments"]
        assert segments and all("id" in s and "text" in s for s in segments)

        # Identity translations so the run needs no model API.
        translations = {s["id"]: s["text"] for s in segments}
        typed = _structured(
            _call_tool(
                client,
                "typeset_translations",
                {"job_id": extracted["job_id"], "translations": translations},
            )
        )
        assert Path(typed["mono"]).exists()
        assert Path(typed["dual"]).exists()

        # A typeset whose ids do not match the job must fail, not write a PDF.
        bad = client.request(
            "tools/call",
            {
                "name": "typeset_translations",
                "arguments": {
                    "job_id": extracted["job_id"],
                    "translations": {"nope": "x"},
                },
            },
        )["result"]
        assert bad.get("isError") is True
    finally:
        client.close()
