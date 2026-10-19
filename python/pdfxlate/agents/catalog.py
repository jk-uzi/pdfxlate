"""Model lists for the external agent CLIs.

Each agent answers this question differently, and one of them (Claude) cannot
answer it at all.  The rule is therefore "try the CLI's own enumeration, fall
back to a small built-in list, and never raise": a model list is a convenience,
and failing to enumerate one must not make the settings panel unusable.

The parsers are pure functions on purpose — the shapes are the part worth
testing, and they are tested without spawning anything.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from pdfxlate.agents import detect, workbuddy_desktop
from pdfxlate.agents.defs import AgentDef, get_agent_def

logger = logging.getLogger(__name__)

#: Model lists change rarely; this matches the detection cache.
MODELS_TTL = 30.0

#: The last resort per agent, mirroring the feature brief.
FALLBACK_MODELS: dict[str, tuple[tuple[str, str], ...]] = {
    "claude": (("sonnet", "Sonnet"), ("opus", "Opus"), ("haiku", "Haiku")),
    "codex": (
        ("gpt-5-codex", "gpt-5-codex"),
        ("gpt-5", "gpt-5"),
        ("o3", "o3"),
        ("o4-mini", "o4-mini"),
    ),
    "workbuddy": (("default", "default"),),
    "zcode": (("default", "Current model"),),
    "dsh": (("default", "Current model"),),
}

_MODELS_CACHE: dict[tuple, tuple[float, dict[str, Any]]] = {}
#: DSH model id -> provider route, filled from ``--dump-config``.
_DSH_PROVIDERS: dict[str, str] = {}
_DSH_DEFAULT: tuple[str, str] | None = None
_DSH_PROVIDER_LOADED = False

#: Reading ZCode's 700 MB session database is not free; a short cache covers
#: the handful of lookups a test/translate run can make in one go.
_ZCODE_CURRENT_TTL = 8.0
_ZCODE_CURRENT_CACHE: tuple[float, tuple[str, str] | None] | None = None

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def _models(defn: AgentDef) -> list[dict[str, str]]:
    pairs = defn.builtin_models or FALLBACK_MODELS.get(defn.id, ())
    return [{"id": model_id, "label": label} for model_id, label in pairs]


def fallback_models(agent: str, error: str | None = None) -> dict[str, Any]:
    """The list to show when the CLI cannot enumerate its models."""
    defn = get_agent_def(agent)
    result: dict[str, Any] = {
        "models": _models(defn),
        "source": "fallback",
    }
    if error:
        result["error"] = error
    return result


def _truncate(text: str, limit: int = 300) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _strip_fences(text: str) -> str:
    match = _FENCE_RE.search(text)
    return match.group(1).strip() if match else text.strip()


def parse_codex_models(payload: str | Mapping[str, Any] | list[Any]) -> list[dict[str, str]]:
    """Parse ``codex debug models``.

    Shape: ``{"models": [{"slug", "display_name", "visibility"}]}``.  Entries
    marked ``visibility == "hide"`` are internal and must not be offered.
    """
    if isinstance(payload, str):
        try:
            data = json.loads(_strip_fences(payload))
        except ValueError:
            return []
    else:
        data = payload
    if isinstance(data, dict):
        entries = data.get("models")
    else:
        entries = data
    if not isinstance(entries, list):
        return []
    models: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("visibility") or "").lower() == "hide":
            continue
        model_id = str(entry.get("slug") or "").strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        label = str(entry.get("display_name") or model_id).strip() or model_id
        models.append({"id": model_id, "label": label})
    return models


def parse_codebuddy_help(text: str) -> list[dict[str, str]]:
    """Parse ``codebuddy -p --help``'s ``Currently supported: (...)`` line."""
    match = re.search(r"currently\s+supported\s*:\s*\(([^)]*)\)", text or "", re.IGNORECASE)
    if match is None:
        match = re.search(
            r"currently\s+supported\s*:\s*([^\n\r]+)", text or "", re.IGNORECASE
        )
    if match is None:
        return []
    raw = match.group(1)
    models: list[dict[str, str]] = []
    seen: set[str] = set()
    for part in re.split(r"[,;\s]+", raw):
        model_id = part.strip().strip("'\"`")
        # Tolerate entries such as "sonnet (default)".
        model_id = re.sub(r"\(.*?\)", "", model_id).strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        models.append({"id": model_id, "label": model_id})
    return models


class _TolerantLoader(yaml.SafeLoader):
    """A SafeLoader that ignores the JavaScript tags DSH's config tree uses.

    ``dsh --dump-config`` composes a JS-yaml tree containing values such as
    ``disabled: !!js '!ctx.get(...)'``.  They are irrelevant to us, but a plain
    ``safe_load`` refuses the whole document because of them.
    """


