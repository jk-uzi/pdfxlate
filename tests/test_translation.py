"""Tests for the translation layer: caching, placeholders, reasoning stripping."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdfxlate import cache as cache_mod  # noqa: E402
from pdfxlate.models.base import (  # noqa: E402
    ModelAdapter,
    PlaceholderLossError,
    build_adapter,
    get_adapter,
    register,
    registered,
    strip_reasoning,
)


class FakeProfile:
    model = "test-model"
    temperature = 0.0
    top_p = None
    max_tokens = None
    reasoning_effort = None
    extra_body = {}
    base_url = None
    api_key = None


@register
class CountingAdapter(ModelAdapter):
    """Returns an incrementing counter so cache hits are observable."""

    name = "counting_test"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls = 0

    def do_translate(self, text: str) -> str:
        self.calls += 1
        return f"{text}#{self.calls}"


@register
class DroppingAdapter(ModelAdapter):
    """Deliberately drops placeholders, to exercise the guard."""

    name = "dropping_test"

    def do_translate(self, text: str) -> str:
        return "translated"


@pytest.fixture
def temp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_mod.paths, "cache_file", lambda: tmp_path / "c.db")
    yield tmp_path


def test_cache_hit_avoids_second_call(temp_cache):
    adapter = CountingAdapter(FakeProfile(), "en", "zh")
    first = adapter.translate("hello")
    second = adapter.translate("hello")
    assert first == second
    assert adapter.calls == 1


def test_ignore_cache_forces_new_call(temp_cache):
    adapter = CountingAdapter(FakeProfile(), "en", "zh")
    adapter.translate("hello")
    adapter.translate("hello", ignore_cache=True)
    assert adapter.calls == 2


def test_no_cache_flag_bypasses_cache(temp_cache):
    adapter = CountingAdapter(FakeProfile(), "en", "zh")
    adapter.no_cache = True
    adapter.translate("hello")
    adapter.translate("hello")
    assert adapter.calls == 2


def test_cache_key_separates_models(temp_cache):
    a = CountingAdapter(FakeProfile(), "en", "zh")
    a.translate("hi")
    other = FakeProfile()
    other.model = "different-model"
    b = CountingAdapter(other, "en", "zh")
    b.translate("hi")
    # A different model must not reuse the first model's cached translation.
    assert b.calls == 1


def test_build_adapter_cache_enabled_by_default(temp_cache):
    adapter = build_adapter("counting_test", FakeProfile(), "en", "zh")
    assert adapter.cache.enabled is True


def test_build_adapter_disabled_cache_neither_reads_nor_writes(temp_cache):
    """cache_enabled=False must bypass both cache reads and writes."""
    disabled = build_adapter(
        "counting_test", FakeProfile(), "en", "zh", cache_enabled=False
    )
    assert disabled.cache.enabled is False

    # No reads: two identical calls both hit the backend.
    disabled.translate("hello")
    disabled.translate("hello")
    assert disabled.calls == 2

    # No writes: a fresh adapter with the cache on must not see the above
    # translations, so its first call goes to the backend too.
    enabled = build_adapter("counting_test", FakeProfile(), "en", "zh")
    enabled.translate("hello")
    assert enabled.calls == 1


def test_build_adapter_disabled_cache_creates_no_file(temp_cache):
    """Disabling the cache must not leave a database behind on disk.

    TranslationCache opens and initialises its sqlite file in __init__, so the
    flag has to reach it at construction time; disabling the adapter afterwards
    would still create the file (and make a read-only cache dir fatal).
    """
    db = cache_mod.paths.cache_file()
    assert not db.exists()
    build_adapter("counting_test", FakeProfile(), "en", "zh", cache_enabled=False)
    assert not db.exists()


def test_placeholder_preserved_passes(temp_cache):
    adapter = CountingAdapter(FakeProfile(), "en", "zh")
    out = adapter.translate("See {v0} and {v1}.")
    assert "{v0}" in out and "{v1}" in out


def test_placeholder_loss_raises(temp_cache):
    adapter = DroppingAdapter(FakeProfile(), "en", "zh")
    with pytest.raises(PlaceholderLossError) as exc:
        adapter.translate("See {v0} for details.")
    assert exc.value.missing == {"0"}


def test_no_placeholder_in_source_is_fine(temp_cache):
    adapter = DroppingAdapter(FakeProfile(), "en", "zh")
    assert adapter.translate("plain text") == "translated"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("<think>hmm</think>answer", "answer"),
        ("<thinking>a\nb</thinking>answer", "answer"),
        ("reasoning here</think>answer", "answer"),
        ("just an answer", "just an answer"),
        ("<THINK>x</THINK>Answer", "Answer"),
    ],
)
def test_strip_reasoning(raw, expected):
    assert strip_reasoning(raw) == expected


def test_registry_has_openai_compat():
    assert "openai_compat" in registered()
    assert get_adapter("openai_compat").name == "openai_compat"


def test_unknown_provider_error_lists_known():
    with pytest.raises(ValueError, match="openai_compat"):
        get_adapter("nope")


def test_duplicate_registration_rejected():
    with pytest.raises(ValueError, match="already registered"):

        @register
        class Dup(ModelAdapter):
            name = "counting_test"

            def do_translate(self, text):  # pragma: no cover
                return text
