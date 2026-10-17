"""Wire-level tests: the three adapters against a real local HTTP server.

Every other adapter test mocks an SDK object, which proves the argument
mapping but not that the bytes on the wire are what the service expects.  This
file starts a real ``ThreadingHTTPServer`` that speaks all three shapes and
drives the adapters through a real socket: URL joining, headers, body fields,
response parsing, SSE handling and truncation detection are all exercised for
real.  No API key and no internet access is involved.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdfxlate.models.anthropic_messages import AnthropicMessagesAdapter  # noqa: E402
from pdfxlate.models.base import TruncatedResponseError  # noqa: E402
from pdfxlate.models.openai_compat import OpenAICompatAdapter  # noqa: E402
from pdfxlate.models.openai_responses import OpenAIResponsesAdapter  # noqa: E402


class Profile:
    def __init__(self, **kw):
        self.provider = "test"
        self.base_url = "http://127.0.0.1"
        self.api_key = "test-key"
        self.model = "test-model"
        self.temperature = 0.0
        self.top_p = None
        self.max_tokens = None
        self.stream = False
        self.reasoning_effort = None
        self.context_length = None
        self.extra_body = {}
        self.enabled = True
        for k, v in kw.items():
            setattr(self, k, v)


class _Handler(BaseHTTPRequestHandler):
    """Speaks chat/completions, /responses and /v1/messages."""

    # Populated per test.
    server_response = "译文"
    server_finish = "stop"
    captured: list[dict] = []

    def log_message(self, *args):  # keep pytest output clean
        return

    def _send_json(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_sse(self, events: list[str]) -> None:
        payload = "".join(f"data: {e}\n\n" for e in events).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):  # noqa: N802 - stdlib naming
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length)
        body = json.loads(raw) if raw else {}
        # Header names are case-insensitive on the wire; normalize so the
        # assertions below do not depend on which client sent the request.
        headers = {k.lower(): v for k, v in self.headers.items()}
        _Handler.captured.append({"path": self.path, "headers": headers, "body": body})
        text = _Handler.server_response
        finish = _Handler.server_finish

        if self.path.endswith("/chat/completions"):
            if body.get("stream"):
                self._send_sse(
                    [
                        json.dumps({"choices": [{"delta": {"content": text}, "finish_reason": None}]}),
                        json.dumps({"choices": [{"delta": {}, "finish_reason": finish}]}),
                    ]
                )
            else:
                self._send_json(
                    {
                        "id": "x",
                        "object": "chat.completion",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": text},
                                "finish_reason": finish,
                            }
                        ],
                    }
                )
            return

        if self.path.endswith("/responses"):
            status = "incomplete" if finish == "length" else "completed"
            if body.get("stream"):
                events = [
                    json.dumps({"type": "response.output_text.delta", "delta": text}),
                ]
                if finish == "length":
                    events.append(json.dumps({"type": "response.incomplete"}))
                else:
                    events.append(json.dumps({"type": "response.completed"}))
                self._send_sse(events)
            else:
                self._send_json(
                    {
                        "id": "x",
                        "object": "response",
                        "status": status,
                        "incomplete_details": (
                            {"reason": "max_output_tokens"} if finish == "length" else None
                        ),
                        "output": [
                            {
                                "type": "message",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": text}],
                            }
                        ],
                    }
                )
            return

        if self.path.endswith("/messages"):
            stop_reason = "max_tokens" if finish == "length" else "end_turn"
            if body.get("stream"):
                self._send_sse(
                    [
                        json.dumps(
                            {
                                "type": "content_block_delta",
                                "delta": {"type": "text_delta", "text": text},
                            }
                        ),
                        json.dumps(
                            {
                                "type": "message_delta",
                                "delta": {"stop_reason": stop_reason},
                            }
                        ),
                    ]
                )
            else:
                self._send_json(
                    {
                        "id": "x",
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "text", "text": text}],
                        "stop_reason": stop_reason,
                    }
                )
            return

        self.send_response(404)
        self.end_headers()


@pytest.fixture(scope="module")
def server():
    _Handler.captured = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture(autouse=True)
def reset_capture():
    _Handler.captured = []
    _Handler.server_response = "译文"
    _Handler.server_finish = "stop"
    yield


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    from pdfxlate import cache as cache_mod

    monkeypatch.setattr(cache_mod.paths, "cache_file", lambda: tmp_path / "c.db")
    yield


# --- chat completions ------------------------------------------------------


def test_chat_completions_round_trip(server, isolated_cache):
    adapter = OpenAICompatAdapter(Profile(base_url=server + "/v1"), "en", "zh")
    assert adapter.translate("Hello.") == "译文"

    call = _Handler.captured[-1]
    assert call["path"] == "/v1/chat/completions"
    assert call["headers"]["authorization"] == "Bearer test-key"
    assert call["body"]["model"] == "test-model"
    assert call["body"]["temperature"] == 0.0
    assert "Hello." in call["body"]["messages"][0]["content"]


def test_chat_completions_streaming(server, isolated_cache):
    _Handler.server_response = "流式"
    adapter = OpenAICompatAdapter(Profile(base_url=server + "/v1", stream=True), "en", "zh")
    assert adapter.do_translate("Hello.") == "流式"
    assert _Handler.captured[-1]["body"]["stream"] is True


def test_chat_completions_detects_truncation(server, isolated_cache):
    _Handler.server_finish = "length"
    adapter = OpenAICompatAdapter(Profile(base_url=server + "/v1"), "en", "zh")
    with pytest.raises(TruncatedResponseError):
        adapter.do_translate("Hello.")


def test_chat_completions_omits_temperature_when_unset(server, isolated_cache):
    adapter = OpenAICompatAdapter(Profile(base_url=server + "/v1", temperature=None), "en", "zh")
    adapter.do_translate("Hello.")
    assert "temperature" not in _Handler.captured[-1]["body"]


# --- responses -------------------------------------------------------------


def test_responses_round_trip(server, isolated_cache):
    adapter = OpenAIResponsesAdapter(Profile(base_url=server + "/v1"), "en", "zh")
    assert adapter.translate("Hello.") == "译文"

    call = _Handler.captured[-1]
    assert call["path"] == "/v1/responses"
    assert call["headers"]["authorization"] == "Bearer test-key"
    assert "input" in call["body"]
    assert "messages" not in call["body"]
    assert "Hello." in call["body"]["input"][0]["content"]


def test_responses_streaming(server, isolated_cache):
    _Handler.server_response = "流式"
    adapter = OpenAIResponsesAdapter(Profile(base_url=server + "/v1", stream=True), "en", "zh")
    assert adapter.do_translate("Hello.") == "流式"


def test_responses_detects_truncation(server, isolated_cache):
    _Handler.server_finish = "length"
    adapter = OpenAIResponsesAdapter(Profile(base_url=server + "/v1"), "en", "zh")
    with pytest.raises(TruncatedResponseError):
        adapter.do_translate("Hello.")


def test_responses_stream_detects_truncation(server, isolated_cache):
    _Handler.server_finish = "length"
    adapter = OpenAIResponsesAdapter(Profile(base_url=server + "/v1", stream=True), "en", "zh")
    with pytest.raises(TruncatedResponseError):
        adapter.do_translate("Hello.")


def test_responses_maps_max_tokens(server, isolated_cache):
    adapter = OpenAIResponsesAdapter(
        Profile(base_url=server + "/v1", max_tokens=256), "en", "zh"
    )
    adapter.do_translate("Hello.")
    body = _Handler.captured[-1]["body"]
    assert body["max_output_tokens"] == 256
    assert "max_tokens" not in body


# --- anthropic messages ----------------------------------------------------


def test_anthropic_round_trip(server, isolated_cache):
    adapter = AnthropicMessagesAdapter(Profile(base_url=server), "en", "zh")
    assert adapter.translate("Hello.") == "译文"

    call = _Handler.captured[-1]
    assert call["path"] == "/v1/messages"
    assert call["headers"]["x-api-key"] == "test-key"
    assert call["headers"]["anthropic-version"] == "2023-06-01"
    assert call["body"]["max_tokens"] > 0
    assert "Hello." in call["body"]["messages"][0]["content"]


def test_anthropic_does_not_double_v1(server, isolated_cache):
    adapter = AnthropicMessagesAdapter(Profile(base_url=server + "/v1"), "en", "zh")
    adapter.do_translate("Hello.")
    assert _Handler.captured[-1]["path"] == "/v1/messages"


def test_anthropic_streaming(server, isolated_cache):
    _Handler.server_response = "流式"
    adapter = AnthropicMessagesAdapter(Profile(base_url=server, stream=True), "en", "zh")
    assert adapter.do_translate("Hello.") == "流式"
    assert _Handler.captured[-1]["body"]["stream"] is True


def test_anthropic_detects_truncation(server, isolated_cache):
    _Handler.server_finish = "length"
    adapter = AnthropicMessagesAdapter(Profile(base_url=server), "en", "zh")
    with pytest.raises(TruncatedResponseError):
        adapter.do_translate("Hello.")


def test_anthropic_stream_detects_truncation(server, isolated_cache):
    _Handler.server_finish = "length"
    adapter = AnthropicMessagesAdapter(Profile(base_url=server, stream=True), "en", "zh")
    with pytest.raises(TruncatedResponseError):
        adapter.do_translate("Hello.")


def test_anthropic_omits_temperature_when_unset(server, isolated_cache):
    adapter = AnthropicMessagesAdapter(Profile(base_url=server, temperature=None), "en", "zh")
    adapter.do_translate("Hello.")
    assert "temperature" not in _Handler.captured[-1]["body"]


def test_anthropic_uses_a_session_per_thread(server, isolated_cache):
    """Two threads must not share one requests.Session."""
    import threading as threading_mod

    adapter = AnthropicMessagesAdapter(Profile(base_url=server), "en", "zh")
    # Hold the sessions themselves: an id() can be reused once the object is
    # collected, which would make distinct sessions look identical.
    seen: list[object] = []

    def grab() -> None:
        seen.append(adapter.session)

    threads = [threading_mod.Thread(target=grab) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({id(s) for s in seen}) == len(seen)


# --- shared ----------------------------------------------------------------


def test_empty_response_is_rejected(server, isolated_cache):
    from pdfxlate.models.base import EmptyTranslationError

    _Handler.server_response = ""
    adapter = OpenAICompatAdapter(Profile(base_url=server + "/v1"), "en", "zh")
    with pytest.raises(EmptyTranslationError):
        adapter.translate("Hello.")