def _ignore_js(loader: yaml.Loader, tag_suffix: str, node: yaml.Node) -> None:
    return None


_TolerantLoader.add_multi_constructor("tag:yaml.org,2002:js", _ignore_js)
_TolerantLoader.add_multi_constructor("!", _ignore_js)


# --- DSH desktop profile ----------------------------------------------------
#
# ``dsh --profile headless --dump-config`` only knows the headless profile's
# own model, which is a single built-in route.  The models a user actually
# configured live in the desktop profile's patch layer:
#
#     ~/.dsh/profiles/desktop/cordis.patch.yml
#
# That file is an entry list; ``llm-pi-ai`` carries the provider dictionary and
# ``agent-default-model`` the current selection.  Reading it is read-only: the
# file is never written, and a ``--patch`` overlay is what passes a selection
# to the headless run.

#: Dict keys that hold a credential rather than a credential *name*.  They are
#: dropped before a provider config is returned or written into a patch; only
#: ``apiKeyEnv`` (the name of an environment variable) survives.
_SECRET_KEYS = frozenset(
    {
        "key",
        "apikey",
        "apikeyvalue",
        "token",
        "accesstoken",
        "refreshtoken",
        "secret",
        "clientsecret",
        "password",
        "passwd",
        "authorization",
        "auth",
        "credential",
        "credentials",
        "xapikey",
        "xauthtoken",
    }
)


def dsh_home() -> Path:
    """The DSH state directory (``$DSH_HOME``, else ``~/.dsh``)."""
    override = (os.environ.get("DSH_HOME") or "").strip()
    return Path(override).expanduser() if override else Path.home() / ".dsh"


def dsh_desktop_profile_path() -> Path | None:
    """``<DSH_HOME>/profiles/desktop/cordis.patch.yml``, if it exists."""
    path = dsh_home() / "profiles" / "desktop" / "cordis.patch.yml"
    try:
        return path if path.is_file() else None
    except OSError:
        return None


def _normalized_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SECRET_SUFFIXES = ("apikey", "token", "secret", "password", "credential")


def _is_secret_key(key: Any) -> bool:
    normalized = _normalized_key(key)
    if normalized in _SECRET_KEYS:
        return True
    return any(normalized.endswith(suffix) for suffix in _SECRET_SUFFIXES)


def sanitize_provider_config(config: Any) -> Any:
    """Copy a provider config, dropping every credential value.

    The desktop profile stores the *name* of the environment variable that
    holds a key (``apiKeyEnv``), not the key itself, but a hand-edited file can
    contain an ``apiKey`` or an authorization header.  Those never leave this
    function: the returned structure is safe to show in the UI and safe to
    write into a temporary ``--patch`` file.
    """
    if isinstance(config, dict):
        clean: dict[Any, Any] = {}
        for key, value in config.items():
            normalized = _normalized_key(key)
            if normalized == "apikeyenv":
                # Keep the name only when it actually looks like a variable
                # name; a pasted secret must not be mistaken for one.
                if isinstance(value, str) and _ENV_NAME_RE.match(value.strip()):
                    clean[key] = value.strip()
                continue
            if _is_secret_key(key):
                continue
            if normalized == "headers" and isinstance(value, dict):
                clean[key] = {
                    header: header_value
                    for header, header_value in value.items()
                    if not _is_secret_key(header)
                }
                continue
            clean[key] = sanitize_provider_config(value)
        return clean
    if isinstance(config, list):
        return [sanitize_provider_config(item) for item in config]
    return config


def _provider_routes(routes: Any) -> list[tuple[str, dict[str, Any]]]:
    """Normalise ``llm-pi-ai.config.providers`` to ``[(name, config)]``."""
    if isinstance(routes, dict):
        return [
            (str(name or ""), route)
            for name, route in routes.items()
            if isinstance(route, dict)
        ]
    if isinstance(routes, list):
        return [
            (str(route.get("provider") or route.get("id") or ""), route)
            for route in routes
            if isinstance(route, dict)
        ]
    return []


def _profile_source_text(source: str | os.PathLike[str]) -> str | None:
    """Read a path argument, or return a YAML string unchanged."""
    if isinstance(source, os.PathLike):
        try:
            return Path(source).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
    text = str(source or "")
    # The signature is ``text_or_path``: a short single-line string that names
    # an existing file is a path, everything else is YAML.
    if text and "\n" not in text:
        try:
            candidate = Path(text)
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    return text


