"""Model lists for the external agent CLIs.

Each agent answers this question differently, and one of them (Claude) cannot
answer it at all.  The rule is therefore "try the CLI's own enumeration, fall
back to a small built-in list, and never raise": a model list is a convenience,
and failing to enumerate one must not make the settings panel unusable.

The parsers are pure functions on purpose — the shapes are the part worth
testing, and they are tested without spawning anything.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Mapping, Sequence

import yaml

from pdfxlate.agents import detect
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


def provider_for_model(
    agent: str,
    model: str,
    command: Sequence[str] | None = None,
) -> str | None:
    """The provider route DSH needs for ``model``, if known.

    Accepts an explicit ``provider/model`` spelling as well as an id from the
    last ``--dump-config`` parse.  ``None`` means "use the current default",
    which the runtime expresses by omitting ``--patch``.

    A configured model can reach the adapter before the Agents panel ever asked
    for a model list, so the dump is run lazily once when the id is unknown.
    ``command`` is the already-resolved DSH command; passing it makes that lazy
    probe use the same executable the adapter will run, instead of re-reading
    paths from whatever config happens to be on disk.
    """
    global _DSH_PROVIDERS, _DSH_DEFAULT, _DSH_PROVIDER_LOADED
    if agent != "dsh" or not model or model == "default":
        return None
    if "/" in model:
        provider, _, tail = model.partition("/")
        if provider and tail:
            return provider
    if model in _DSH_PROVIDERS:
        return _DSH_PROVIDERS[model]
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
    """Return ``{"models": [{id,label}], "source": "cli"|"fallback", "error"?}``.

    Never raises for a missing or misbehaving CLI: the fallback list plus an
    ``error`` string is what the settings panel shows.
    """
    defn = get_agent_def(agent)
    configured = detect.configured_paths() if paths is None else paths
    resolved = detect.resolve_agent(defn, configured, refresh=refresh)
    if not resolved.installed:
        return fallback_models(agent, not_installed_message(resolved))

    # Claude has no model-list command and ZCode has only its current model;
    # their built-in lists are the answer, not a failure.
    if agent in ("claude", "zcode"):
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
    "fallback_models",
    "list_models",
    "not_installed_message",
    "parse_codebuddy_help",
    "parse_codex_models",
    "parse_dsh_config",
    "provider_for_model",
]
