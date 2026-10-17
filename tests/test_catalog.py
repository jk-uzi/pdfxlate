"""Upstream model discovery (``GET .../models``) for the three API shapes."""

from __future__ import annotations

import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdfxlate.models import catalog  # noqa: E402


# --- URL construction ------------------------------------------------------


@pytest.mark.parametrize(
    "kind, base, expected",
    [
        ("openai_compat", "https://api.openai.com/v1", "https://api.openai.com/v1/models"),
        ("openai_compat", "https://api.openai.com/v1/", "https://api.openai.com/v1/models"),
        ("openai_responses", "https://api.openai.com/v1", "https://api.openai.com/v1/models"),
        ("anthropic_messages", "https://api.anthropic.com", "https://api.anthropic.com/v1/models"),
        ("anthropic_messages", "https://api.anthropic.com/v1", "https://api.anthropic.com/v1/models"),
        ("anthropic_messages", "https://api.anthropic.com/v1/", "https://api.anthropic.com/v1/models"),
        ("anthropic_messages", "https://proxy.example/anthropic", "https://proxy.example/anthropic/v1/models"),
    ],
)
def test_models_url(kind, base, expected):
    assert catalog.models_url(kind, base) == expected


# --- a real local server ---------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    # Set per test.
    payload = {"data": [{"id": "b-model"}, {"id": "a-model"}]}
    status = 200
    seen: list[dict] = []

    def log_message(self, *args):
        return

    def do_GET(self):  # noqa: N802 - stdlib naming
        _Handler.seen.append({"path": self.path, "headers": dict(self.headers)})
        body = __import__("json").dumps(_Handler.payload).encode("utf-8")
        self.send_response(_Handler.status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def server():
    _Handler.seen = []
    _Handler.payload = {"data": [{"id": "b-model"}, {"id": "a-model"}]}
    _Handler.status = 200
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_lists_ids_sorted_and_deduplicated(server):
    _Handler.payload = {"data": [{"id": "b"}, {"id": "a"}, {"id": "b"}]}
    assert catalog.list_models("openai_compat", server + "/v1", "k") == ["a", "b"]


def test_accepts_a_bare_array(server):
    _Handler.payload = [{"id": "solo"}]
    assert catalog.list_models("openai_compat", server + "/v1", "k") == ["solo"]


def test_entries_without_an_id_are_skipped(server):
    _Handler.payload = {"data": [{"id": "ok"}, {"nope": 1}, "junk"]}
    assert catalog.list_models("openai_compat", server + "/v1", "k") == ["ok"]


def test_openai_uses_bearer_auth(server):
    catalog.list_models("openai_compat", server + "/v1", "secret")
    headers = {k.lower(): v for k, v in _Handler.seen[-1]["headers"].items()}
    assert headers["authorization"] == "Bearer secret"


def test_anthropic_uses_key_and_version_headers(server):
    _Handler.payload = {"data": [{"id": "claude"}]}
    catalog.list_models("anthropic_messages", server, "secret")
    call = _Handler.seen[-1]
    headers = {k.lower(): v for k, v in call["headers"].items()}
    assert call["path"].startswith("/v1/models")
    assert headers["x-api-key"] == "secret"
    assert headers["anthropic-version"] == "2023-06-01"


def test_http_error_names_the_status_and_url(server):
    _Handler.status = 404
    _Handler.payload = {"error": {"message": "no such route"}}
    with pytest.raises(catalog.CatalogError, match="HTTP 404.*no such route"):
        catalog.list_models("openai_compat", server + "/v1", "k")


def test_unreachable_host_is_reported(monkeypatch):
    with pytest.raises(catalog.CatalogError, match="could not reach"):
        catalog.list_models("openai_compat", "http://127.0.0.1:1/v1", "k", timeout=1)


def test_missing_key_is_rejected_before_the_request():
    with pytest.raises(catalog.CatalogError, match="api_key"):
        catalog.list_models("openai_compat", "https://example.invalid/v1", "")


def test_missing_base_url_is_rejected():
    with pytest.raises(catalog.CatalogError, match="base_url"):
        catalog.list_models("openai_compat", "", "k")


def test_non_json_response_is_reported(server):
    _Handler.payload = "not a list"
    assert catalog.list_models("openai_compat", server + "/v1", "k") == []


def test_anthropic_pagination_follows_has_more(monkeypatch):
    """Two pages must both be read, and the cursor passed on."""
    pages = [
        {"data": [{"id": "a"}], "has_more": True, "last_id": "a"},
        {"data": [{"id": "b"}], "has_more": False},
    ]
    calls: list[dict] = []

    class Response:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append(params or {})
        return Response(pages[len(calls) - 1])

    monkeypatch.setattr(catalog.requests, "get", fake_get)
    assert catalog.list_models("anthropic_messages", "https://x.test", "k") == ["a", "b"]
    assert calls[0].get("after_id") is None
    assert calls[1]["after_id"] == "a"


def test_anthropic_pagination_stops_on_a_repeated_cursor(monkeypatch):
    """A server that never advances must not loop ten times."""
    calls = []

    class Response:
        status_code = 200

        def json(self):
            return {"data": [{"id": "a"}], "has_more": True, "last_id": "a"}

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append(params)
        return Response()

    monkeypatch.setattr(catalog.requests, "get", fake_get)
    assert catalog.list_models("anthropic_messages", "https://x.test", "k") == ["a"]
    assert len(calls) == 2  # first page, then the cursor repeats and it stops
