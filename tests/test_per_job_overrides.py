"""Per-job overrides for language and reasoning effort.

The settings panel owns the stored configuration; the options panel changes one
job and must leave those stored values alone.  So the two things worth testing
are that an override reaches the adapter, and that the config object the caller
passed in comes back untouched.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdfxlate import api as api_mod  # noqa: E402
from pdfxlate import config as config_mod  # noqa: E402


def _cfg(**translation) -> config_mod.AppConfig:
    """A minimal two-provider config, with optional translation overrides."""
    return config_mod.AppConfig(
        default_provider="prov",
        default_model="chat",
        providers={
            "prov": {
                "kind": "openai_compat",
                "base_url": "https://example.test/v1",
                "models": {"chat": {"model": "m", "reasoning_effort": "low"}},
            }
        },
        translation=translation or {},
    )


def _capture(monkeypatch, sink: dict) -> None:
    """Record the config handed to `build_translator` and stub the pipeline."""

    def fake_build_translator(provider=None, model=None, app_config=None, **kwargs):
        sink["config"] = app_config
        sink["reasoning_effort"] = kwargs.get("reasoning_effort")
        return object()

    monkeypatch.setattr(api_mod, "build_translator", fake_build_translator)
    monkeypatch.setattr(api_mod.pipeline, "translate", lambda *a, **k: [])


def test_language_override_reaches_the_translator(monkeypatch, tmp_path):
    """An override is what the adapter is built from, not the stored value."""
    seen: dict = {}
    _capture(monkeypatch, seen)
    cfg = _cfg(lang_in="en", lang_out="zh")

    api_mod.translate(
        ["a.pdf"],
        config=cfg,
        output=str(tmp_path),
        mode="ultrafast",
        lang_in="ja",
        lang_out="ko",
    )

    assert seen["config"].translation.lang_in == "ja"
    assert seen["config"].translation.lang_out == "ko"


def test_reasoning_override_reaches_the_translator(monkeypatch, tmp_path):
    """Passed as an argument rather than by editing the model entry."""
    seen: dict = {}
    _capture(monkeypatch, seen)
    cfg = _cfg()

    api_mod.translate(
        ["a.pdf"], config=cfg, output=str(tmp_path), mode="ultrafast", reasoning_effort="high"
    )

    assert seen["reasoning_effort"] == "high"


def test_overrides_do_not_touch_the_callers_config(monkeypatch, tmp_path):
    """The stored configuration must survive a job untouched.

    This is the whole point of the feature: the options panel changes one run,
    while the settings panel is the only thing that edits what is saved.  A
    shared object mutated in place would make a one-off choice permanent.
    """
    seen: dict = {}
    _capture(monkeypatch, seen)
    cfg = _cfg(lang_in="en", lang_out="zh")

    api_mod.translate(
        ["a.pdf"],
        config=cfg,
        output=str(tmp_path),
        mode="ultrafast",
        lang_in="ja",
        lang_out="ko",
        reasoning_effort="high",
    )

    # The caller's object is unchanged...
    assert cfg.translation.lang_in == "en"
    assert cfg.translation.lang_out == "zh"
    assert cfg.resolve("prov", "chat").reasoning_effort == "low"
    # ...and the translator got a different object, not the same one edited.
    assert seen["config"] is not cfg


def test_omitting_an_override_keeps_the_configured_value(monkeypatch, tmp_path):
    """Absent means "use the config", which is what the three-state rule is for."""
    seen: dict = {}
    _capture(monkeypatch, seen)
    cfg = _cfg(lang_in="en", lang_out="zh")

    api_mod.translate(["a.pdf"], config=cfg, output=str(tmp_path), mode="ultrafast")

    assert seen["config"].translation.lang_in == "en"
    assert seen["config"].translation.lang_out == "zh"
    # None, not a value: build_translator leaves the model entry as configured.
    assert seen["reasoning_effort"] is None


def test_empty_string_is_an_override_not_an_omission(monkeypatch, tmp_path):
    """`None` and `""` must not be conflated.

    `""` is a deliberate override to nothing; treating it as "unspecified" would
    silently ignore a caller that meant it.  The config schema may reject an
    empty language, which is a separate matter from how `None` is read.
    """
    seen: dict = {}
    _capture(monkeypatch, seen)
    cfg = _cfg(lang_out="zh")

    api_mod.translate(["a.pdf"], config=cfg, output=str(tmp_path), mode="ultrafast", lang_out="")

    assert seen["config"].translation.lang_out == ""