def parse_dsh_desktop_profile(
    text_or_path: str | os.PathLike[str],
) -> dict[str, Any]:
    """Parse the desktop profile's patch layer.

    Returns ``{"models", "providers", "default"}``:

    * ``models`` — ``[{id, label, provider, current}]`` where ``id`` is the
      composite ``provider/model`` spelling.  Model ids may themselves contain
      ``/`` (OpenRouter does this), so only the first slash separates provider
      from model.
    * ``providers`` — the full (sanitised) config of every enabled provider,
      ready to be embedded in a ``--patch`` overlay.
    * ``default`` — ``(provider, model)`` from ``agent-default-model``, or
      ``None``.

    A missing/unreadable/invalid file yields empty structures rather than an
    exception: the model list is a convenience, and the settings panel must
    still render.
    """
    empty = {"models": [], "providers": {}, "default": None}
    text = _profile_source_text(text_or_path)
    if not text:
        return empty
    try:
        data = yaml.load(text, Loader=_TolerantLoader)
    except yaml.YAMLError:
        return empty
    entries = data if isinstance(data, list) else []

    default: tuple[str, str] | None = None
    provider_configs: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_id = entry.get("id")
        config = entry.get("config")
        if not isinstance(config, dict):
            continue
        if entry_id == "agent-default-model":
            provider = str(config.get("provider") or "").strip()
            model = str(config.get("model") or "").strip()
            if model:
                default = (provider, model)
        elif entry_id == "llm-pi-ai":
            for name, route in _provider_routes(config.get("providers")):
                provider = str(
                    name or route.get("provider") or route.get("id") or ""
                ).strip()
                if not provider or route.get("enabled") is False:
                    continue
                provider_configs[provider] = sanitize_provider_config(route)

    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for provider, route in provider_configs.items():
        display = str(
            route.get("displayName") or route.get("display_name") or provider
        ).strip() or provider
        route_models = route.get("models")
        if isinstance(route_models, dict):
            route_models = [
                {"id": key, **(value if isinstance(value, dict) else {})}
                for key, value in route_models.items()
            ]
        if not isinstance(route_models, list):
            continue
        for model in route_models:
            if not isinstance(model, dict):
                continue
            model_id = str(model.get("id") or model.get("model") or "").strip()
            if not model_id:
                continue
            composite = f"{provider}/{model_id}"
            if composite in seen:
                continue
            seen.add(composite)
            name = str(
                model.get("name")
                or model.get("displayName")
                or model.get("display_name")
                or model_id
            ).strip() or model_id
            models.append(
                {
                    "id": composite,
                    "label": f"{name} ({display})",
                    "provider": provider,
                    "current": default == (provider, model_id),
                }
            )
    return {"models": models, "providers": provider_configs, "default": default}


_DSH_DESKTOP_CACHE: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}


def load_dsh_desktop_profile(refresh: bool = False) -> dict[str, Any]:
    """Read and cache the desktop profile, keyed on its mtime and size.

    ``refresh=True`` re-reads even when the signature is unchanged, mirroring
    the model-list cache's refresh flag.
    """
    path = dsh_desktop_profile_path()
    if path is None:
        return {"models": [], "providers": {}, "default": None}
    try:
        stat = path.stat()
    except OSError:
        return {"models": [], "providers": {}, "default": None}
    key = str(path)
    signature = (stat.st_mtime_ns, stat.st_size)
    cached = _DSH_DESKTOP_CACHE.get(key)
    if not refresh and cached is not None and cached[0] == signature:
        return cached[1]
    parsed = parse_dsh_desktop_profile(path)
    _DSH_DESKTOP_CACHE[key] = (signature, parsed)
    return parsed


def dsh_provider_config(provider: str | None) -> dict[str, Any] | None:
    """The sanitised desktop-profile config for one provider, or ``None``."""
    if not provider:
        return None
    providers = load_dsh_desktop_profile().get("providers") or {}
    config = providers.get(provider)
    return copy.deepcopy(config) if isinstance(config, dict) else None


def dsh_default_model() -> tuple[str, str] | None:
    """The desktop profile's ``agent-default-model``, else the dump's default."""
    default = load_dsh_desktop_profile().get("default")
    if isinstance(default, tuple) and len(default) == 2 and default[1]:
        return default
    return _DSH_DEFAULT


def _desktop_model_providers(refresh: bool = False) -> dict[str, set[str]]:
    """Map a bare model id to the desktop providers that serve it."""
    providers = load_dsh_desktop_profile(refresh=refresh).get("providers") or {}
    mapping: dict[str, set[str]] = {}
    for provider, config in providers.items():
        if not isinstance(config, dict):
            continue
        route_models = config.get("models")
        if isinstance(route_models, dict):
            model_ids = [str(key) for key in route_models]
        elif isinstance(route_models, list):
            model_ids = [
                str(model.get("id") or model.get("model") or "").strip()
                for model in route_models
                if isinstance(model, dict)
            ]
        else:
            continue
        for model_id in model_ids:
            if model_id:
                mapping.setdefault(model_id, set()).add(provider)
    return mapping


