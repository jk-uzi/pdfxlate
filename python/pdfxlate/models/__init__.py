"""Translation backends.  Importing this package registers every adapter."""

from pdfxlate.models.base import (
    DEFAULT_PROMPT,
    ModelAdapter,
    PlaceholderLossError,
    build_adapter,
    get_adapter,
    register,
    registered,
    strip_reasoning,
)

__all__ = [
    "DEFAULT_PROMPT",
    "ModelAdapter",
    "PlaceholderLossError",
    "build_adapter",
    "get_adapter",
    "register",
    "registered",
    "strip_reasoning",
]
