# pdfxlate

A cross-platform desktop app that translates PDF documents while preserving
their original layout — columns, headings, figures, tables and math formulas.
Point it at any OpenAI-compatible model endpoint.

Built as an independent project, using
[PDFMathTranslate](https://github.com/Byaidu/PDFMathTranslate) as a reference
for the PDF layout and typesetting pipeline.

## What it does

- Translates PDF text into a target language and writes the translation back
  into the original page geometry, so figures and formulas stay in place.
- Produces two outputs per input file:
  - `*-mono.pdf` — translated text only
  - `*-dual.pdf` — original and translated pages interleaved
- Detects layout regions (title, body text, figure, table, formula) with a
  DocLayout-YOLO ONNX model, so protected regions are left untouched.
- Supports any OpenAI-compatible API: OpenAI, DeepSeek, Qwen, Moonshot, GLM,
  Groq, xAI, OpenRouter, SiliconFlow, and local vLLM / Ollama / LM Studio.
- Ships with an English / Simplified Chinese interface. The first run picks the
  language from the OS locale; the switcher in the title bar overrides it and
  the choice is remembered.

## Architecture

```
Electron (TypeScript)            Python sidecar
  renderer  — UI                   pipeline — parse, detect layout,
  main      — window, IPC                    translate, typeset
  preload   — contextBridge        models   — model adapters
        │                                │
        └──── newline-delimited JSON ────┘
              over stdin/stdout
```

The PDF engine is Python, packaged with PyInstaller and shipped inside the
app. The UI never touches Node directly — it goes through a narrow
`contextBridge` API.

`stdin`/`stdout` was chosen over a local HTTP server because it binds no port
(no firewall prompt), needs no auth token, and dies with its parent process.

## Requirements

- Node.js 18+ and npm (for the desktop shell)
- Python 3.11–3.12 (for the PDF engine)
- An API key for an OpenAI-compatible endpoint

## Setup

```bash
# 1. Python engine
cd python
python3 -m venv .venv
.venv/bin/pip install -e .
cd ..

# 2. Desktop shell
npm install

# 3. Enable the pre-commit secret scan (once per clone)
git config core.hooksPath .githooks
```

Step 3 installs a git hook that blocks credentials from being committed. It
uses `gitleaks` if present and falls back to pattern matching, so it has no
network or Python requirement.

```bash
# optional: install the full scanner for entropy-based detection
go install github.com/zricethezav/gitleaks/v8@latest
```

Copy `.env.example` to `.env` to set API keys locally; `.env` is gitignored.

## Running

```bash
# Desktop app (development)
npm run dev

# Command line
cd python
.venv/bin/pdfxlate paper.pdf --profile deepseek -o out/
```

The first translation downloads the layout model (75 MB) and a CJK font. Both
are cached under the platform cache directory and shared between the CLI and
the desktop app. Where `huggingface.co` is unreachable, the downloader falls
back to `hf-mirror.com` automatically.

## Configuration

`config.yaml` lives in the platform config directory:

| Platform | Location |
|---|---|
| Linux | `~/.config/pdfxlate/config.yaml` |
| macOS | `~/Library/Application Support/pdfxlate/config.yaml` |
| Windows | `%APPDATA%\pdfxlate\config.yaml` |

```yaml
default_profile: deepseek

profiles:
  deepseek:
    provider: openai_compat
    base_url: https://api.deepseek.com/v1
    api_key: ${DEEPSEEK_API_KEY}   # expanded from the environment
    model: deepseek-chat
    temperature: 0                 # 0 keeps the {vN} formula markers intact
    reasoning_effort: high         # optional, forwarded verbatim
    extra_body: {}                 # arbitrary provider-specific fields

  local_vllm:
    provider: openai_compat
    base_url: http://127.0.0.1:8000/v1
    api_key: dummy
    model: Qwen/Qwen3-32B
    extra_body:
      chat_template_kwargs:
        enable_thinking: true

translation:
  lang_in: en
  lang_out: zh
  thread: 4
  mode: fast                       # fast | ultrafast

layout:
  backend: auto                    # auto | cpu | cuda | dml

cache:
  enabled: true
```

`${VAR}` is read from the environment. An unset variable expands to empty and
is reported as "not configured" rather than being sent as a literal string.
Saving the config never writes an expanded secret back to the file.

## Adding a model backend

Adapters are plugins. A new backend is one decorated class:

```python
# python/pdfxlate/models/my_backend.py
from pdfxlate.models.base import ModelAdapter, register

@register
class MyBackend(ModelAdapter):
    name = "my_backend"            # registry key and cache namespace

    def do_translate(self, text: str) -> str:
        ...                        # return the translation, keep {vN} intact
```

Import it from `pdfxlate/models/__init__.py` and set `provider: my_backend`
in a profile. The PDF pipeline needs no changes — it only requires
`translate(text) -> str` and a `lang_out` attribute.

## Layout modes

- **fast** (default) — renders each page and runs DocLayout-YOLO to find
  regions, so figures, tables and formulas are protected. Best quality.
- **ultrafast** — uses native PDF text positions instead of the model. No
  model download, faster, but degrades on complex multi-column layouts.

## Building installers

```bash
./scripts/build.sh
```

This builds the Python sidecar with PyInstaller, compiles the TypeScript, and
packages with electron-builder. Output lands in `dist/`.

PyInstaller output is platform-specific, so each target must be built on its
own OS (or in CI with an OS matrix).

Signing is not configured. Unsigned builds trigger Gatekeeper on macOS and a
SmartScreen warning on Windows.

## Tests

```bash
cd python && .venv/bin/python -m pytest ../tests -q
npm run typecheck     # main/preload and renderer
npm run check:i18n    # English/Chinese string tables stay in sync
```

## Adding a UI language

The interface strings live in two flat tables in `src/renderer/i18n.ts`
(`EN` / `ZH`). To add a locale, extend the `Locale` union, `LOCALES`,
`LOCALE_LABELS` and `MESSAGES`, then run `npm run check:i18n` — it fails
loudly if a key, a `{placeholder}` or a value is missing. Static markup is
tagged with `data-i18n*` attributes; strings built in code go through `t()`.

The main process keeps its own small table for native dialog titles
(`DIALOG_STRINGS` in `src/main/main.ts`) because it compiles as CommonJS and
must not pull in the renderer's browser-dependent module.

## Known limitations

- The text-wrapping step inherits its behaviour from the reference
  implementation. Very long translations of short source blocks can exceed the
  original box by a few points on the right margin. Keeping `temperature: 0`
  and a concise prompt reduces this.
- `--ultrafast` does not protect figures/tables by class, only by proximity.
- Encrypted PDFs are not supported.
- Scanned PDFs need OCR; PyMuPDF's built-in OCR is used for image-only pages.

## License

AGPL-3.0-or-later, matching the upstream project this was derived from.
PyMuPDF and MuPDF are likewise AGPL; a commercial license is required from
Artifex for proprietary distribution.
