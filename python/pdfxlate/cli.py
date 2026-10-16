"""Command-line interface.

    pdfxlate paper.pdf --provider deepseek --model chat -o out/
    pdfxlate paper.pdf --mode ultrafast
    pdfxlate --list-models
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from pdfxlate import __version__, config as config_mod, paths
from pdfxlate.api import translate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdfxlate",
        description="Translate PDF documents while preserving their layout.",
    )
    parser.add_argument("files", nargs="*", help="PDF files to translate")
    parser.add_argument("--version", "-v", action="version", version=f"pdfxlate {__version__}")

    parser.add_argument(
        "--provider",
        "-p",
        help="Provider from config.yaml (default: the first one in the file)",
    )
    parser.add_argument(
        "--model",
        "-m",
        help="Model alias under the provider (default: that provider's first model)",
    )
    # ``--profile`` predates the provider/model split: a profile named one flat
    # endpoint+model.  config.resolve() still accepts such a name in the
    # provider position, so keep the flag as a deprecated alias of --provider.
    parser.add_argument(
        "--profile",
        dest="provider",
        help="Deprecated alias for --provider",
    )
    parser.add_argument("--output", "-o", default="", help="Output directory")
    parser.add_argument(
        "--pages",
        help="Page range to translate, e.g. '1,3-5' (1-based)",
    )
    parser.add_argument(
        "--mode",
        choices=["fast", "ultrafast"],
        help="fast uses ONNX layout detection; ultrafast uses native text positions",
    )
    parser.add_argument("--thread", "-t", type=int, help="Concurrent translation workers")
    parser.add_argument("--lang-in", help="Source language code")
    parser.add_argument("--lang-out", help="Target language code")
    parser.add_argument("--vfont", help="Regex for formula font names to preserve")
    parser.add_argument("--vchar", help="Regex for formula characters to preserve")
    parser.add_argument("--prompt", help="Path to a custom prompt template file")

    parser.add_argument(
        "--backend",
        choices=["auto", "cpu", "cuda", "dml"],
        help="ONNX Runtime execution provider; overrides layout.backend in config",
    )
    parser.add_argument(
        "--onnx",
        metavar="PATH",
        help="Custom ONNX layout model path; overrides layout.model_path in config",
    )
    # BooleanOptionalAction makes the option tri-state: omitted leaves the
    # config value alone (None), while --skip-subset-fonts / --no-skip-subset-fonts
    # force it on/off.  A plain store_true would default to False and silently
    # override a config that sets skip_subset_fonts: true.
    parser.add_argument(
        "--skip-subset-fonts",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Skip font subsetting (better compatibility, larger output)",
    )
    parser.add_argument(
        "--ignore-cache",
        action="store_true",
        help="Ignore the translation cache for this run and force retranslation",
    )

    parser.add_argument(
        "--config-dir",
        help="Override the config/cache/assets directory (used by the desktop app)",
    )
    parser.add_argument("--config", help="Path to a config.yaml file")
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="List every provider/model pair and exit",
    )
    # ``--list-profiles`` predates providers and is kept so old command lines
    # and docs keep working.  A profile is now just a model alias, so the flag
    # is a hidden alias of --list-models and prints exactly the same tree.
    parser.add_argument(
        "--list-profiles",
        dest="list_models",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--debug", "-d", action="store_true", help="Verbose logging")

    return parser


def parse_pages(spec: str | None) -> list[int] | None:
    """Parse a 1-based page spec like ``1,3-5`` into 0-based indices."""
    if not spec:
        return None
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            pages.extend(range(int(start) - 1, int(end)))
        else:
            pages.append(int(part) - 1)
    return pages


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    paths.ensure_dirs()
    config_path = Path(args.config) if args.config else None
    cfg = config_mod.load(config_path)

    if args.list_models:
        pairs = cfg.all_models()
        if not pairs:
            print("No models configured. Edit", paths.config_file())
            return 1
        print(f"config: {paths.config_file()}")
        # There is no stored default: the first entry in the file is what an
        # unspecified call reaches, so it is marked to make that visible.
        first = pairs[0]
        # Group by provider so the provider → models nesting is visible.
        current: str | None = None
        for provider, alias in pairs:
            if provider != current:
                current = provider
                base_url = cfg.providers[provider].base_url or "(no base_url)"
                print(f"  {provider} @ {base_url}:")
            marker = " (used when unspecified)" if (provider, alias) == first else ""
            print(f"    {alias}{marker}: {cfg.resolve(provider, alias).model}")
        return 0

    if not args.files:
        build_parser().print_help()
        return 1

    # CLI overrides win over the config file.
    if args.lang_in:
        cfg.translation.lang_in = args.lang_in
    if args.lang_out:
        cfg.translation.lang_out = args.lang_out
    if args.backend:
        cfg.layout.backend = args.backend
    if args.onnx:
        cfg.layout.model_path = args.onnx
    # None means the flag was not given, so the config value stands.
    if args.skip_subset_fonts is not None:
        cfg.translation.skip_subset_fonts = args.skip_subset_fonts

    prompt = None
    if args.prompt:
        prompt = Path(args.prompt).read_text(encoding="utf-8")
        cfg.translation.prompt = prompt

    def report(stage: str, current: int, total: int, message: str) -> None:
        if total > 1:
            print(f"  [{stage}] {current}/{total} {message}", file=sys.stderr, flush=True)
        else:
            print(f"  [{stage}] {message}", file=sys.stderr, flush=True)

    try:
        results = translate(
            args.files,
            provider=args.provider,
            model=args.model,
            output=args.output or str(Path.cwd()),
            pages=parse_pages(args.pages),
            mode=args.mode,
            thread=args.thread,
            config=cfg,
            progress=report,
            vfont=args.vfont or "",
            vchar=args.vchar or "",
            ignore_cache=args.ignore_cache,
        )
    except Exception as exc:  # noqa: BLE001 - surface a clean message
        logging.error("%s", exc)
        if args.debug:
            raise
        return 1

    for mono, dual in results:
        print(f"translated -> {mono}")
        print(f"           -> {dual}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
