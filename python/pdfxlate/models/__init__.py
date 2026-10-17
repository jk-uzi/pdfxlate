"""Translation backends.  Importing this package registers every adapter."""

from pdfxlate.models.base import (
    DEFAULT_PROMPT,
    PERMANENT_TRANSLATION_ERRORS,
    EmptyTranslationError,
    ModelAdapter,
    PlaceholderLossError,
    TruncatedResponseError,
    build_adapter,
    get_adapter,
    register,
    registered,
    strip_reasoning,
)

__all__ = [
    "DEFAULT_PROMPT",
    "PERMANENT_TRANSLATION_ERRORS",
    "EmptyTranslationError",
    "ModelAdapter",
    "PlaceholderLossError",
    "TruncatedResponseError",
    "build_adapter",
    "get_adapter",
    "register",
    "registered",
    "strip_reasoning",
]
