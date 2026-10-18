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
    context_length = None
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


def test_cache_stats_count_hits_and_misses(temp_cache):
    cache_mod.snapshot_stats(reset=True)
    adapter = CountingAdapter(FakeProfile(), "en", "zh")
    adapter.translate("hello")  # miss
    adapter.translate("hello")  # hit
    assert cache_mod.snapshot_stats(reset=True) == {"hits": 1, "misses": 1}


def test_cache_stats_disabled_cache_is_not_counted(temp_cache):
    """A switched-off cache performs no lookup, so it is neither hit nor miss."""
    cache_mod.snapshot_stats(reset=True)
    disabled = build_adapter(
        "counting_test", FakeProfile(), "en", "zh", cache_enabled=False
    )
    disabled.translate("hello")
    disabled.translate("hello")
    assert cache_mod.snapshot_stats(reset=True) == {"hits": 0, "misses": 0}


def test_cache_stats_ignore_cache_is_not_counted(temp_cache):
    adapter = CountingAdapter(FakeProfile(), "en", "zh")
    adapter.translate("hello")
    cache_mod.snapshot_stats(reset=True)
    adapter.translate("hello", ignore_cache=True)
    assert cache_mod.snapshot_stats(reset=True) == {"hits": 0, "misses": 0}


def test_default_prompt_stays_short_for_cost():
    """Do not "optimise" the prompt by lengthening its fixed prefix.

    The gateway measured for this project never caches the first prefix block
    and only starts reporting hits at ~256 prompt tokens.  A longer template
    therefore adds uncached tokens faster than the cache saves them; the short
    template is the cheapest option.  The local sqlite cache is where the real
    savings come from.
    """
    from pdfxlate.models.base import DEFAULT_PROMPT

    prefix, separator, tail = DEFAULT_PROMPT.partition("${text}")
    assert separator, "the default prompt must contain ${text}"
    assert len(prefix) < 400, "a longer prefix costs more than the cache saves"
    assert "${text}" not in tail, "the variable text must appear exactly once"


def test_default_prompt_puts_the_paragraph_last():
    from pdfxlate.models.base import DEFAULT_PROMPT

    adapter = CountingAdapter(FakeProfile(), "en", "zh")
    content = adapter.build_messages("UNIQUE-PARAGRAPH")[0]["content"]
    assert content.endswith("Translated Text:")
    # The variable part is at the very end, after the stable instruction block.
    assert content.index("UNIQUE-PARAGRAPH") > len(DEFAULT_PROMPT) * 0.7


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


def test_registry_has_every_shipped_protocol():
    """The three kinds the settings dropdown offers must all resolve."""
    assert {"openai_compat", "openai_responses", "anthropic_messages"} <= set(registered())
    for kind in ("openai_compat", "openai_responses", "anthropic_messages"):
        assert get_adapter(kind).name == kind


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


def test_over_context_warns_without_changing_the_request(temp_cache, caplog):
    """A paragraph too big for the window is reported, not silently rewritten.

    The warning exists so a mis-set `context_length` is diagnosable; the
    request itself is left alone, because inventing a `max_tokens` would change
    what the model returns and could truncate the translation.
    """
    class SmallWindow(FakeProfile):
        context_length = 10

    adapter = CountingAdapter(SmallWindow(), "en", "zh")
    with caplog.at_level("WARNING", logger="pdfxlate.models.base"):
        result = adapter.translate("x" * 4000)
    assert "context_length" in caplog.text
    # The translation still happened, in full.
    assert result.startswith("x" * 10)


def test_over_context_warns_only_once(temp_cache, caplog):
    """A long document must not emit one identical line per paragraph."""
    class SmallWindow(FakeProfile):
        context_length = 10

    adapter = CountingAdapter(SmallWindow(), "en", "zh")
    with caplog.at_level("WARNING", logger="pdfxlate.models.base"):
        for i in range(5):
            adapter.translate("y" * 4000 + str(i))
    over = [r for r in caplog.records if "over the configured" in r.getMessage()]
    assert len(over) == 1
    # Each paragraph really was translated, so the flag is suppressing the
    # message rather than the work.
    assert adapter.calls == 5


def test_within_context_does_not_warn(temp_cache, caplog):
    class Roomie(FakeProfile):
        context_length = 100000

    adapter = CountingAdapter(Roomie(), "en", "zh")
    with caplog.at_level("WARNING", logger="pdfxlate.models.base"):
        adapter.translate("short paragraph")
    assert caplog.text == ""