def parse_dsh_config(text: str) -> dict[str, Any]:
    """Extract models from ``dsh --profile headless --dump-config``.

    The dump is a list of plugin entries.  ``agent-default-model`` carries the
    current selection, and ``llm-pi-ai`` carries configured provider routes,
    each with a ``models`` list.  Returns ``{"models", "providers", "default"}``
    where ``providers`` maps a model id to its provider route (needed for the
    ``--patch`` overlay) and ``default`` is ``(provider, model)`` or ``None``.
    """
    try:
        data = yaml.load(text, Loader=_TolerantLoader)
    except yaml.YAMLError:
        return {"models": [], "providers": {}, "default": None}
    entries = data if isinstance(data, list) else []
    found: list[dict[str, str]] = []
    providers: dict[str, str] = {}
    default: tuple[str, str] | None = None
    seen: set[str] = set()

    def add(model_id: str, label: str, provider: str | None) -> None:
        if not model_id or model_id in seen:
            return
        seen.add(model_id)
        found.append({"id": model_id, "label": label or model_id})
        if provider:
            providers[model_id] = provider

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_id = entry.get("id")
        config = entry.get("config")
        if not isinstance(config, dict):
            continue
        if entry_id == "agent-default-model":
            provider = str(config.get("provider") or "").strip()
            model = str(config.get("model") or "").strip()
            if model:
                default = (provider, model)
                add(model, f"{model} ({provider})" if provider else model, provider or None)
        elif entry_id == "llm-pi-ai":
            routes = config.get("providers")
            if isinstance(routes, dict):
                items = routes.items()
            elif isinstance(routes, list):
                items = (
                    (str(route.get("provider") or route.get("id") or ""), route)
                    for route in routes
                    if isinstance(route, dict)
                )
            else:
                items = ()
            for route_name, route in items:
                if not isinstance(route, dict):
                    continue
                provider = str(
                    route_name or route.get("provider") or route.get("id") or ""
                ).strip()
                route_models = route.get("models")
                if isinstance(route_models, dict):
                    route_models = [
                        {"id": key, **(value if isinstance(value, dict) else {})}
                        for key, value in route_models.items()
                    ]
                if not isinstance(route_models, list):
                    continue
                for model in route_models:
                    if not isinstance(model, dict):
                        continue
                    model_id = str(model.get("id") or model.get("model") or "").strip()
                    label = str(
                        model.get("name")
                        or model.get("displayName")
                        or model.get("display_name")
                        or model_id
                    ).strip()
                    add(
                        model_id,
                        f"{label} · {provider}" if provider else label,
                        provider or None,
                    )
    return {"models": found, "providers": providers, "default": default}


def desktop_provider_for_model(model: str) -> str | None:
    """The desktop provider serving a bare model id, when it is unambiguous."""
    if not model:
        return None
    owners = _desktop_model_providers().get(model)
    if owners and len(owners) == 1:
        return next(iter(owners))
    return None


def provider_for_model(
    agent: str,
    model: str,
    command: Sequence[str] | None = None,
) -> str | None:
    """The provider route DSH needs for ``model``, if known.

    Accepts the composite ``provider/model`` spelling the Agents panel stores
    as well as a bare id from the desktop profile or the last ``--dump-config``
    parse.  ``None`` means "no provider route is known": the runtime then
    writes only the ``agent-default-model`` overlay and lets DSH report the
    unknown selection.

    A configured model can reach the adapter before the Agents panel ever asked
    for a model list, so the dump is run lazily once when the id is unknown.
    ``command`` is the already-resolved DSH command; passing it makes that lazy
    probe use the same executable the adapter will run, instead of re-reading
    paths from whatever config happens to be on disk.
    """
    global _DSH_PROVIDERS, _DSH_DEFAULT, _DSH_PROVIDER_LOADED
    if agent == "zcode":
        # ZCode stores a composite ``provider/model`` id, with only the first
        # slash separating the two (a model id may contain further slashes).
        if not model or model == "default" or "/" not in model:
            return None
        provider, _, tail = model.partition("/")
        return provider if provider and tail else None
    if agent != "dsh" or not model or model == "default":
        return None

    # A bare model id can itself contain a slash (OpenRouter's
    # ``deepseek/deepseek-v4.1-flash-1017``).  Prefer an exact desktop match
    # before interpreting the slash as a provider separator.
    owners = _desktop_model_providers().get(model)
    if owners and len(owners) == 1:
        return next(iter(owners))

    if "/" in model:
        provider, _, tail = model.partition("/")
        if provider and tail:
            return provider
    if model in _DSH_PROVIDERS:
        return _DSH_PROVIDERS[model]
    if owners:
        # Ambiguous: the same bare id is served by several providers and only
        # the UI's composite spelling can tell them apart.
        return None
    if not _DSH_PROVIDER_LOADED:
        _DSH_PROVIDER_LOADED = True
        if command:
            returncode, stdout, _stderr = detect._run_probe(
                list(command),
                ["--profile", "headless", "--dump-config"],
                detect.MODELS_TIMEOUT,
            )
            if returncode == 0:
                parsed = parse_dsh_config(stdout)
                _DSH_PROVIDERS = dict(parsed.get("providers") or {})
                _DSH_DEFAULT = parsed.get("default")
        else:
            try:
                list_models("dsh", refresh=True)
            except Exception:  # noqa: BLE001 - an unknown model is reported by the caller
                logger.debug("could not refresh the dsh model list", exc_info=True)
    return _DSH_PROVIDERS.get(model)


