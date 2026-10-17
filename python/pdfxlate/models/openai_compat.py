"""OpenAI-compatible chat-completions adapter.

One adapter covers the large majority of services in use today: OpenAI itself,
DeepSeek, Qwen/DashScope, Moonshot, GLM, Groq, xAI, OpenRouter, SiliconFlow,
and any local vLLM / Ollama / LM Studio server exposing the OpenAI schema.

Three request knobs are first-class because they are what people actually need
to vary per model:

* ``reasoning_effort`` — forwarded verbatim (o-series, GLM, and others).
* ``extra_body`` — arbitrary provider-specific fields, passed through untouched
  (e.g. Qwen's ``chat_template_kwargs.enable_thinking``, vLLM's ``top_k``).
* ``stream`` — some servers only behave correctly one way or the other.
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
class OpenAICompatAdapter(ModelAdapter):
    """Adapter for any endpoint speaking the OpenAI chat-completions API."""

    name = "openai_compat"

    def __init__(self, profile: Any, lang_in: str, lang_out: str, prompt=None, ignore_cache=False):
        super().__init__(profile, lang_in, lang_out, prompt=prompt, ignore_cache=ignore_cache)

        if not profile.base_url:
            raise ValueError(
                "profile.base_url is required for the openai_compat provider"
            )
        if not profile.api_key:
            raise ValueError(
                "profile.api_key is required (set it in config.yaml or via ${ENV_VAR})"
            )

        self.model = profile.model
        self.stream = profile.stream
        self.reasoning_effort = profile.reasoning_effort
        self.extra_body = profile.extra_body or {}

        # Sampling options assembled once and reused per request.  temperature
        # is omitted when unset: some reasoning models reject it outright, and
        # leaving the field blank in Settings is how that is expressed.
        self.options: dict[str, Any] = {}
        if profile.temperature is not None:
            self.options["temperature"] = profile.temperature
        if profile.top_p is not None:
            self.options["top_p"] = profile.top_p
        if profile.max_tokens is not None:
            self.options["max_tokens"] = profile.max_tokens

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
            "messages": self.build_messages(text),
            **self.options,
        }
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        if self.extra_body:
            kwargs["extra_body"] = self.extra_body
        return kwargs

    # --- translation ------------------------------------------------------
    # Rate limits and transient server errors are retried with exponential
    # backoff; a long document can issue hundreds of calls, so one 429 must
    # not abort the job.
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
        response = self.client.chat.completions.create(
            stream=self.stream, **self._request_kwargs(text)
        )
        if self.stream:
            parts = []
            finish_reason = None
            for chunk in response:
                if chunk.choices:
                    choice = chunk.choices[0]
                    if choice.delta.content:
                        parts.append(choice.delta.content)
                    # The reason arrives on the last chunk, often with an empty
                    # delta, so it has to be read on every chunk and kept.
                    finish_reason = choice.finish_reason or finish_reason
            content = "".join(parts)
        else:
            if not response.choices:
                raise ValueError(f"empty response from model: {response!r}")
            finish_reason = response.choices[0].finish_reason
            content = response.choices[0].message.content or ""
        if finish_reason == "length":
            raise TruncatedResponseError(getattr(self.profile, "max_tokens", None), text)
        return content.strip()
