"""The Responses adapter maps the same config knobs onto a different API shape.

Asserted on the arguments handed to the OpenAI SDK, so no network and no key.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdfxlate.models.openai_responses import OpenAIResponsesAdapter  # noqa: E402


class Profile:
    def __init__(self, **kw):
        self.provider = "openai_responses"
        self.base_url = "https://example.invalid/v1"
        self.api_key = "test-key"
        self.model = "test-model"
        self.temperature = 0.0
        self.top_p = None
        self.max_tokens = None
        self.stream = False
        self.reasoning_effort = None
        self.extra_body = {}
        for k, v in kw.items():
            setattr(self, k, v)


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    from pdfxlate import cache as cache_mod

    monkeypatch.setattr(cache_mod.paths, "cache_file", lambda: tmp_path / "c.db")
    yield


def _capture_create(adapter, text="hello"):
    response = mock.Mock()
    response.output_text = "translated"
    with mock.patch.object(adapter.client.responses, "create", return_value=response) as create:
        adapter.do_translate(text)
    return create.call_args.kwargs


def test_prompt_goes_in_input_not_messages(isolated_cache):
    adapter = OpenAIResponsesAdapter(Profile(), "en", "zh")
    kwargs = _capture_create(adapter, "Source sentence.")
    assert "input" in kwargs
    assert "messages" not in kwargs
    assert "Source sentence." in kwargs["input"][0]["content"]


def test_max_tokens_maps_to_max_output_tokens(isolated_cache):
    adapter = OpenAIResponsesAdapter(Profile(max_tokens=4096), "en", "zh")
    kwargs = _capture_create(adapter)
    assert kwargs["max_output_tokens"] == 4096
    assert "max_tokens" not in kwargs


def test_reasoning_effort_is_nested(isolated_cache):
    adapter = OpenAIResponsesAdapter(Profile(reasoning_effort="high"), "en", "zh")
    kwargs = _capture_create(adapter)
    assert kwargs["reasoning"] == {"effort": "high"}
    # The chat-completions spelling would be rejected by this endpoint.
    assert "reasoning_effort" not in kwargs


def test_reasoning_omitted_when_unset(isolated_cache):
    adapter = OpenAIResponsesAdapter(Profile(), "en", "zh")
    kwargs = _capture_create(adapter)
    assert "reasoning" not in kwargs


def test_extra_body_forwarded(isolated_cache):
    adapter = OpenAIResponsesAdapter(Profile(extra_body={"service_tier": "flex"}), "en", "zh")
    kwargs = _capture_create(adapter)
    assert kwargs["extra_body"] == {"service_tier": "flex"}


def test_temperature_and_top_p_forwarded(isolated_cache):
    adapter = OpenAIResponsesAdapter(Profile(temperature=0.2, top_p=0.9), "en", "zh")
    kwargs = _capture_create(adapter)
    assert kwargs["temperature"] == 0.2
    assert kwargs["top_p"] == 0.9


def test_output_text_is_used(isolated_cache):
    adapter = OpenAIResponsesAdapter(Profile(), "en", "zh")
    response = mock.Mock()
    response.output_text = "  译文  "
    with mock.patch.object(adapter.client.responses, "create", return_value=response):
        assert adapter.do_translate("hello") == "译文"


def test_empty_output_raises(isolated_cache):
    adapter = OpenAIResponsesAdapter(Profile(), "en", "zh")
    response = mock.Mock()
    response.output_text = ""
    with mock.patch.object(adapter.client.responses, "create", return_value=response):
        with pytest.raises(ValueError, match="empty response"):
            adapter.do_translate("hello")


def test_streaming_accumulates_text_deltas_only(isolated_cache):
    adapter = OpenAIResponsesAdapter(Profile(stream=True), "en", "zh")
    events = [
        mock.Mock(type="response.created"),
        mock.Mock(type="response.output_text.delta", delta="你"),
        mock.Mock(type="response.reasoning_summary_text.delta", delta="IGNORED"),
        mock.Mock(type="response.output_text.delta", delta="好"),
        mock.Mock(type="response.completed"),
    ]
    with mock.patch.object(
        adapter.client.responses, "create", return_value=iter(events)
    ) as create:
        assert adapter.do_translate("hello") == "你好"
    assert create.call_args.kwargs["stream"] is True
    assert "hello" in create.call_args.kwargs["input"][0]["content"]


def test_streaming_detects_incomplete(isolated_cache):
    from pdfxlate.models.base import TruncatedResponseError

    adapter = OpenAIResponsesAdapter(Profile(stream=True), "en", "zh")
    events = [
        mock.Mock(type="response.output_text.delta", delta="半句"),
        mock.Mock(type="response.incomplete"),
    ]
    with mock.patch.object(adapter.client.responses, "create", return_value=iter(events)):
        with pytest.raises(TruncatedResponseError):
            adapter.do_translate("hello")


def test_missing_base_url_raises(isolated_cache):
    with pytest.raises(ValueError, match="base_url"):
        OpenAIResponsesAdapter(Profile(base_url=None), "en", "zh")


def test_missing_api_key_raises(isolated_cache):
    with pytest.raises(ValueError, match="api_key"):
        OpenAIResponsesAdapter(Profile(api_key=""), "en", "zh")
