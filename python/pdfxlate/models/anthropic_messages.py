"""Anthropic Messages API adapter (``POST /v1/messages``).

Deliberately built on ``requests`` rather than the ``anthropic`` SDK: this is
one endpoint with one body shape, and adding a second vendor SDK (plus its
transitive dependencies) to a desktop bundle that is already ~250 MB is a poor
trade.  The request/response shape is small and stable.

Two things differ from the OpenAI adapters in a way that has to be handled
rather than translated away:

* ``max_tokens`` is **required**.  The config's value is used when set;
  otherwise the model's ``context_length`` / 4 (the same suggestion the UI
  shows), and failing that a conservative 4096.
* ``reasoning_effort`` has no equivalent — Anthropic expresses this as
  ``thinking.budget_tokens``, which cannot be derived from "low/medium/high"
  without guessing a number.  It is therefore not sent; put
  ``{"thinking": {...}}`` in ``extra_body`` to opt in explicitly.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterator

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from pdfxlate.models.base import ModelAdapter, register

logger = logging.getLogger(__name__)

#: Pinned because the request body below is written against this version.
ANTHROPIC_VERSION = "2023-06-01"

#: Used when neither max_tokens nor context_length is configured.
DEFAULT_MAX_TOKENS = 4096


class TransientApiError(RuntimeError):
    """A 429 or 5xx, i.e. the kind worth retrying."""


@register
class AnthropicMessagesAdapter(ModelAdapter):
    """Adapter for the Anthropic Messages API."""

    name = "anthropic_messages"

    def __init__(self, profile: Any, lang_in: str, lang_out: str, prompt=None, ignore_cache=False):
        super().__init__(profile, lang_in, lang_out, prompt=prompt, ignore_cache=ignore_cache)

        if not profile.base_url:
            raise ValueError(
                "profile.base_url is required for the anthropic_messages provider"
            )
        if not profile.api_key:
            raise ValueError(
                "profile.api_key is required (set it in config.yaml or via ${ENV_VAR})"
            )

        self.model = profile.model
        self.stream = profile.stream
        self.extra_body = profile.extra_body or {}
        self.max_tokens = self._resolve_max_tokens(profile)

        # A Session reuses the TLS connection: a long document issues hundreds
        # of calls, and a fresh handshake each time is pure latency.
        self.session = requests.Session()
        self.session.headers.update(
            {
                "x-api-key": profile.api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            }
        )
        self.url = messages_url(profile.base_url)

    @staticmethod
    def _resolve_max_tokens(profile: Any) -> int:
        if getattr(profile, "max_tokens", None):
            return int(profile.max_tokens)
        context = getattr(profile, "context_length", None)
        if context:
            # The same quarter-window suggestion the settings panel shows as a
            # placeholder; here it is a real value because the API demands one.
            return max(1, int(context) // 4)
        return DEFAULT_MAX_TOKENS

    # --- request building -------------------------------------------------
    def _body(self, text: str) -> dict[str, Any]:
        # `build_messages` renders the prompt into OpenAI-style turns; Messages
        # takes the same role/content pairs, so only the envelope differs.
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": self.build_messages(text),
        }
        if getattr(self.profile, "temperature", None) is not None:
            body["temperature"] = self.profile.temperature
        if getattr(self.profile, "top_p", None) is not None:
            body["top_p"] = self.profile.top_p
        if self.extra_body:
            # Merged rather than nested: these are top-level fields of this
            # API's body (e.g. `thinking`, `metadata`), not a passthrough bag.
            body.update(self.extra_body)
        return body

    # --- translation ------------------------------------------------------
    @retry(
        retry=retry_if_exception_type(
            (TransientApiError, requests.ConnectionError, requests.Timeout)
        ),
        stop=stop_after_attempt(8),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        before_sleep=lambda state: logger.warning(
            "transient API error, retry %d/8 in %.0fs",
            state.attempt_number,
            state.next_action.sleep if state.next_action else 0,
        ),
        reraise=True,
    )
    def do_translate(self, text: str) -> str:
        body = self._body(text)
        if self.stream:
            return self._translate_streaming(body)

        response = self.session.post(self.url, json=body, timeout=120.0)
        self._raise_for_status(response)
        payload = response.json()
        return _join_text_blocks(payload.get("content") or []).strip()

    def _translate_streaming(self, body: dict[str, Any]) -> str:
        body = {**body, "stream": True}
        with self.session.post(self.url, json=body, timeout=120.0, stream=True) as response:
            self._raise_for_status(response)
            parts = [_delta_text(event) for event in _sse_events(response.iter_lines())]
        return "".join(part for part in parts if part).strip()

    @staticmethod
    def _raise_for_status(response: requests.Response) -> None:
        if response.status_code < 400:
            return
        detail = _error_message(response)
        if response.status_code == 429 or response.status_code >= 500:
            raise TransientApiError(f"HTTP {response.status_code}: {detail}")
        raise ValueError(f"Anthropic API error (HTTP {response.status_code}): {detail}")


def messages_url(base_url: str) -> str:
    """Build the Messages endpoint from whatever the user pasted.

    People paste ``https://api.anthropic.com``, the same with ``/v1``, and
    proxies that put the version under a prefix.  Appending ``/v1/messages``
    unconditionally would produce ``/v1/v1/messages`` for the second form, so
    the version segment is only added when it is not already there.
    """
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/messages"
    return f"{base}/v1/messages"


def _error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200]
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    return json.dumps(payload)[:200]


def _join_text_blocks(blocks: list[dict[str, Any]]) -> str:
    """Concatenate the ``text`` of every ``text`` block, ignoring the rest."""
    return "".join(
        block.get("text", "")
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _sse_events(lines: Iterator[bytes | str]) -> Iterator[dict[str, Any]]:
    """Yield the JSON payload of each ``data:`` line in a server-sent stream.

    The Messages API sends ``event:``/``data:`` pairs and a final
    ``data: [DONE]``-less close; only the data lines carry content.  Blank lines
    and the ping events are skipped rather than parsed.
    """
    for raw in lines:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        line = line.strip()
        if not line or not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if not data or data == "[DONE]":
            continue
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield event


def _delta_text(event: dict[str, Any]) -> str:
    """The text carried by one streamed event, or an empty string."""
    if event.get("type") != "content_block_delta":
        return ""
    delta = event.get("delta") or {}
    # `text_delta` is the answer; `thinking_delta` is reasoning and must not be
    # typeset into the PDF.
    if delta.get("type") == "text_delta":
        return str(delta.get("text") or "")
    return ""
