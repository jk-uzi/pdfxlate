"""Tests for the provider → models configuration schema.

Covers what the nesting adds over the old flat profiles: resolving a provider
plus one of its models, the defaults, the old-layout migration, and that a
``${VAR}`` secret is never written back in clear.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdfxlate import config as config_mod  # noqa: E402


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


NESTED = """\
default_provider: deepseek
default_model: chat

providers:
  deepseek:
    kind: openai_compat
    base_url: https://api.deepseek.com/v1/
    api_key: static-key
    models:
      chat:
        model: deepseek-chat
      reason:
        model: deepseek-reasoner
        reasoning_effort: high
  local:
    base_url: http://127.0.0.1:8000/v1
    api_key: local-key
    models:
      qwen:
        model: Qwen/Qwen3-32B
"""


# --- resolution ------------------------------------------------------------


def test_resolve_uses_defaults(tmp_path):
    cfg = config_mod.load(_write(tmp_path, NESTED))
    resolved = cfg.resolve()
    assert resolved.provider == "deepseek"
    assert resolved.alias == "chat"
    assert resolved.model == "deepseek-chat"
    assert resolved.kind == "openai_compat"


def test_resolve_named_provider_and_model(tmp_path):
    cfg = config_mod.load(_write(tmp_path, NESTED))
    resolved = cfg.resolve("deepseek", "reason")
    assert resolved.model == "deepseek-reasoner"
    assert resolved.reasoning_effort == "high"


def test_resolve_model_of_other_provider(tmp_path):
    cfg = config_mod.load(_write(tmp_path, NESTED))
    resolved = cfg.resolve("local", "qwen")
    assert resolved.base_url == "http://127.0.0.1:8000/v1"
    assert resolved.model == "Qwen/Qwen3-32B"


def test_provider_alone_picks_first_model(tmp_path):
    cfg = config_mod.load(_write(tmp_path, NESTED))
    assert cfg.resolve("local").alias == "qwen"


def test_resolved_is_flat_for_adapters(tmp_path):
    """The adapters read flat attributes; every one must be present."""
    cfg = config_mod.load(_write(tmp_path, NESTED))
    resolved = cfg.resolve("deepseek", "chat")
    for field in (
        "base_url",
        "api_key",
        "model",
        "temperature",
        "top_p",
        "max_tokens",
        "stream",
        "reasoning_effort",
        "extra_body",
    ):
        assert hasattr(resolved, field), field


def test_base_url_trailing_slash_stripped(tmp_path):
    cfg = config_mod.load(_write(tmp_path, NESTED))
    assert cfg.resolve("deepseek").base_url == "https://api.deepseek.com/v1"


def test_model_name_defaults_to_alias(tmp_path):
    cfg = config_mod.load(
        _write(tmp_path, "providers:\n  p:\n    api_key: k\n    models:\n      mymodel: {}\n")
    )
    assert cfg.resolve("p").model == "mymodel"


def test_all_models_lists_every_pair(tmp_path):
    cfg = config_mod.load(_write(tmp_path, NESTED))
    assert cfg.all_models() == [
        ("deepseek", "chat"),
        ("deepseek", "reason"),
        ("local", "qwen"),
    ]


# --- validation ------------------------------------------------------------


def test_unknown_provider_raises(tmp_path):
    cfg = config_mod.load(_write(tmp_path, NESTED))
    with pytest.raises(ValueError, match="Unknown provider"):
        cfg.resolve("nope")


def test_unknown_model_raises(tmp_path):
    cfg = config_mod.load(_write(tmp_path, NESTED))
    with pytest.raises(ValueError, match="no model"):
        cfg.resolve("deepseek", "missing")


def test_provider_without_models_raises(tmp_path):
    cfg = config_mod.load(_write(tmp_path, "providers:\n  p:\n    api_key: k\n"))
    with pytest.raises(ValueError, match="no models"):
        cfg.resolve("p")


def test_no_default_provider_raises(tmp_path):
    cfg = config_mod.load(_write(tmp_path, "providers: {}\n"))
    with pytest.raises(ValueError, match="no default_provider"):
        cfg.resolve()


def test_dangling_default_provider_rejected(tmp_path):
    with pytest.raises(Exception, match="default_provider"):
        config_mod.load(
            _write(tmp_path, "default_provider: ghost\nproviders: {}\n")
        )


def test_dangling_default_model_rejected(tmp_path):
    with pytest.raises(Exception, match="default_model"):
        config_mod.load(
            _write(
                tmp_path,
                "default_provider: p\ndefault_model: ghost\n"
                "providers:\n  p:\n    models:\n      real: {}\n",
            )
        )


def test_typo_in_provider_rejected(tmp_path):
    with pytest.raises(Exception, match="extra"):
        config_mod.load(
            _write(
                tmp_path,
                "providers:\n  p:\n    base_urk: http://x\n    models:\n      m: {}\n",
            )
        )


# --- migration from the old flat layout ------------------------------------


OLD = """\
default_profile: deepseek

