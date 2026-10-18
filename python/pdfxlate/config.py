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

    The lookup is by dotted path, which assumes the structure is unchanged
    since the file was read. It is not: renaming a provider or reordering
    entries in the UI changes those paths, and a path-only restore would then
    silently write the expanded secret to disk. So anything the path lookup
    could not place falls back to a value match — any remaining string that
    equals an expanded ``${VAR}`` is rewritten as the reference.
    """
    unmatched = dict(refs)
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
                unmatched.pop(dotted, None)
    if unmatched:
        _restore_by_value(data, unmatched)
    return data


def _restore_by_value(node: Any, refs: dict[str, str]) -> None:
    """Rewrite any remaining value that equals an expanded ``${VAR}``.

    Mutates ``node`` in place. Only the values are compared, never the keys, so
    a provider renamed from ``old`` to ``new`` still gets its key written back
    as ``${DEEPSEEK_API_KEY}``.
    """
    expanded = {_expand(template): template for template in refs.values()}
    # Longest first: if two variables expand to the same secret, any of them is
    # equally correct, but taking the longer template first is stable.
    pairs = sorted(expanded.items(), key=lambda item: len(item[1]), reverse=True)
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str):
                for secret, template in pairs:
                    if value == secret:
                        node[key] = template
                        break
            else:
                _restore_by_value(value, refs)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            if isinstance(value, str):
                for secret, template in pairs:
                    if value == secret:
                        node[index] = template
                        break
            else:
                _restore_by_value(value, refs)


def _strip_trailing_slash(v: str | None) -> str | None:
    return v.rstrip("/") if v else v


def _read_env_refs(path: Path) -> dict[str, str]:
    """Collect ``${VAR}`` references from a config file on disk.

    A migrated file keeps its references under the old ``profiles.*`` paths
    while the migrated document lives under ``providers.*``; migrating here too
    keeps the two in step, so the lookup in :func:`_restore_env_refs` still
    finds them (otherwise a secret held only in the environment would be
    written out in clear on the next save).
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        logger.debug("could not read env refs from %s", path, exc_info=True)
        return {}
    if not isinstance(raw, dict):
        return {}
    if "profiles" in raw and "providers" not in raw:
        raw = _migrate_profiles(dict(raw))
    return _collect_env_refs(raw)


class ModelEntry(BaseModel):
    """One model served by a provider.

    The key under ``provider.models`` is a short alias the user picks in the
    UI and in ``--model``; ``model`` is the identifier sent to the API and
    defaults to that alias when omitted.
    """

    model: str | None = None
    # Whether this model may be used for a run.
    #
    # Disabling is how a model is taken out of rotation without deleting its
    # settings: the options dropdown and the no-argument fallback skip it, and
    # naming it explicitly is an error rather than a silent translation with a
    # model the user turned off.  Existing files have no such key and must keep
    # working, so the default is enabled.
    enabled: bool = True
    # Sampling knobs.  temperature defaults to 0 because random sampling
    # corrupts the {vN} formula placeholders the typesetter relies on.
    # `None` means "leave it out of the request entirely", which is the only
    # way to talk to reasoning models that reject the parameter.  The settings
    # UI writes 0 for new models, so the safe default is what people get.
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stream: bool = False
    # Reasoning models: forwarded verbatim to the API when set.
    reasoning_effort: str | None = None
    # The model's total context window, in tokens.
    #
    # Recorded because it is a property of the model that nothing else knows
    # (no catalogue is consulted), and it is what makes a too-long paragraph
    # diagnosable: the prompt for one paragraph can exceed a small window.
    # Deliberately *not* used to invent a `max_tokens` on the caller's behalf —
    # see `_check_context` in models/base.py for what it does do.
    context_length: int | None = Field(default=None, gt=0)
    # Arbitrary provider-specific body params, passed through untouched.
    extra_body: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class Provider(BaseModel):
    """A model endpoint plus the models served by it."""

    # Adapter registry name (see pdfxlate.models.base.register).
    kind: str = "openai_compat"
    # Which external agent CLI backs this provider when ``kind == "agent_cli"``.
    #
    # An agent provider has no endpoint and no key: the CLI's own login state is
    # the credential, and ``agent`` names which CLI to spawn (claude, codex, …).
    # It is meaningless for the HTTP kinds, so it stays ``None`` there.
    agent: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    # Whether this provider takes part in a run at all.
    #
    # Same idea as ModelEntry.enabled, one level up: switching a provider off
    # removes every one of its models from the options list and from the
    # no-argument fallback, and naming one explicitly is an error.  Kept
    # separate from deleting because a provider that is merely not in use
    # right now should not lose its key.
    enabled: bool = True
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

    @model_validator(mode="after")
    def _require_agent_for_agent_cli(self) -> "Provider":
        # ``agent_cli`` is the one kind that cannot infer what to spawn: without
        # this field the adapter would have no idea which CLI the user meant, so
        # fail at load time with a message naming the field rather than halfway
        # through a job.
        if self.kind == "agent_cli" and not (self.agent or "").strip():
            raise ValueError(
                "providers with kind 'agent_cli' must set 'agent' "
                "(one of: claude, codex, workbuddy, zcode, dsh)"
            )
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
    # External agent id for ``kind == "agent_cli"``; ``None`` for HTTP kinds.
    agent: str | None = None
    # Names, kept for display and diagnostics rather than for behaviour.
    provider: str = ""
    alias: str = ""
    base_url: str | None = None
    api_key: str | None = None
    model: str
    # Carried through from the entry so an adapter built from a resolved model
    # can tell it was deliberately switched off.  `_flatten` dumps every entry
    # field into this class, and it forbids unknown keys, so the field has to
    # exist here as well as on ModelEntry.
    enabled: bool = True
    # Same `None` semantics as ModelEntry: omitted from the request when unset.
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stream: bool = False
    reasoning_effort: str | None = None
    context_length: int | None = None
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


