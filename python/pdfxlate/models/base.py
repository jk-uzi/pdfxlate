"""Model adapter contract and plugin registry.

The PDF pipeline needs only three things from a translation backend:

* ``translate(text) -> str``
* a ``lang_out`` attribute (picks the line-height table)
* a ``no_cache`` flag (the ultrafast path toggles it)

Anything satisfying that duck type works.  Adapters subclass :class:`ModelAdapter`
to get caching, prompt rendering and the ``{vN}`` placeholder guard for free.

Registering a new backend is one decorated class — the PDF pipeline never needs
to change, unlike the upstream project where each service had to be added to
three separate dispatch tables.
"""

from __future__ import annotations

import abc
import logging
import re
from string import Template
from typing import Any, Callable, ClassVar

from pdfxlate.cache import TranslationCache

logger = logging.getLogger(__name__)

# Placeholders the typesetter injects for formulas.  A translation that drops
# or mangles these corrupts the output PDF, so they are validated after every
# response.  See converter.py, which emits "{vN}" and parses it back with the
# same shape.
_PLACEHOLDER_RE = re.compile(r"\{\s*v(\d+)\s*\}")

DEFAULT_PROMPT = (
    "You are a professional translation engine for scientific documents. "
    "Translate the source text into ${lang_out}. "
    "Keep the formula notation {v*} unchanged and in place. "
    "Output only the translated text, with no explanation or extra commentary.\n\n"
    "Source Text: ${text}\n\n"
    "Translated Text:"
)

# Reasoning models emit their chain of thought in tags before the answer.  It
# must be stripped or it gets typeset into the PDF.
_THINK_RE = re.compile(
    r"<(think|thinking|reasoning)>.*?</\1>", re.DOTALL | re.IGNORECASE
)
# Some servers stream the opening tag only, or leave an unclosed trailing block.
_THINK_OPEN_RE = re.compile(r"^.*?</(?:think|thinking|reasoning)>", re.DOTALL | re.IGNORECASE)


def strip_reasoning(text: str) -> str:
    """Remove chain-of-thought markup from a model response."""
    cleaned = _THINK_RE.sub("", text)
    # Handle a leading unclosed block, e.g. "reasoning...</think>answer".
    if "</think>" in cleaned.lower():
        cleaned = _THINK_OPEN_RE.sub("", cleaned)
    return cleaned.strip()


def placeholders(text: str) -> set[str]:
    return {m.group(1) for m in _PLACEHOLDER_RE.finditer(text)}


class PlaceholderLossError(RuntimeError):
    """Raised when a response drops a formula placeholder present in the input."""

    def __init__(self, missing: set[str], text: str, response: str) -> None:
        self.missing = missing
        self.text = text
        self.response = response
        super().__init__(
            f"translation dropped formula placeholder(s) {sorted(missing)}; "
            f"input={text[:120]!r} response={response[:120]!r}"
        )