profiles:
  deepseek:
    provider: openai_compat
    base_url: https://api.deepseek.com/v1
    api_key: key-1
    model: deepseek-chat
    temperature: 0
  local_vllm:
    provider: openai_compat
    base_url: http://127.0.0.1:8000/v1
    model: Qwen/Qwen3-32B
"""


def test_old_profiles_migrate(tmp_path):
    cfg = config_mod.load(_write(tmp_path, OLD))
    assert cfg.default_provider == "deepseek"
    assert cfg.default_model == "deepseek"
    assert cfg.resolve().model == "deepseek-chat"
    assert cfg.resolve("local_vllm").model == "Qwen/Qwen3-32B"


def test_old_profile_name_still_resolves(tmp_path):
    """``--profile deepseek`` predates providers and must keep working."""
    cfg = config_mod.load(_write(tmp_path, OLD))
    assert cfg.resolve("deepseek").model == "deepseek-chat"


def test_migration_groups_same_endpoint(tmp_path):
    cfg = config_mod.load(
        _write(
            tmp_path,
            "profiles:\n"
            "  a:\n    base_url: https://x/v1\n    api_key: k\n    model: m-1\n"
            "  b:\n    base_url: https://x/v1\n    api_key: k\n    model: m-2\n",
        )
    )
    # One endpoint and key: one provider holding both models, not two.
    assert len(cfg.providers) == 1
    assert len(next(iter(cfg.providers.values())).models) == 2


def test_migration_splits_different_endpoints(tmp_path):
    cfg = config_mod.load(_write(tmp_path, OLD))
    assert set(cfg.providers) == {"deepseek", "local_vllm"}


# --- env refs --------------------------------------------------------------


def test_env_ref_expanded_on_load(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_KEY", "secret-value")
    cfg = config_mod.load(
        _write(
            tmp_path,
            "providers:\n  p:\n    api_key: ${MY_KEY}\n    models:\n      m: {}\n",
        )
    )
    assert cfg.resolve("p").api_key == "secret-value"


def test_save_keeps_env_ref_instead_of_secret(tmp_path, monkeypatch):
    """A secret that lives in the environment must not land in the file."""
    monkeypatch.setenv("MY_KEY", "secret-value")
    path = _write(
        tmp_path,
        "providers:\n  p:\n    api_key: ${MY_KEY}\n    models:\n      m: {}\n",
    )
    cfg = config_mod.load(path)
    config_mod.save(cfg, path)
    assert "${MY_KEY}" in path.read_text(encoding="utf-8")
    assert "secret-value" not in path.read_text(encoding="utf-8")


def test_save_without_env_refs_does_not_leak(tmp_path, monkeypatch):
    """The UI rebuilds the config, so refs must be recovered from disk."""
    monkeypatch.setenv("MY_KEY", "secret-value")
    path = _write(
        tmp_path,
        "providers:\n  p:\n    api_key: ${MY_KEY}\n    models:\n      m: {}\n",
    )
    # Simulate the renderer round-trip: expanded values, no memory of refs.
    rebuilt = config_mod.AppConfig.model_validate(
        {
            "providers": {
                "p": {"api_key": "secret-value", "models": {"m": {"model": "m"}}}
            }
        }
    )
    config_mod.save(rebuilt, path)
    text = path.read_text(encoding="utf-8")
    assert "${MY_KEY}" in text
    assert "secret-value" not in text


def test_user_typed_key_wins_over_env_ref(tmp_path, monkeypatch):
    """A value the user typed must overwrite, not be reverted to ${VAR}."""
    monkeypatch.setenv("MY_KEY", "from-env")
    path = _write(
        tmp_path,
        "providers:\n  p:\n    api_key: ${MY_KEY}\n    models:\n      m: {}\n",
    )
    cfg = config_mod.load(path)
    cfg.providers["p"].api_key = "typed-by-user"
    config_mod.save(cfg, path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["providers"]["p"]["api_key"] == "typed-by-user"


def test_migration_keeps_env_ref(tmp_path, monkeypatch):
    """Migrating an old file must not write the secret out in clear."""
    monkeypatch.setenv("OLD_KEY", "old-secret")
    path = _write(
        tmp_path,
        "profiles:\n  p:\n    base_url: https://x/v1\n"
        "    api_key: ${OLD_KEY}\n    model: m\n",
    )
    cfg = config_mod.load(path)
    config_mod.save(cfg, path)
    text = path.read_text(encoding="utf-8")
    assert "${OLD_KEY}" in text
    assert "old-secret" not in text


def test_default_template_is_valid(tmp_path):
    """The shipped template must load and resolve without edits."""
    path = tmp_path / "config.yaml"
    path.write_text(config_mod.DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
    cfg = config_mod.load(path)
    assert cfg.resolve().model == "deepseek-chat"
    assert cfg.resolve("deepseek", "reason").reasoning_effort == "high"
