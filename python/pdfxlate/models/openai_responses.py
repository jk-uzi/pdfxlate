"""OpenAI Responses API adapter (``POST /responses``).

A second OpenAI shape, not a second vendor.  It differs from chat/completions
in more than the path, and the differences are exactly what would silently
produce an empty translation if this were bolted onto the chat adapter:

* the prompt goes in ``input`` (a string or a list of message items), not
  ``messages``;
* the output cap is ``max_output_tokens``, not ``max_tokens``;
* reasoning effort is nested (``reasoning={"effort": ...}``), not top-level;
* the reply is a list of output items, and the text is the concatenation of
  the ``output_text`` blocks — ``response.output_text`` does that for us.

The ``openai`` SDK already ships the endpoint, so this adds no dependency.
"""

from __future__ import annotations

import logging
from typing import Any

import openai
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from pdfxlate.models.base import ModelAdapter, TruncatedResponseError, register

logger = logging.getLogger(__name__)


@register
class OpenAIResponsesAdapter(ModelAdapter):
    """Adapter for endpoints speaking the OpenAI Responses API."""

    name = "openai_responses"

    def __init__(self, profile: Any, lang_in: str, lang_out: str, prompt=None, ignore_cache=False):
        super().__init__(profile, lang_in, lang_out, prompt=prompt, ignore_cache=ignore_cache)

        if not profile.base_url:
            raise ValueError(
                "profile.base_url is required for the openai_responses provider"
            )
        if not profile.api_key:
            raise ValueError(
                "profile.api_key is required (set it in config.yaml or via ${ENV_VAR})"
            )

        self.model = profile.model
        self.stream = profile.stream
        self.reasoning_effort = profile.reasoning_effort
        self.extra_body = profile.extra_body or {}

        # Same knobs as the chat adapter, mapped onto this API's names below.
        # temperature is omitted when unset, for models that reject it.
        self.options: dict[str, Any] = {}
        if profile.temperature is not None:
            self.options["temperature"] = profile.temperature
        if profile.top_p is not None:
            self.options["top_p"] = profile.top_p
        if profile.max_tokens is not None:
            self.options["max_output_tokens"] = profile.max_tokens

        self.client = openai.OpenAI(
            base_url=profile.base_url,
            api_key=profile.api_key,
            timeout=120.0,
            max_retries=0,  # tenacity owns retries so behaviour is uniform
        )

    # --- request building -------------------------------------------------
    def _request_kwargs(self, text: str) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "input": self.build_messages(text),
            **self.options,
        }
        if self.reasoning_effort:
            kwargs["reasoning"] = {"effort": self.reasoning_effort}
        if self.extra_body:
            kwargs["extra_body"] = self.extra_body
        return kwargs

    # --- translation ------------------------------------------------------
    @retry(
        retry=retry_if_exception_type(
            (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError)
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
        if self.stream:
            # `create(stream=True)` yields the raw event stream.  The SDK's
            # `stream()` helper additionally runs a state machine that requires
            # `response.created`, `output_item.added` and friends in order; the
            # only events this adapter needs are the text deltas and the final
            # `incomplete`, so the raw stream is both simpler and less likely to
            # break on a provider that emits a slightly reduced event set.
            parts: list[str] = []
            incomplete = False
            for event in self.client.responses.create(
                stream=True, **self._request_kwargs(text)
            ):
                # Only the visible answer: reasoning-summary deltas are a
                # different event type and must not be typeset.
                if event.type == "response.output_text.delta":
                    parts.append(event.delta)
                elif event.type == "response.incomplete":
                    incomplete = True
            if incomplete:
                raise TruncatedResponseError(getattr(self.profile, "max_tokens", None), text)
            return "".join(parts).strip()

        response = self.client.responses.create(**self._request_kwargs(text))
        # `incomplete` is how this API reports "ran out of output budget";
        # the reason says whether it was the token cap or a content filter.
        if getattr(response, "status", None) == "incomplete":
            details = getattr(response, "incomplete_details", None)
            reason = getattr(details, "reason", None)
            if reason in (None, "max_output_tokens"):
                raise TruncatedResponseError(getattr(self.profile, "max_tokens", None), text)
        content = response.output_text
        if not content:
            raise ValueError(f"empty response from model: {response!r}")
        return content.strip()
