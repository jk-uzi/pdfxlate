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
  A provider holds the endpoint and key once, with any number of models under
  it.
- Settings live in a left sidebar of collapsible groups; the right pane is a
  PDF preview with Original / Translated / Compare views, paging and zoom, so
  the result can be checked against the source without leaving the app.
- Can be driven by other agents over MCP, using either a configured model or
  the agent's own model. See [Using it from another agent](#using-it-from-another-agent-mcp).
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

External agent (MCP client)      Python MCP server
        │                                │
        └────────── MCP over stdio ──────┘  same core, same config.yaml
```

The PDF engine is Python, packaged with PyInstaller and shipped inside the
app. The UI never touches Node directly — it goes through a narrow
`contextBridge` API.

`stdin`/`stdout` was chosen over a local HTTP server because it binds no port
(no firewall prompt), needs no auth token, and dies with its parent process.
The MCP server makes the same choice for the same reasons.

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
.venv/bin/pdfxlate paper.pdf --provider deepseek --model chat -o out/
.venv/bin/pdfxlate --list-models          # provider -> model tree
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
default_provider: deepseek
default_model: chat

# A provider holds the endpoint and credentials once; list any number of
# models under it. Adding a second model from the same service needs no
# repetition of the URL or key.
providers:
  deepseek:
    kind: openai_compat
    base_url: https://api.deepseek.com/v1
    api_key: ${DEEPSEEK_API_KEY}   # expanded from the environment
    models:
      chat:
        model: deepseek-chat
        temperature: 0             # 0 keeps the {vN} formula markers intact
      reason:
        model: deepseek-reasoner
        reasoning_effort: high     # optional, forwarded verbatim

  local_vllm:
    base_url: http://127.0.0.1:8000/v1
    api_key: dummy
    models:
      qwen:
        model: Qwen/Qwen3-32B
        extra_body:                # arbitrary provider-specific fields
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

A `profile` used to be a single endpoint+model; a config written that way (a
flat `profiles:` block) is migrated automatically on load, and a profile name
still resolves as a model alias, so existing files and `--profile` command
lines keep working.

## Using it from another agent (MCP)

Other agents can drive pdfxlate over the [Model Context
Protocol](https://modelcontextprotocol.io), either with a model you configured
in the app or with **the agent's own model**.

```bash
cd python && .venv/bin/pip install -e ".[mcp]"
```

That installs the `mcp` SDK (an optional extra, so the desktop bundle does not
carry it) and the `pdfxlate-mcp` entry point. Point your MCP client at it:

```json
{
  "mcpServers": {
    "pdfxlate": {
      "command": "/path/to/python/.venv/bin/pdfxlate-mcp",
      "args": ["--config-dir", "/path/to/your/userData"]
    }
  }
}
```

`--config-dir` makes the server read the *same* `config.yaml` as the desktop
app, so the providers and models you configured in the UI are the ones it uses.
On Windows the executable is `.venv\Scripts\pdfxlate-mcp.exe`.

Tools exposed:

| Tool | What it does |
|---|---|
| `list_providers` | Lists configured providers and their models (never the API key) |
| `extract_segments` | Collects the translatable paragraphs and returns a `job_id` |
| `typeset_translations` | Typesets a job with translations you supply |
| `translate_pdf` | Does the whole job in one call |

`translate_pdf` takes `translator="provider"` (the default — uses a model from
`config.yaml`) or `translator="client"`, which asks **your** model to do the
translating via MCP sampling and requires the client to support the `sampling`
capability. The two-phase `extract_segments` + `typeset_translations` pair is
the same engine made explicit, and works with any client: collect the
paragraphs, translate them however you like, hand them back.

Configuration is **read-only** over MCP: there is deliberately no tool that
adds a provider, edits a model or writes an API key, so pointing an agent at
this server cannot change your setup.

The server is a separate stdio process, so it shares your config, cache and
downloaded assets, but not the desktop app's in-memory state: it does not reuse
an already-loaded layout model, and its jobs do not appear in the app's
progress pane.

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

Import it from `pdfxlate/models/__init__.py` and set `kind: my_backend` on a
provider. The PDF pipeline needs no changes — it only requires
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
- In the Compare view the two panes scroll independently: the preview uses
  Chromium's built-in PDF viewer inside a `file://` frame, which cannot be
  scripted from the page around it. Only the page number is synchronised.

## License

AGPL-3.0-or-later, matching the upstream project this was derived from.
PyMuPDF and MuPDF are likewise AGPL; a commercial license is required from
Artifex for proprietary distribution.
