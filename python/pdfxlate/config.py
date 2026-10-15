"""Configuration model and loading.

A single YAML file holds every model the user has configured.  Validation is
strict so a typo surfaces at startup rather than halfway through a translation
job.  ``${VAR}`` references are expanded from the environment so API keys need
not be written into the file.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from pdfxlate import paths

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

Mode = Literal["fast", "ultrafast"]
Backend = Literal["auto", "cpu", "cuda", "dml"]


def _expand(value: Any) -> Any:
    """Recursively expand ``${VAR}`` in strings.

    An unset variable expands to the empty string rather than being left as
    the literal ``${VAR}``: a literal would look like a real credential to the
    rest of the code and only fail much later with a confusing API error.
    Callers can therefore treat an empty value as "not configured".
    """
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def _collect_env_refs(value: Any, prefix: str = "") -> dict[str, str]:
    """Map dotted paths to the original ``${VAR}`` template at that path."""
    refs: dict[str, str] = {}
    if isinstance(value, str):
        if _ENV_PATTERN.search(value):
            refs[prefix] = value
    elif isinstance(value, dict):
        for k, v in value.items():
            refs.update(_collect_env_refs(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            refs.update(_collect_env_refs(v, f"{prefix}[{i}]"))
    return refs


def _restore_env_refs(data: dict, refs: dict[str, str]) -> dict:
    """Put ``${VAR}`` templates back where the value still matches the env.

    Keeps a secret that lives only in the environment out of the file, while
    still writing a value the user explicitly typed into the UI.
    """
    for dotted, template in refs.items():
        parts = dotted.replace("]", "").replace("[", ".").split(".")
        target: Any = data
        for part in parts[:-1]:
            target = target[int(part)] if isinstance(target, list) else target.get(part)
            if target is None:
                break
        else:
            leaf = parts[-1]
            current = target[int(leaf)] if isinstance(target, list) else target.get(leaf)
            expanded = _expand(template)
            # Only restore the reference if the value is still the env-derived
            # one; a user edit must win.
            if current == expanded:
                if isinstance(target, list):
                    target[int(leaf)] = template
                else:
                    target[leaf] = template
    return data


class Profile(BaseModel):
    """One configured model endpoint."""

    provider: str = "openai_compat"
    base_url: str | None = None
    api_key: str | None = None
    model: str
    # Sampling knobs.  temperature defaults to 0 because random sampling
    # corrupts the {vN} formula placeholders the typesetter relies on.
    temperature: float = 0.0
    top_p: float | None = None
    max_tokens: int | None = None
    stream: bool = False
    # Reasoning models: forwarded verbatim to the API when set.
    reasoning_effort: str | None = None
    # Arbitrary provider-specific body params, passed through untouched.
    extra_body: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str | None) -> str | None:
        return v.rstrip("/") if v else v


class TranslationSettings(BaseModel):
    lang_in: str = "en"
    lang_out: str = "zh"
    thread: int = Field(default=4, ge=1, le=64)
    mode: Mode = "fast"
    # Path to a custom prompt template file, or inline template text.
    prompt: str | None = None
    skip_subset_fonts: bool = False

    model_config = {"extra": "forbid"}


class LayoutSettings(BaseModel):
    backend: Backend = "auto"
    # Override the downloaded DocLayout model path.
    model_path: str | None = None

    model_config = {"extra": "forbid"}


class CacheSettings(BaseModel):
    enabled: bool = True

    model_config = {"extra": "forbid"}


class OcrSettings(BaseModel):
    """OCR behaviour for image-only pages."""

    # Tesseract language codes, e.g. "eng", "chi_sim", "eng+deu".  When unset,
    # the source language is mapped to a Tesseract code automatically.
    language: str | None = None
    # Directory holding *.traineddata.  When unset, data is downloaded and
    # cached under assets_dir()/tessdata.
    tessdata_prefix: str | None = None

    model_config = {"extra": "forbid"}


class AppConfig(BaseModel):
    default_profile: str | None = None
    profiles: dict[str, Profile] = Field(default_factory=dict)
    translation: TranslationSettings = Field(default_factory=TranslationSettings)
    layout: LayoutSettings = Field(default_factory=LayoutSettings)
    cache: CacheSettings = Field(default_factory=CacheSettings)
    ocr: OcrSettings = Field(default_factory=OcrSettings)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _check_default_profile(self) -> "AppConfig":
        if self.default_profile and self.default_profile not in self.profiles:
            raise ValueError(
                f"default_profile {self.default_profile!r} is not defined in profiles"
            )
        return self

    def resolve_profile(self, name: str | None = None) -> Profile:
        """Return the named profile, or the default one."""
        key = name or self.default_profile
        if not key:
            raise ValueError("No profile requested and no default_profile configured")
        try:
            return self.profiles[key]
        except KeyError:
            raise ValueError(f"Unknown profile {key!r}") from None


DEFAULT_CONFIG_TEMPLATE = """\
# pdfxlate configuration
default_profile: deepseek

profiles:
  deepseek:
    provider: openai_compat
    base_url: https://api.deepseek.com/v1
    api_key: ${DEEPSEEK_API_KEY}
    model: deepseek-chat
    temperature: 0
    stream: false

translation:
  lang_in: en
  lang_out: zh
  thread: 4
  mode: fast

layout:
  backend: auto

cache:
  enabled: true
"""


def load(path: Path | None = None) -> AppConfig:
    """Load config from disk, creating a commented template on first run."""
    path = path or paths.config_file()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    config = AppConfig.model_validate(_expand(raw))
    # Remember which fields came from ${VAR} so save() can restore the
    # reference instead of persisting an expanded secret.
    config.__dict__["_env_refs"] = _collect_env_refs(raw)
    return config


def save(config: AppConfig, path: Path | None = None) -> Path:
    """Persist config as YAML, keeping ``${VAR}`` references intact."""
    path = path or paths.config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(exclude_none=True)
    if refs := config.__dict__.get("_env_refs"):
        data = _restore_env_refs(data, refs)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path