# --- ZCode -----------------------------------------------------------------
#
# Unlike DSH, ZCode does not expose its model list through the CLI: the CLI has
# no ``--model`` flag and a ``/model`` sent as a ``-p`` prompt is consumed as a
# real (billed) turn.  The catalog therefore lives in two read-only places:
#
#     ~/.zcode/v2/provider_config.json   enabled providers and models
#     ~/.zcode/cli/db/db.sqlite          the model of the latest session
#
# The CLI itself can only run the current model, so the runtime refuses a CLI
# model mismatch.  Translation of a non-current model goes through the official
# app-server's ``workspace/generateText`` instead (no session, no current-model
# change); the catalog therefore marks every enabled model selectable.


def zcode_home() -> Path:
    """The ZCode state directory (``$ZCODE_HOME``, else ``~/.zcode``)."""
    override = (os.environ.get("ZCODE_HOME") or "").strip()
    return Path(override).expanduser() if override else Path.home() / ".zcode"


def zcode_provider_config_path() -> Path | None:
    """``<ZCODE_HOME>/v2/provider_config.json``, if it exists."""
    path = zcode_home() / "v2" / "provider_config.json"
    try:
        return path if path.is_file() else None
    except OSError:
        return None


def parse_zcode_provider_config(
    text_or_path: str | os.PathLike[str],
    current: tuple[str, str] | None = None,
) -> dict[str, Any]:
    """Parse ZCode's ``provider_config.json`` into the model-list shape.

    Returns ``{"models", "current", "providers"}``:

    * ``models`` — ``[{id, label, provider, current, selectable}]`` with the
      composite ``provider/model`` id, in ``providerOrder`` order (providers
      missing from that list follow, in definition order).
    * ``current`` — the ``(provider, model)`` pair that was passed in, if any.
    * ``providers`` — only ``{providerId: providerName}``; provider configs can
      contain credentials and are deliberately dropped here.

    Only providers whose ``enabled`` is not ``False`` and models whose
    ``config.enabled`` is not ``False`` are kept.  A missing/unreadable/invalid
    file yields empty structures rather than an exception: the settings panel
    must still render.
    """
    empty = {"models": [], "current": None, "providers": {}}
    text = _profile_source_text(text_or_path)
    if not text:
        return empty
    try:
        data = json.loads(text)
    except ValueError:
        return empty
    if not isinstance(data, dict):
        return empty
    config = data.get("config")
    if not isinstance(config, dict):
        config = data

    provider_rules: Any = config.get("providerConfigRules")
    if isinstance(provider_rules, dict):
        provider_rules = provider_rules.get("providerRules")
    providers: dict[str, str] = {}
    if isinstance(provider_rules, list):
        for rule in provider_rules:
            if not isinstance(rule, dict) or rule.get("enabled") is False:
                continue
            provider_id = str(
                rule.get("providerId") or rule.get("id") or ""
            ).strip()
            if not provider_id:
                continue
            name = str(
                rule.get("providerName") or rule.get("name") or ""
            ).strip()
            providers[provider_id] = name or provider_id

    order: list[str] = []
    raw_order = config.get("providerOrder")
    if isinstance(raw_order, list):
        for entry in raw_order:
            provider_id = str(entry or "").strip()
            if provider_id and provider_id not in order:
                order.append(provider_id)
    ordered_providers = [
        provider_id for provider_id in order if provider_id in providers
    ]
    ordered_providers += [
        provider_id for provider_id in providers if provider_id not in ordered_providers
    ]

    model_rules: Any = config.get("modelConfigRules")
    if not isinstance(model_rules, dict):
        model_rules = {}
    merged: list[dict[str, Any]] = []
    for key in ("providerModelRules", "manualProviderModelRules"):
        entries = model_rules.get(key)
        if isinstance(entries, list):
            merged.extend(entry for entry in entries if isinstance(entry, dict))

    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for rule in merged:
        provider_id = str(rule.get("providerId") or "").strip()
        model_id = str(rule.get("modelId") or rule.get("id") or "").strip()
        if not provider_id or not model_id or provider_id not in providers:
            continue
        deduped.setdefault((provider_id, model_id), rule)

    by_provider: dict[str, list[str]] = {}
    for (provider_id, model_id), rule in deduped.items():
        rule_config = rule.get("config")
        if isinstance(rule_config, dict) and rule_config.get("enabled") is False:
            continue
        by_provider.setdefault(provider_id, []).append(model_id)

    current_pair: tuple[str, str] | None = None
    if current is not None:
        try:
            current_provider, current_model = current
        except (TypeError, ValueError):
            current_provider, current_model = "", ""
        current_provider = str(current_provider or "").strip()
        current_model = str(current_model or "").strip()
        if current_provider and current_model:
            current_pair = (current_provider, current_model)

    models: list[dict[str, Any]] = []
    for provider_id in ordered_providers:
        for model_id in by_provider.get(provider_id, ()):
            is_current = current_pair == (provider_id, model_id)
            models.append(
                {
                    "id": f"{provider_id}/{model_id}",
                    "label": f"{model_id} ({providers[provider_id]})",
                    "provider": provider_id,
                    "current": is_current,
                    # Non-current ZCode models are translatable through the
                    # official app-server's one-off ``workspace/generateText``
                    # (no session, no current-model change).  The current model
                    # keeps using the CLI.
                    "selectable": True,
                }
            )
    return {"models": models, "current": current_pair, "providers": providers}


