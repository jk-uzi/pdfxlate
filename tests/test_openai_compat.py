"""Tests that the OpenAI-compatible adapter forwards every request knob.

These assert on the arguments handed to the OpenAI SDK, so they need no
network access and no API key.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdfxlate.models.openai_compat import OpenAICompatAdapter  # noqa: E402


class Profile:
    def __init__(self, **kw):
        self.provider = "openai_compat"
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


def _capture_create(adapter, text="hello"):
    """Call do_translate with the SDK patched, returning the kwargs used."""
    response = mock.Mock()
    response.choices = [mock.Mock()]
    response.choices[0].message.content = "translated"
    with mock.patch.object(
        adapter.client.chat.completions, "create", return_value=response
    ) as create:
        adapter.do_translate(text)
    return create.call_args.kwargs


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    from pdfxlate import cache as cache_mod

    monkeypatch.setattr(cache_mod.paths, "cache_file", lambda: tmp_path / "c.db")
    yield


def test_reasoning_effort_forwarded(isolated_cache):
    adapter = OpenAICompatAdapter(
        Profile(reasoning_effort="high"), "en", "zh"
    )
    kwargs = _capture_create(adapter)
    assert kwargs["reasoning_effort"] == "high"


def test_extra_body_forwarded(isolated_cache):
    adapter = OpenAICompatAdapter(
        Profile(extra_body={"chat_template_kwargs": {"enable_thinking": True}}),
        "en",
        "zh",
    )
    kwargs = _capture_create(adapter)
    assert kwargs["extra_body"] == {"chat_template_kwargs": {"enable_thinking": True}}


def test_extra_body_omitted_when_empty(isolated_cache):
    adapter = OpenAICompatAdapter(Profile(), "en", "zh")
    kwargs = _capture_create(adapter)
    assert "extra_body" not in kwargs


def test_reasoning_effort_omitted_when_none(isolated_cache):
    adapter = OpenAICompatAdapter(Profile(), "en", "zh")
    kwargs = _capture_create(adapter)
    assert "reasoning_effort" not in kwargs


def test_temperature_defaults_to_zero(isolated_cache):
    adapter = OpenAICompatAdapter(Profile(), "en", "zh")
    kwargs = _capture_create(adapter)
    # Non-zero temperature corrupts the {vN} placeholders, so 0 is the default.
    assert kwargs["temperature"] == 0.0


def test_top_p_and_max_tokens_optional(isolated_cache):
    adapter = OpenAICompatAdapter(Profile(top_p=0.9, max_tokens=4096), "en", "zh")
    kwargs = _capture_create(adapter)
    assert kwargs["top_p"] == 0.9
    assert kwargs["max_tokens"] == 4096


def test_base_url_trailing_slash_stripped(isolated_cache):
    from pdfxlate.config import Profile as ConfigProfile

    p = ConfigProfile(base_url="https://x.invalid/v1/", model="m", api_key="k")
    assert p.base_url == "https://x.invalid/v1"


def test_missing_base_url_raises(isolated_cache):
    with pytest.raises(ValueError, match="base_url"):
        OpenAICompatAdapter(Profile(base_url=None), "en", "zh")


def test_missing_api_key_raises(isolated_cache):
    with pytest.raises(ValueError, match="api_key"):
        OpenAICompatAdapter(Profile(api_key=None), "en", "zh")


def test_empty_env_var_key_is_rejected(isolated_cache):
    """An unset ${VAR} expands to '' and must be reported as missing."""
    with pytest.raises(ValueError, match="api_key"):
        OpenAICompatAdapter(Profile(api_key=""), "en", "zh")


def test_prompt_renders_language_and_text(isolated_cache):
    adapter = OpenAICompatAdapter(Profile(), "en", "zh")
    messages = adapter.build_messages("Source sentence.")
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert "Source sentence." in messages[0]["content"]
    assert "zh" in messages[0]["content"]


def test_custom_prompt_template(isolated_cache):
    adapter = OpenAICompatAdapter(
        Profile(), "en", "zh", prompt="Translate to ${lang_out}: ${text}"
    )
    messages = adapter.build_messages("abc")
    assert messages[0]["content"] == "Translate to zh: abc"


def _response_with_usage(usage):
    response = mock.Mock()
    response.choices = [mock.Mock()]
    response.choices[0].message.content = "translated"
    response.choices[0].finish_reason = "stop"
    response.usage = usage
    return response


def test_deepseek_usage_cache_fields_are_recorded(isolated_cache):
    from pdfxlate.models.base import provider_cache_snapshot

    usage = SimpleNamespace(
        prompt_tokens=564,
        prompt_cache_hit_tokens=384,
        prompt_cache_miss_tokens=180,
    )
    adapter = OpenAICompatAdapter(Profile(), "en", "zh")
    provider_cache_snapshot(reset=True)
    with mock.patch.object(
        adapter.client.chat.completions,
        "create",
        return_value=_response_with_usage(usage),
    ):
        adapter.do_translate("hello")
    assert provider_cache_snapshot(reset=False) == {
        "hit_tokens": 384,
        "miss_tokens": 180,
    }


def test_openai_cached_tokens_details_are_recorded(isolated_cache):
    from pdfxlate.models.base import provider_cache_snapshot

    usage = SimpleNamespace(
        prompt_tokens=500,
        prompt_tokens_details=SimpleNamespace(cached_tokens=384),
    )
    adapter = OpenAICompatAdapter(Profile(), "en", "zh")
    provider_cache_snapshot(reset=True)
    with mock.patch.object(
        adapter.client.chat.completions,
        "create",
        return_value=_response_with_usage(usage),
    ):
        adapter.do_translate("hello")
    # The miss count is the remainder of the prompt.
    assert provider_cache_snapshot(reset=False) == {
        "hit_tokens": 384,
        "miss_tokens": 116,
    }


def test_usage_without_cache_fields_is_ignored(isolated_cache):
    from pdfxlate.models.base import provider_cache_snapshot

    metric = mock.Mock()
    adapter = OpenAICompatAdapter(Profile(), "en", "zh")
    provider_cache_snapshot(reset=True)
    with mock.patch.object(
        adapter.client.chat.completions,
        "create",
        return_value=_response_with_usage(metric),
    ):
        adapter.do_translate("hello")
    assert provider_cache_snapshot(reset=False) == {
        "hit_tokens": 0,
        "miss_tokens": 0,
    }