class AgentSettings(BaseModel):
    """Machine-local overrides for external agent CLIs.

    Only paths live here.  Everything else about an agent is discovered from the
    machine (PATH, the Windows uninstall registry, the standard install
    directories) or from the CLI itself; the point of the section is to let a
    user say "it is installed *there*" when discovery cannot see it.  Storing
    nothing else also means this section is safe to copy between machines only
    as a starting point.
    """

    # agent id -> executable (or, for ZCode, the zcode.cjs script) path.
    paths: dict[str, str] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class AppConfig(BaseModel):
    """The whole configuration document.

    There is deliberately no "default provider/model".  Settings describes what
    models exist and how to reach them; which one a run uses is decided per run
    (the options panel, ``--provider``/``--model``, or an MCP tool argument).
    A stored default used to exist and only ever caused confusion: the UI showed
    it as a marker, nothing wrote it, and the settings panel's selection leaked
    into jobs as if it were one.
    """

    providers: dict[str, Provider] = Field(default_factory=dict)
    # Optional: only machine-local agent CLI path overrides.  ``None`` (absent)
    # and an empty object mean the same thing, but keeping ``None`` is what lets
    # ``save()`` omit the section entirely from a config that never used it.
    agents: AgentSettings | None = None
    translation: TranslationSettings = Field(default_factory=TranslationSettings)
    layout: LayoutSettings = Field(default_factory=LayoutSettings)
    cache: CacheSettings = Field(default_factory=CacheSettings)
    ocr: OcrSettings = Field(default_factory=OcrSettings)

    model_config = {"extra": "forbid"}

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
        """Return the flattened provider+model.

        With nothing named, this falls back to the first provider's first
        *enabled* model so the CLI and MCP can still be called without
        arguments. That fallback follows the order of the file, so reordering
        ``config.yaml`` changes which model an unspecified call reaches — the
        price of having no stored default.
        """
        if not self.providers:
            raise ValueError("No providers configured")

        if provider is None:
            # In config order, not sorted: the file is the user's ordering.
            enabled = self.all_models(only_enabled=True)
            if not enabled:
                raise ValueError("No enabled models configured")
            return self._flatten(*enabled[0])

        # A name that is not a provider but matches exactly one model alias is
        # an old-style ``profile`` reference; honour both halves at once.
        if model is None and provider not in self.providers:
            if found := self._find_provider_with_alias(provider):
                self._require_provider_enabled(found[0])
                self._require_enabled(*found)
                return self._flatten(*found)

        pname = self.provider_for(provider)
        self._require_provider_enabled(pname)
        models = self.providers[pname].models
        if not models:
            raise ValueError(f"Provider {pname!r} has no models configured")
        if model is None:
            # Provider named but not a model: the first enabled one is the
            # natural stand-in, mirroring the no-argument fallback.
            alias = next((a for a, e in models.items() if e.enabled), None)
            if alias is None:
                raise ValueError(f"Provider {pname!r} has no enabled models")
        else:
            alias = model
        if alias not in models:
            available = ", ".join(sorted(models)) or "(none)"
            raise ValueError(
                f"Provider {pname!r} has no model {alias!r}; available: {available}"
            )
        self._require_enabled(pname, alias)
        return self._flatten(pname, alias)

    def _require_provider_enabled(self, pname: str) -> None:
        """Refuse a provider the user switched off, before looking at models."""
        if not self.providers[pname].enabled:
            raise ValueError(
                f"Provider {pname!r} is disabled; enable it in Settings first"
            )

    def _require_enabled(self, pname: str, alias: str) -> None:
        """Refuse a model the user switched off.

        Silent use would defeat the point of disabling it; the message says
        which switch to turn back on.
        """
        if not self.providers[pname].models[alias].enabled:
            raise ValueError(
                f"Model {alias!r} of provider {pname!r} is disabled; "
                "enable it in Settings first"
            )

    def _flatten(self, pname: str, alias: str) -> ResolvedModel:
        prov = self.providers[pname]
        entry = prov.models[alias]
        return ResolvedModel(
            kind=prov.kind,
            agent=prov.agent,
            provider=pname,
            alias=alias,
            base_url=prov.base_url,
            api_key=prov.api_key,
            **entry.model_dump(),
        )

    def all_models(self, only_enabled: bool = False) -> list[tuple[str, str]]:
        """Every ``(provider name, model alias)`` pair, in config order.

        ``only_enabled`` drops models the user switched off; it is what the
        options dropdown and the no-argument fallback want. A disabled
        *provider* drops all of its models too — switching the provider off is
        meant to take the whole group out of rotation. The default keeps every
        pair, because the editor and ``--list-models`` must still show a
        disabled entry in order to switch it back on.
        """
        return [
            (name, alias)
            for name, provider in self.providers.items()
            if not only_enabled or provider.enabled
            for alias, entry in provider.models.items()
            if not only_enabled or entry.enabled
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

    # `default_profile` named the one flat profile to use by default. Both the
    # flat layout and the notion of a stored default are gone, so the former
    # default's provider simply becomes the first entry — `resolve()` with no
    # arguments falls back to the first provider, which reproduces the old
    # behaviour for an unspecified call.
    old_default = raw.pop("default_profile", None)
    if old_default:
        ordered = {name: prov for name, prov in providers.items() if old_default in prov["models"]}
        ordered.update({name: prov for name, prov in providers.items() if name not in ordered})
        providers = ordered
        raw["providers"] = providers
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
#
# This file describes what exists, not what to use: there is no "default"
# model. Which one a run uses is chosen per run (the options panel in the
# desktop app, or --provider/--model on the command line).

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
        # The model's context window, in tokens. Recorded so a paragraph whose
        # prompt cannot fit is reported instead of failing obscurely; it does
        # not set max_tokens for you.
        context_length: 65536
      reason:
        model: deepseek-reasoner
        reasoning_effort: high
        context_length: 65536

# External agent CLIs can translate too, using their own login instead of an API
# key. A provider with kind: agent_cli names the agent and lists the models the
# CLI offers; `agents.paths` is only needed when the CLI is not on PATH.
#
# providers:
#   claude:
#     kind: agent_cli
#     agent: claude
#     models:
#       sonnet:
#         model: sonnet
#
# agents:
#   paths:
#     zcode: C:\\Program Files\\ZCode\\resources\\glm\\zcode.cjs

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
    _drop_stored_default(raw)
    config = AppConfig.model_validate(_expand(raw))
    # Remember which fields came from ${VAR} so save() can restore the
    # reference instead of persisting an expanded secret.
    config.__dict__["_env_refs"] = _collect_env_refs(raw)
    return config


def _drop_stored_default(raw: dict) -> None:
    """Discard the removed ``default_provider`` / ``default_model`` keys.

    They no longer exist on :class:`AppConfig`, which forbids unknown keys, so a
    file still carrying them would fail to validate and the engine would refuse
    to start — for every existing user, since the setting shipped in the
    template. Dropping them here makes old files load; the next save writes the
    file without them.

    Mutates ``raw`` in place, because the caller validates that same object.
    """
    for key in ("default_provider", "default_model"):
        if raw.pop(key, None) is not None:
            logger.warning(
                "config has the removed %r setting; ignored. Which model a run "
                "uses is now chosen per run.",
                key,
            )


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
        refs = _read_env_refs(path)
    if refs:
        data = _restore_env_refs(data, refs)

    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path