def _zcode_message_rows(connection: sqlite3.Connection) -> list[str]:
    """Recent assistant-message payloads, newest session first.

    The database is ~700 MB, so the query is deliberately two-step: pick the
    few newest non-subagent sessions from the small ``session`` table, then use
    the ``(session_id, time_created, id)`` index for each session's messages.
    A single ``message JOIN session ... ORDER BY session.time_created`` would
    sort every message in every session before applying ``LIMIT``.
    """
    try:
        cursor = connection.execute(
            "SELECT id FROM session WHERE id NOT LIKE 'sess_subagent_%' "
            "ORDER BY time_created DESC LIMIT 20"
        )
        session_ids = [row[0] for row in cursor if isinstance(row[0], str)]
    except sqlite3.Error:
        return []

    rows: list[str] = []
    message_queries = (
        "SELECT data FROM message WHERE session_id = ? "
        "ORDER BY time_created DESC, id DESC LIMIT 100",
        # A minimal/hand-rolled database may not have the ordering columns.
        "SELECT data FROM message WHERE session_id = ? ORDER BY rowid DESC LIMIT 100",
    )
    for session_id in session_ids:
        for sql in message_queries:
            try:
                cursor = connection.execute(sql, (session_id,))
                rows.extend(row[0] for row in cursor if isinstance(row[0], str))
            except sqlite3.Error:
                continue
            break
        if len(rows) >= 200:
            break
    return rows


def _zcode_model_from_message(data: str) -> tuple[str, str] | None:
    """Extract ``(providerId, modelId)`` from one ``message.data`` JSON blob."""
    try:
        payload = json.loads(data)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    if str(payload.get("role") or "").strip().lower() != "assistant":
        return None
    provider = ""
    model = ""
    selection = payload.get("modelSelection")
    if isinstance(selection, dict):
        provider = str(selection.get("providerId") or "").strip()
        model = str(selection.get("modelId") or "").strip()
    elif isinstance(selection, str) and "/" in selection:
        provider, _, model = selection.partition("/")
        provider, model = provider.strip(), model.strip()
    if not provider:
        provider = str(payload.get("providerId") or "").strip()
    if not model:
        model = str(payload.get("modelId") or "").strip()
    if provider and model:
        return (provider, model)
    return None


def zcode_current_model(refresh: bool = False) -> tuple[str, str] | None:
    """The model of ZCode's most recent non-subagent session.

    Reads ``<ZCODE_HOME>/cli/db/db.sqlite`` read-only.  A missing, locked or
    corrupt database is not an error: the caller treats ``None`` as "unknown"
    and leaves the CLI's own selection alone.
    """
    global _ZCODE_CURRENT_CACHE
    now = time.monotonic()
    if (
        not refresh
        and _ZCODE_CURRENT_CACHE is not None
        and now - _ZCODE_CURRENT_CACHE[0] < _ZCODE_CURRENT_TTL
    ):
        return _ZCODE_CURRENT_CACHE[1]

    value: tuple[str, str] | None = None
    path = zcode_home() / "cli" / "db" / "db.sqlite"
    try:
        uri = f"{path.as_uri()}?mode=ro" if path.is_file() else ""
    except (OSError, ValueError):
        uri = ""
    if uri:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(uri, uri=True, timeout=2.0)
            for data in _zcode_message_rows(connection):
                value = _zcode_model_from_message(data)
                if value is not None:
                    break
        except sqlite3.Error:
            value = None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error:
                    pass
    _ZCODE_CURRENT_CACHE = (now, value)
    return value


