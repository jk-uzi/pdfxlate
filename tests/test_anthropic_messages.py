"""The Anthropic Messages adapter, exercised against a mocked HTTP session.

No network: every assertion is about the URL, headers, body or the parsing of
a canned response.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdfxlate.models.anthropic_messages import (  # noqa: E402
    DEFAULT_MAX_TOKENS,
    AnthropicMessagesAdapter,
    messages_url,
)


class Profile:
    def __init__(self, **kw):
        self.provider = "anthropic_messages"
        self.base_url = "https://api.anthropic.com"
        self.api_key = "test-key"
        self.model = "claude-test"
        self.temperature = 0.0
        self.top_p = None
        self.max_tokens = None
        self.stream = False
        self.reasoning_effort = None
        self.context_length = None
        self.extra_body = {}
        for k, v in kw.items():
            setattr(self, k, v)


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    from pdfxlate import cache as cache_mod

    monkeypatch.setattr(cache_mod.paths, "cache_file", lambda: tmp_path / "c.db")
    yield


def _response(payload, status=200):
    response = mock.Mock(spec=requests.Response)
    response.status_code = status
    response.json.return_value = payload
    response.text = json.dumps(payload)
    return response


# --- endpoint construction -------------------------------------------------


@pytest.mark.parametrize(
    "base, expected",
    [
        ("https://api.anthropic.com", "https://api.anthropic.com/v1/messages"),
        ("https://api.anthropic.com/", "https://api.anthropic.com/v1/messages"),
        ("https://api.anthropic.com/v1", "https://api.anthropic.com/v1/messages"),
        ("https://api.anthropic.com/v1/", "https://api.anthropic.com/v1/messages"),
        ("https://proxy.example.com/anthropic", "https://proxy.example.com/anthropic/v1/messages"),
    ],
)
def test_messages_url_never_doubles_the_version(base, expected):
    assert messages_url(base) == expected


# --- request shape ---------------------------------------------------------


def test_headers_carry_the_key_and_version(isolated_cache):
    adapter = AnthropicMessagesAdapter(Profile(), "en", "zh")
    assert adapter.session.headers["x-api-key"] == "test-key"
    assert adapter.session.headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in {k.lower() for k in adapter.session.headers}


def test_body_uses_messages_and_required_max_tokens(isolated_cache):
    adapter = AnthropicMessagesAdapter(Profile(max_tokens=2048), "en", "zh")
    response = _response({"content": [{"type": "text", "text": "译文"}]})
    with mock.patch.object(adapter.session, "post", return_value=response) as post:
        adapter.do_translate("Source sentence.")
    body = post.call_args.kwargs["json"]
    assert body["model"] == "claude-test"
    assert body["max_tokens"] == 2048
    assert body["messages"][0]["role"] == "user"
    assert "Source sentence." in body["messages"][0]["content"]
    assert post.call_args.kwargs["json"] is body


def test_max_tokens_falls_back_to_a_quarter_of_context(isolated_cache):
    adapter = AnthropicMessagesAdapter(Profile(context_length=32768), "en", "zh")
    assert adapter.max_tokens == 8192


def test_max_tokens_falls_back_to_a_constant(isolated_cache):
    adapter = AnthropicMessagesAdapter(Profile(), "en", "zh")
    assert adapter.max_tokens == DEFAULT_MAX_TOKENS


def test_explicit_max_tokens_beats_context_length(isolated_cache):
    adapter = AnthropicMessagesAdapter(
        Profile(max_tokens=512, context_length=32768), "en", "zh"
    )
    assert adapter.max_tokens == 512


def test_reasoning_effort_is_not_sent(isolated_cache):
    """Anthropic has no such field; guessing a budget would be worse than omitting."""
    adapter = AnthropicMessagesAdapter(Profile(reasoning_effort="high"), "en", "zh")
    body = adapter._body("x")
    assert "reasoning_effort" not in body
    assert "reasoning" not in body


def test_extra_body_is_merged_at_the_top_level(isolated_cache):
    adapter = AnthropicMessagesAdapter(
        Profile(extra_body={"thinking": {"type": "enabled", "budget_tokens": 1024}}), "en", "zh"
    )
    body = adapter._body("x")
    assert body["thinking"]["budget_tokens"] == 1024


def test_top_p_only_when_set(isolated_cache):
    assert "top_p" not in AnthropicMessagesAdapter(Profile(), "en", "zh")._body("x")
    assert AnthropicMessagesAdapter(Profile(top_p=0.5), "en", "zh")._body("x")["top_p"] == 0.5


# --- response parsing ------------------------------------------------------


def test_text_blocks_are_joined_and_others_ignored(isolated_cache):
    adapter = AnthropicMessagesAdapter(Profile(), "en", "zh")
    response = _response(
        {
            "content": [
                {"type": "thinking", "thinking": "IGNORED"},
                {"type": "text", "text": "你"},
                {"type": "text", "text": "好"},
            ]
        }
    )
    with mock.patch.object(adapter.session, "post", return_value=response):
        assert adapter.do_translate("hello") == "你好"


def test_streaming_reads_text_deltas_only(isolated_cache):
    adapter = AnthropicMessagesAdapter(Profile(stream=True), "en", "zh")
    lines = [
        b"event: message_start",
        b'data: {"type":"message_start"}',
        b"",
        b"event: content_block_delta",
        b'data: {"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":"NO"}}',
        b"",
        b"event: content_block_delta",
        b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"\xe4\xbd\xa0"}}',
        b"",
        b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"\xe5\xa5\xbd"}}',
        b"",
        b"event: message_stop",
        b'data: {"type":"message_stop"}',
    ]
    response = _response({})
    response.iter_lines.return_value = iter(lines)
    response.__enter__ = mock.Mock(return_value=response)
    response.__exit__ = mock.Mock(return_value=False)
    with mock.patch.object(adapter.session, "post", return_value=response) as post:
        assert adapter.do_translate("hello") == "你好"
    assert post.call_args.kwargs["json"]["stream"] is True


def test_malformed_sse_line_is_skipped(isolated_cache):
    adapter = AnthropicMessagesAdapter(Profile(stream=True), "en", "zh")
    response = _response({})
    response.iter_lines.return_value = iter(
        [
            b"data: not json",
            b": ping comment",
            b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"ok"}}',
        ]
    )
    response.__enter__ = mock.Mock(return_value=response)
    response.__exit__ = mock.Mock(return_value=False)
    with mock.patch.object(adapter.session, "post", return_value=response):
        assert adapter.do_translate("hello") == "ok"


# --- errors ----------------------------------------------------------------


def test_429_and_5xx_are_classified_as_transient(isolated_cache):
    """Retryable vs fatal is decided by status code, not by the message."""
    from pdfxlate.models.anthropic_messages import TransientApiError

    adapter = AnthropicMessagesAdapter(Profile(), "en", "zh")
    for status in (429, 500, 503):
        response = _response({"error": {"message": "slow down"}}, status=status)
        with pytest.raises(TransientApiError, match="slow down"):
            adapter._raise_for_status(response)


def test_client_error_is_not_retried(isolated_cache):
    adapter = AnthropicMessagesAdapter(Profile(), "en", "zh")
    response = _response({"error": {"message": "bad model"}}, status=400)
    with mock.patch.object(adapter.session, "post", return_value=response) as post:
        with pytest.raises(ValueError, match="bad model"):
            adapter.do_translate("hello")
    assert post.call_count == 1


def test_missing_base_url_raises(isolated_cache):
    with pytest.raises(ValueError, match="base_url"):
        AnthropicMessagesAdapter(Profile(base_url=None), "en", "zh")


def test_missing_api_key_raises(isolated_cache):
    with pytest.raises(ValueError, match="api_key"):
        AnthropicMessagesAdapter(Profile(api_key=""), "en", "zh")