class ModelAdapter(abc.ABC):
    """Base class for every translation backend."""

    #: Registry key.  Also used as the cache engine name, so keep it short.
    name: ClassVar[str] = "base"

    def __init__(
        self,
        profile: Any,
        lang_in: str,
        lang_out: str,
        prompt: str | None = None,
        ignore_cache: bool = False,
    ) -> None:
        self.profile = profile
        self.lang_in = lang_in
        self.lang_out = lang_out
        self.prompt_template = Template(prompt or DEFAULT_PROMPT)
        self.ignore_cache = ignore_cache
        # The ultrafast path sets this to bypass cache reads and writes.
        self.no_cache = False
        self.cache = TranslationCache(self.name, self._cache_params())

    # --- cache identity -------------------------------------------------
    def _cache_params(self) -> dict[str, Any]:
        """Everything that changes model output, folded into the cache key."""
        return {
            "lang_in": self.lang_in,
            "lang_out": self.lang_out,
            "model": getattr(self.profile, "model", None),
            "temperature": getattr(self.profile, "temperature", None),
            "top_p": getattr(self.profile, "top_p", None),
            "max_tokens": getattr(self.profile, "max_tokens", None),
            "reasoning_effort": getattr(self.profile, "reasoning_effort", None),
            "extra_body": getattr(self.profile, "extra_body", None),
            "prompt": self.prompt_template.template,
        }

    # --- prompt ---------------------------------------------------------
    def build_messages(self, text: str) -> list[dict[str, str]]:
        """Render the prompt template into an OpenAI-style message list."""
        content = self.prompt_template.safe_substitute(
            lang_in=self.lang_in, lang_out=self.lang_out, text=text
        )
        return [{"role": "user", "content": content}]

    # --- translation ----------------------------------------------------
    @abc.abstractmethod
    def do_translate(self, text: str) -> str:
        """Perform one API call.  Implementations must not touch the cache."""

    def translate(self, text: str, ignore_cache: bool = False) -> str:
        """Translate one paragraph, consulting the cache first.

        Formula placeholders present in the input are verified to survive;
        a model that rewrites ``{v0}`` into something else would silently
        corrupt the PDF, so that is treated as a failure worth retrying.
        """
        use_cache = not (self.ignore_cache or ignore_cache or self.no_cache)
        if use_cache:
            if cached := self.cache.get(text):
                return cached

        response = self.do_translate(text)
        cleaned = strip_reasoning(response)
        self._check_placeholders(text, cleaned)

        if not self.no_cache:
            self.cache.set(text, cleaned)
        return cleaned

    def _check_placeholders(self, source: str, response: str) -> None:
        expected = placeholders(source)
        if not expected:
            return
        missing = expected - placeholders(response)
        if missing:
            raise PlaceholderLossError(missing, source, response)

    def __str__(self) -> str:
        return f"{self.name} {self.lang_in}->{self.lang_out} {getattr(self.profile, 'model', '?')}"


# --- registry --------------------------------------------------------------
_REGISTRY: dict[str, type[ModelAdapter]] = {}


def register(cls: type[ModelAdapter]) -> type[ModelAdapter]:
    """Class decorator registering an adapter under its ``name``."""
    if not getattr(cls, "name", None) or cls.name == "base":
        raise ValueError(f"{cls.__name__} must define a unique 'name'")
    if cls.name in _REGISTRY:
        raise ValueError(f"adapter name {cls.name!r} is already registered")
    _REGISTRY[cls.name] = cls
    return cls


def get_adapter(provider: str) -> type[ModelAdapter]:
    try:
        return _REGISTRY[provider]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise ValueError(f"unknown provider {provider!r}; registered: {known}") from None


def registered() -> list[str]:
    return sorted(_REGISTRY)


def build_adapter(
    provider: str,
    profile: Any,
    lang_in: str,
    lang_out: str,
    prompt: str | None = None,
    ignore_cache: bool = False,
    cache_enabled: bool = True,
) -> ModelAdapter:
    """Instantiate the adapter registered for ``provider``.

    ``cache_enabled`` mirrors ``CacheSettings.enabled``: when false the
    adapter's cache is switched off entirely, so nothing is read or written.
    ``ignore_cache`` is unrelated — it only skips the cache for a single
    :meth:`ModelAdapter.translate` call.
    """
    adapter = get_adapter(provider)(
        profile,
        lang_in,
        lang_out,
        prompt=prompt,
        ignore_cache=ignore_cache,
    )
    # Adapter subclasses predate this flag and do not take it as a constructor
    # argument, so apply it to the cache object here.  TranslationCache treats
    # ``enabled=False`` as a full bypass of get/set/clear.
    adapter.cache.enabled = cache_enabled
    return adapter


# Import adapters so their @register decorators run.
from pdfxlate.models import openai_compat as _openai_compat  # noqa: E402,F401