def _zcode_model_list(refresh: bool = False) -> dict[str, Any]:
    """The ZCode profile's enabled models, with the current one marked."""
    path = zcode_provider_config_path()
    if path is None:
        return {"models": [], "current": None, "providers": {}}
    current = zcode_current_model(refresh=refresh)
    return parse_zcode_provider_config(path, current=current)


def _models_command(
    agent: str, resolved: detect.ResolvedAgent
) -> tuple[list[str], list[str]] | None:
    command = list(resolved.command or ())
    if not command:
        return None
    if agent == "codex":
        return command, ["debug", "models"]
    if agent == "workbuddy":
        return command, ["-p", "--help"]
    if agent == "dsh":
        return command, ["--profile", "headless", "--dump-config"]
    return None


def _cached(agent: str, resolved: detect.ResolvedAgent, refresh: bool) -> dict[str, Any] | None:
    key = (agent, resolved.path)
    now = time.monotonic()
    if not refresh:
        cached = _MODELS_CACHE.get(key)
        if cached and now - cached[0] < MODELS_TTL:
            return dict(cached[1])
    return None


def _store(agent: str, resolved: detect.ResolvedAgent, result: dict[str, Any]) -> dict[str, Any]:
    _MODELS_CACHE[(agent, resolved.path)] = (time.monotonic(), dict(result))
    return result


def _probe_models(
    agent: str, resolved: detect.ResolvedAgent
) -> tuple[list[dict[str, str]], str | None]:
    """Run the agent's enumeration command; returns ``(models, error)``."""
    global _DSH_PROVIDERS, _DSH_DEFAULT, _DSH_PROVIDER_LOADED
    plan = _models_command(agent, resolved)
    if plan is None:
        return [], None
    command, args = plan
    returncode, stdout, stderr = detect._run_probe(command, args, detect.MODELS_TIMEOUT)
    if returncode is None:
        return [], f"the model-list command did not finish within {detect.MODELS_TIMEOUT:g}s"
    if returncode != 0:
        tail = _truncate(stderr or stdout, 200)
        return [], f"the model-list command exited with code {returncode}" + (
            f": {tail}" if tail else ""
        )
    if agent == "codex":
        models = parse_codex_models(stdout)
        return models, None if models else "could not parse the model list from `codex debug models`"
    if agent == "workbuddy":
        models = parse_codebuddy_help(stdout or stderr)
        return models, None if models else "could not find 'Currently supported' in `codebuddy -p --help`"
    if agent == "dsh":
        parsed = parse_dsh_config(stdout)
        _DSH_PROVIDERS = dict(parsed.get("providers") or {})
        _DSH_DEFAULT = parsed.get("default")
        _DSH_PROVIDER_LOADED = True
        models = parsed.get("models") or []
        return models, None if models else "could not find any models in `dsh --dump-config`"
    return [], None


def _desktop_status() -> dict[str, Any]:
    """The WorkBuddy desktop card status, never fatal."""
    try:
        return workbuddy_desktop.desktop_status()
    except Exception as exc:  # noqa: BLE001 - a status probe must not break the list
        logger.debug("could not read the WorkBuddy desktop status", exc_info=True)
        return {
            "available": False,
            "auth": "unknown",
            "app_path": None,
            "error": str(exc),
        }


