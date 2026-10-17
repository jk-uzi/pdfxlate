"""Discover the models a provider actually offers (``GET .../models``).

Kept separate from the adapters because it is a different concern: the adapters
translate, this only asks "what does this endpoint say it has?" so the settings
panel can offer a checklist instead of making the user type model ids from
memory.

Two conventions, three shapes:

* OpenAI-compatible endpoints (chat completions and responses alike) expose
  ``{base_url}/models`` with a bearer token and answer
  ``{"data": [{"id": ...}, ...]}``.
* Anthropic exposes ``{base_url}/v1/models`` with ``x-api-key`` and the
  ``anthropic-version`` header, and paginates with ``has_more`` / ``last_id``.

A few gateways return a bare array instead of a ``data`` envelope; both are
accepted, because guessing wrong is worse than accepting both.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from pdfxlate.models.anthropic_messages import ANTHROPIC_VERSION

logger = logging.getLogger(__name__)

#: Discovery is one small GET; this bounds a hanging endpoint.
DEFAULT_TIMEOUT = 15.0

#: Anthropic paginates. Stops a misbehaving server from looping forever.
_MAX_PAGES = 10


class CatalogError(RuntimeError):
    """Listing models failed in a way worth showing to the user verbatim."""


def models_url(kind: str, base_url: str) -> str:
    """The models endpoint for a provider, tolerating a trailing ``/v1``."""
    base = base_url.rstrip("/")
    if kind == "anthropic_messages":
        # Same rule as the messages endpoint: never produce /v1/v1/models.
        return f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
    return f"{base}/models"


def _headers(kind: str, api_key: str) -> dict[str, str]:
    if kind == "anthropic_messages":
        return {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION}
    return {"Authorization": f"Bearer {api_key}"}


def _extract(payload: Any) -> tuple[list[str], bool, str | None]:
    """Model ids from a response, plus Anthropic's pagination hints."""
    if isinstance(payload, list):
        items: Any = payload
    elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
        items = payload["data"]
    else:
        return [], False, None

    ids = [
        str(item["id"])
        for item in items
        if isinstance(item, dict) and item.get("id")
    ]
    if isinstance(payload, dict) and payload.get("has_more"):
        last = payload.get("last_id")
        return ids, True, str(last) if last else None
    return ids, False, None


def list_models(
    kind: str,
    base_url: str,
    api_key: str,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[str]:
    """Return the model ids the endpoint reports.

    Sorted and de-duplicated. Sorted rather than in server order on purpose: a
    checklist is easier to scan alphabetically, and some gateways return their
    catalogue in an order that changes between calls.

    Raises :class:`CatalogError` with the HTTP status in the message, so a
    wrong API format reads as "HTTP 404 from …/models" rather than as an
    unexplained empty list.
    """
    if not base_url:
        raise CatalogError("base_url is required")
    if not api_key:
        raise CatalogError("api_key is required")

    url = models_url(kind, base_url)
    headers = _headers(kind, api_key)
    found: list[str] = []
    after: str | None = None

    for _page in range(_MAX_PAGES):
        params: dict[str, str] = {}
        if kind == "anthropic_messages":
            # A large page keeps the common case to a single request.
            params["limit"] = "1000"
            if after:
                params["after_id"] = after
        try:
            response = requests.get(url, headers=headers, params=params, timeout=timeout)
        except requests.RequestException as exc:
            raise CatalogError(f"could not reach {url}: {exc}") from exc

        if response.status_code >= 400:
            raise CatalogError(
                f"HTTP {response.status_code} from {url}: {_error_text(response)}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise CatalogError(f"{url} did not return JSON") from exc

        ids, has_more, last_id = _extract(payload)
        found.extend(ids)
        if not has_more or not last_id or last_id == after:
            break
        after = last_id

    # De-duplicating is not optional: some gateways list an alias and the
    # underlying id as separate entries.
    return sorted(dict.fromkeys(found))


def _error_text(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if isinstance(error, str):
            return error
    return str(payload)[:200]
