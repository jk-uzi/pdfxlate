"""pdfxlate — layout-preserving PDF translation with any OpenAI-compatible model."""

__version__ = "0.1.0"
__all__ = ["translate", "translate_stream"]


def __getattr__(name):
    if name in {"translate", "translate_stream"}:
        from pdfxlate.api import translate, translate_stream

        return {"translate": translate, "translate_stream": translate_stream}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