def _desktop_model_rows(models: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Turn the desktop catalog into the Agents panel's model rows."""
    rows: list[dict[str, str]] = []
    for model in models:
        model_id = str(model.get("id") or "").strip()
        if not model_id:
            continue
        credit = str(model.get("credits") or model.get("credit") or "").strip()
        row: dict[str, str] = {
            "id": model_id,
            "label": workbuddy_desktop.model_label(
                str(model.get("name") or model_id), credit
            ),
        }
        if credit:
            row["credit"] = credit
        rows.append(row)
    return rows


def not_installed_message(resolved: detect.ResolvedAgent) -> str:
    defn = resolved.defn
    reason = resolved.reason or "not_installed"
    if reason == "app_only":
        return (
            f"{defn.name}: the desktop app is installed, but its bundled CLI "
            f"cannot be used. Install the standalone CLI ({defn.install_url}) "
            "or set agents.paths." + defn.id
        )
    if reason == "node_missing":
        return (
            f"{defn.name} needs Node.js, which was not found on PATH. Install "
            "Node.js and try again."
        )
    if reason == "invalid_path":
        return (
            f"{defn.name}: the configured path {resolved.path!r} is not a usable "
            "executable. Fix agents.paths." + defn.id
        )
    return (
        f"{defn.name} was not found. Install it from {defn.install_url} or set "
        f"agents.paths.{defn.id} to the executable path."
    )


def list_models(
    agent: str,
    refresh: bool = False,
    paths: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return ``{"models": [{id,label}], "source":
    "profile"|"cli"|"desktop"|"fallback", "error"?}``.

    DSH prefers the desktop profile's real model list; WorkBuddy falls back to
    its desktop app when no standalone CLI exists; the other agents use their
    own enumeration commands.  Never raises for a missing or misbehaving
    backend: the fallback list plus an ``error`` string is what the settings
    panel shows.
    """
    defn = get_agent_def(agent)
    configured = detect.configured_paths() if paths is None else paths
    resolved = detect.resolve_agent(defn, configured, refresh=refresh)

    if agent == "dsh":
        # The desktop profile is the only place the user's real model list
        # lives; the headless dump knows one built-in route.  Try it first, and
        # only fall through to the dump when it has nothing to say.
        cached = _cached(agent, resolved, refresh)
        if cached is not None:
            return cached
        desktop = load_dsh_desktop_profile(refresh=refresh)
        desktop_models = desktop.get("models") or []
        if desktop_models:
            return _store(
                agent,
                resolved,
                {
                    "models": [
                        {"id": "default", "label": "Current model"},
                        *desktop_models,
                    ],
                    "source": "profile",
                },
            )

    if agent == "zcode":
        # The real model list is the profile on disk; the CLI itself has no
        # enumeration command and must never be started just to list models.
        cached = _cached(agent, resolved, refresh)
        if cached is not None:
            return cached
        parsed = _zcode_model_list(refresh=refresh)
        if parsed.get("models"):
            return _store(
                agent,
                resolved,
                {"models": parsed["models"], "source": "profile"},
            )
        if not resolved.installed:
            return fallback_models(agent, not_installed_message(resolved))
        return fallback_models(agent)

    if agent == "workbuddy" and not (resolved.installed and resolved.command):
        # The desktop app is a complete backend by itself: its own login, the
        # gateway's CLI roster, and no CLI to install.  Only taken when the
        # standalone `codebuddy`/`cbc` CLI is absent, so an installed CLI keeps
        # exactly the behaviour it had before.
        cached = _cached(agent, resolved, refresh)
        if cached is not None:
            return cached
        status = _desktop_status()
        if status.get("available"):
            try:
                credential = workbuddy_desktop.workbuddy_credential(refresh=refresh)
                fetched = workbuddy_desktop.fetch_models(credential)
            except workbuddy_desktop.WorkBuddyError as exc:
                result = fallback_models(agent, str(exc))
                result["desktop"] = _desktop_status()
                return _store(agent, resolved, result)
            except Exception as exc:  # noqa: BLE001 - never break the panel
                logger.debug("WorkBuddy desktop catalog failed", exc_info=True)
                result = fallback_models(agent, str(exc))
                result["desktop"] = status
                return _store(agent, resolved, result)

            rows = _desktop_model_rows(fetched)
            if rows:
                return _store(
                    agent,
                    resolved,
                    {
                        "models": rows,
                        "source": "desktop",
                        "desktop": _desktop_status(),
                    },
                )
            result = fallback_models(
                agent, "the WorkBuddy desktop app returned no usable models"
            )
            result["desktop"] = _desktop_status()
            return _store(agent, resolved, result)

    if not resolved.installed:
        return fallback_models(agent, not_installed_message(resolved))

    # Claude has no model-list command; its built-in list is the answer, not a
    # failure.
    if agent == "claude":
        return fallback_models(agent)

    cached = _cached(agent, resolved, refresh)
    if cached is not None:
        return cached

    models, error = _probe_models(agent, resolved)
    if not models:
        return _store(agent, resolved, fallback_models(agent, error))
    if agent == "dsh":
        # "default" means "leave the CLI's current selection alone".
        models = [{"id": "default", "label": "Current model"}, *models]
    return _store(agent, resolved, {"models": models, "source": "cli"})


__all__ = [
    "FALLBACK_MODELS",
    "desktop_provider_for_model",
    "dsh_default_model",
    "dsh_desktop_profile_path",
    "dsh_home",
    "dsh_provider_config",
    "fallback_models",
    "list_models",
    "load_dsh_desktop_profile",
    "not_installed_message",
    "parse_codebuddy_help",
    "parse_codex_models",
    "parse_dsh_config",
    "parse_dsh_desktop_profile",
    "parse_zcode_provider_config",
    "provider_for_model",
    "sanitize_provider_config",
    "zcode_current_model",
    "zcode_home",
    "zcode_provider_config_path",
]
