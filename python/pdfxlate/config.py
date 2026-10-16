"""Configuration model and loading.

A single YAML file holds every model the user has configured.  Validation is
strict so a typo surfaces at startup rather than halfway through a translation
job.  ``${VAR}`` references are expanded from the environment so API keys need
not be written into the file.

Structure
---------
Providers own endpoints; models live inside them::

    providers:
      deepseek:
        kind: openai_compat
        base_url: https://api.deepseek.com/v1
        api_key: ${DEEPSEEK_API_KEY}
        models:
          chat:   {model: deepseek-chat}
          reason: {model: deepseek-reasoner, reasoning_effort: high}

One provider therefore serves many models, and the credentials/endpoint are
configured once instead of being repeated per model.  ``resolve()`` flattens a
provider plus one of its models into a :class:`ResolvedModel`, which is the
single object the translation adapters consume.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from pdfxlate import paths

logger = logging.getLogger(__name__)

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


def _strip_trailing_slash(v: str | None) -> str | None:
    return v.rstrip("/") if v else v


class ModelEntry(BaseModel):
    """One model served by a provider.

    The key under ``provider.models`` is a short alias the user picks in the
    UI and in ``--model``; ``model`` is the identifier sent to the API and
    defaults to that alias when omitted.
    """

    model: str | None = None
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


class Provider(BaseModel):
    """A model endpoint plus the models served by it."""

    # Adapter registry name (see pdfxlate.models.base.register).
    kind: str = "openai_compat"
    base_url: str | None = None
    api_key: str | None = None
    models: dict[str, ModelEntry] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str | None) -> str | None:
        return _strip_trailing_slash(v)

    @model_validator(mode="after")
    def _fill_model_names(self) -> "Provider":
        # A model entry may rely on its alias alone, which is the common case
        # for someone adding a model quickly.
        for alias, entry in self.models.items():
            if not entry.model:
                entry.model = alias
        return self


class ResolvedModel(BaseModel):
    """A provider and one of its models, flattened into a single object.

    This is the exact shape the translation adapters expect: every field a
    :class:`~pdfxlate.models.base.ModelAdapter` reads (``base_url``,
    ``api_key``, ``model`` and the sampling knobs) lives on one object, so the
    adapters and the PDF pipeline need no knowledge of the provider/model
    nesting.
    """

    # Adapter registry name, taken from the owning provider.
    kind: str = "openai_compat"
    # Names, kept for display and diagnostics rather than for behaviour.
    provider: str = ""
    alias: str = ""
    base_url: str | None = None
    api_key: str | None = None
    model: str
    temperature: float = 0.0
    top_p: float | None = None
    max_tokens: int | None = None
    stream: bool = False
    reasoning_effort: str | None = None
    extra_body: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str | None) -> str | None:
        return _strip_trailing_slash(v)


# Historical name.  ``Profile`` used to be the flat one-endpoint model and is
# still what the adapter tests construct; the two are the same shape now, so
# this is an alias rather than a second class to keep in sync.
Profile = ResolvedModel


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
    default_provider: str | None = None
    default_model: str | None = None
    providers: dict[str, Provider] = Field(default_factory=dict)
    translation: TranslationSettings = Field(default_factory=TranslationSettings)
    layout: LayoutSettings = Field(default_factory=LayoutSettings)
    cache: CacheSettings = Field(default_factory=CacheSettings)
    ocr: OcrSettings = Field(default_factory=OcrSettings)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _check_defaults(self) -> "AppConfig":
        if self.default_provider and self.default_provider not in self.providers:
            raise ValueError(
                f"default_provider {self.default_provider!r} is not defined in providers"
            )
        if self.default_model:
            owner = self.providers.get(self.default_provider or "")
            if owner is None:
                raise ValueError(
                    "default_model requires a default_provider that exists"
                )
            if self.default_model not in owner.models:
                raise ValueError(
                    f"default_model {self.default_model!r} is not defined under "
                    f"provider {self.default_provider!r}"
                )
        return self

    # --- resolution -----------------------------------------------------
    def _find_provider_with_alias(self, alias: str) -> tuple[str, str] | None:
        """Back-compat: an old profile name used as a model alias."""
        hits = [
            (name, alias)
            for name, provider in self.providers.items()
            if alias in provider.models
        ]
        return hits[0] if len(hits) == 1 else None

    def provider_for(self, name: str) -> str:
        """Resolve a provider name, tolerating an old profile name."""
        if name in self.providers:
            return name
        if found := self._find_provider_with_alias(name):
            return found[0]
        known = ", ".join(sorted(self.providers)) or "(none)"
        raise ValueError(f"Unknown provider {name!r}; configured: {known}")

    def resolve(
        self, provider: str | None = None, model: str | None = None
    ) -> ResolvedModel:
        """Return the flattened provider+model, filling in the defaults."""
        requested = provider or self.default_provider
        if not requested:
            raise ValueError("No provider requested and no default_provider configured")

        pname = self.provider_for(requested)
        prov = self.providers[pname]
        if not prov.models:
            raise ValueError(f"Provider {pname!r} has no models configured")

        # A name that is not a provider but matches exactly one model alias is
        # an old-style ``profile`` reference; honour both halves at once.
        if model is None and provider and provider not in self.providers:
            if found := self._find_provider_with_alias(provider):
                return self._flatten(pname, found[1])

        alias = model
        if alias is None and pname == self.default_provider:
            alias = self.default_model
        if alias is None:
            alias = next(iter(prov.models))
        if alias not in prov.models:
            available = ", ".join(sorted(prov.models)) or "(none)"
            raise ValueError(
                f"Provider {pname!r} has no model {alias!r}; available: {available}"
            )
        return self._flatten(pname, alias)

    def _flatten(self, pname: str, alias: str) -> ResolvedModel:
        prov = self.providers[pname]
        entry = prov.models[alias]
        return ResolvedModel(
            kind=prov.kind,
            provider=pname,
            alias=alias,
            base_url=prov.base_url,
            api_key=prov.api_key,
            **entry.model_dump(),
        )

    def all_models(self) -> list[tuple[str, str]]:
        """Every ``(provider name, model alias)`` pair, in config order."""
        return [
            (name, alias)
            for name, provider in self.providers.items()
            for alias in provider.models
        ]


def _migrate_profiles(raw: dict) -> dict:
    """Convert a pre-provider ``profiles:`` file to the nested layout.

    Old files listed one flat profile per endpoint+model.  Providers that share
    an endpoint and credentials are grouped, each old profile name becoming a
    model alias, so ``--profile deepseek`` and the like keep resolving.
    """
    profiles = raw.pop("profiles") or {}
    if not isinstance(profiles, dict):
        return raw

    providers: dict[str, Any] = {}
    for alias, entry in profiles.items():
        if not isinstance(entry, dict):
            continue
        entry = dict(entry)
        kind = entry.pop("provider", "openai_compat")
        base_url = entry.pop("base_url", None)
        api_key = entry.pop("api_key", None)
        group = next(
            (
                name
                for name, prov in providers.items()
                if prov["kind"] == kind
                and prov["base_url"] == base_url
                and prov["api_key"] == api_key
            ),
            None,
        )
        if group is None:
            group = str(alias)
            providers[group] = {
                "kind": kind,
                "base_url": base_url,
                "api_key": api_key,
                "models": {},
            }
        providers[group]["models"][str(alias)] = entry

    raw["providers"] = providers

    old_default = raw.pop("default_profile", None)
    if old_default:
        for name, prov in providers.items():
            if old_default in prov["models"]:
                raw.setdefault("default_provider", name)
                raw.setdefault("default_model", old_default)
                break
    logger.warning(
        "config uses the old flat 'profiles:' layout; migrated in memory to "
        "'providers:' (saved back in the new form on the next config save)"
    )
    return raw


DEFAULT_CONFIG_TEMPLATE = """\
# pdfxlate configuration
#
# A provider holds the endpoint and credentials once; models are listed under
# it. Add more entries under `models` to use another model from the same
# service without repeating the URL and key.
default_provider: deepseek
default_model: chat

providers:
  deepseek:
    kind: openai_compat
    base_url: https://api.deepseek.com/v1
    api_key: ${DEEPSEEK_API_KEY}
    models:
      chat:
        model: deepseek-chat
        temperature: 0
        stream: false
      reason:
        model: deepseek-reasoner
        reasoning_effort: high

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
    # Migrate before collecting env refs: the migration keeps the literal
    # ``${VAR}`` strings, so refs must be gathered from the *new* paths or a
    # secret would be written out in clear on the first save.
    if "profiles" in raw and "providers" not in raw:
        raw = _migrate_profiles(raw)
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

    refs = config.__dict__.get("_env_refs")
    if not refs and path.exists():
        # The config was rebuilt by a caller (the UI posts back a full document
        # from ``config.get``, which necessarily returns *expanded* values).
        # Re-read the references from disk so an environment-held secret is
        # rewritten as ``${VAR}`` instead of being persisted in clear.
        try:
            on_disk = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            refs = _collect_env_refs(on_disk)
        except (OSError, yaml.YAMLError):
            logger.debug("could not re-read env refs from %s", path, exc_info=True)
    if refs:
        data = _restore_env_refs(data, refs)

    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path
