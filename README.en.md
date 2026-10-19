# pdfxlate

[简体中文](README.md) | **English**

A desktop app that translates PDF documents while preserving their original
layout — columns, headings, figures, tables and math formulas all stay where
they were. Point it at any OpenAI-compatible model endpoint.

[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0--or--later-blue.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/jk-uzi/pdfxlate)](https://github.com/jk-uzi/pdfxlate/releases)
[![Platform](https://img.shields.io/badge/platform-Windows%20x64%20%7C%20Linux-lightgrey)]()

![The pdfxlate window: document list and options on the left, translated page on the right](docs/images/screenshot.png)

## Contents

- [What it does](#what-it-does)
- [Download and install](#download-and-install)
- [Run from source](#run-from-source)
- [Using it](#using-it)
  - [Desktop app](#desktop-app)
  - [Command line](#command-line)
  - [Output files](#output-files)
  - [Driving it from another agent (MCP)](#driving-it-from-another-agent-mcp)
- [Configuration](#configuration)
- [Supported model services](#supported-model-services)
- [Architecture](#architecture)
- [Building installers](#building-installers)
- [Tests](#tests)
- [Limitations and troubleshooting](#limitations-and-troubleshooting)
- [Contributing](#contributing)
- [License and credits](#license-and-credits)

## What it does

- **Layout preserved.** Each page is segmented into regions (title, body text,
  figure, table, formula); only prose is translated, and the translation is
  typeset back at the original font size and line width. Figures and formulas
  are left untouched.
- **Two outputs per input:**
  - `*-mono.pdf` — translated text only
  - `*-dual.pdf` — source and translated pages interleaved, for proofreading
- **Two layout modes:**
  - `fast` (default) — renders each page and runs the DocLayout-YOLO ONNX model,
    so figures and tables are protected by class. Best quality.
  - `ultrafast` — uses native PDF text positions instead of the model. No model
    download and considerably faster, but degrades on complex multi-column
    layouts.
- **Any OpenAI-compatible service:** OpenAI, DeepSeek, Qwen (DashScope),
  Moonshot, Zhipu GLM, Groq, xAI, OpenRouter, SiliconFlow, and local vLLM /
  Ollama / LM Studio. OpenAI Responses and Anthropic Messages shapes are also
  supported. A provider holds the endpoint and key once, with any number of
  models underneath.
- **External agent CLIs:** in Settings → Agents, point the translator at a model
  from Claude Code, Codex, WorkBuddy/CodeBuddy, ZCode or DeepSeek Harness and use
  that CLI's own login and quota — no API key needed. A CLI outside `PATH` can
  be located with a manual path.
- **Desktop app:** browse a folder in the left sidebar and tick the files to
  translate; the PDF preview sits in the right pane of the same window. Opened
  documents become tabs you can drag into a different order. Translation outputs
  are listed alongside their sources and marked "translated", so a result can be
  compared with its original without leaving the app.
- **Translation cache:** a local SQLite cache keyed on engine, parameters and
  source text, so repeated paragraphs are not billed twice. The end of a job
  reports hits and misses.
- **MCP:** other agents can drive the translator, either with a model you
  configured in the app or with **their own** model.
- **English / Simplified Chinese interface:** picked from the OS locale on first
  run, switchable under Settings → Appearance, and remembered.

## Download and install

Get the latest Windows installer from
[Releases](https://github.com/jk-uzi/pdfxlate/releases)
(`pdfxlate-Setup-0.2.0.exe`, about 193 MB).

- **Requirements:** Windows 10 or 11, 64-bit. The installer is per-user; no
  administrator rights are needed.
- **Unsigned build:** there is no code-signing certificate, so SmartScreen shows
  "Windows protected your PC" on the first run. Choose *More info → Run anyway*.
- **The first translation needs a network:** the default Chinese font ships
  inside the installer, but the layout model (about 75 MB) is downloaded on first
  use. `huggingface.co` is tried first and `hf-mirror.com` is the fallback; the
  endpoint that worked is remembered. Translating also needs access to whichever
  model service you configure.
- If startup fails with an ONNX Runtime DLL error, install the
  [Microsoft Visual C++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe).

Packaging is configured for Linux and macOS as well, but only the Windows
installer and a Linux AppImage have actually been built and smoke-tested. macOS
bundles are unverified; build them yourself if you need one.

## Run from source

Requires Node.js 18+ and Python 3.11–3.12.

```bash
# 1. Python engine
cd python
python -m venv .venv
.venv/bin/pip install -e .            # macOS / Linux
# .venv\Scripts\pip install -e .      # Windows
cd ..

# 2. Desktop shell
npm install

# 3. Enable the pre-commit secret scan (once per clone)
git config core.hooksPath .githooks
```

Step 3 installs a git hook that blocks credentials from being committed. It uses
`gitleaks` when present and falls back to pattern matching, so it has no network
or Python requirement. For entropy-based detection:

```bash
go install github.com/zricethezav/gitleaks/v8@latest
```

Start the app:

```bash
npm run dev      # development build, opens DevTools
npm start        # ordinary launch
```

## Using it

### Desktop app

1. Click *Choose…* and pick a folder. Its PDFs are listed on the left, including
   translations already written into the `translated/` subfolder.
2. Tick the files to translate, then pick a model, source and target language,
   layout mode and output mode under *Options*.
3. Press *Start translation*. Progress, cache hits and errors appear at the
   bottom of the sidebar; the same button cancels a running job.
4. Results land in `translated/`, appear in the file list marked as translated,
   and open in the preview when clicked.

Dropping a PDF onto the window files it into the current folder and opens it.

### Command line

```bash
cd python
.venv/bin/pdfxlate paper.pdf --provider deepseek --model chat -o out/
.venv/bin/pdfxlate --list-models              # provider -> model tree
.venv/bin/pdfxlate paper.pdf --pages 1-5 --output-mode mono
```

| Option | Meaning |
|---|---|
| `--provider` / `--model` | Provider and model alias; omitted means the first enabled model in the file |
| `-o, --output` | Output directory; default is `translated/` beside each source |
| `--output-mode` | `mono` / `dual` / `both` (CLI default `both`; the UI offers the first two) |
| `--pages` | Restrict to pages, e.g. `1-5,8` |
| `--mode` | `fast` (default) or `ultrafast` |
| `--thread` | Worker threads |
| `--lang-in` / `--lang-out` | Source and target language |
| `--ignore-cache` | Ignore the cache for this run |
| `--backend` | ONNX backend: `auto` / `cpu` / `cuda` / `dml` |
| `--config` | Path to a `config.yaml` |
| `--config-dir` | Override the **config** directory only (cache and downloaded models stay in the platform directory, so CLI, desktop and MCP share one copy) |

### Output files

Translating `paper.pdf` produces:

```
translated/paper-mono.pdf    translated text only
translated/paper-dual.pdf    source and translation interleaved
```

Existing files are never overwritten: a new run adds ` (2)`, ` (3)` and so on,
numbering the mono and dual files together.

### Driving it from another agent (MCP)

pdfxlate can act as a [Model Context Protocol](https://modelcontextprotocol.io)
server, so another agent can call it — using **its own** model (client sampling)
or one you configured in the app (provider).

The easy path is *Settings → MCP* in the app: it shows a snippet already filled
in with the right command for your install, and can write it into Claude Desktop,
Claude Code, Codex, WorkBuddy or ZCode when present. Writing backs the target
file up first and adds only its own entry, leaving everything else alone. Any
other client gets the snippet pasted by hand.

From a source checkout, install the optional extra first:

```bash
cd python
.venv/Scripts/pip install -e ".[mcp]"    # Windows
.venv/bin/pip install -e ".[mcp]"        # macOS / Linux
```

A hand-written config looks like this (on Windows, `.venv\Scripts\python.exe`):

```json
{
  "mcpServers": {
    "pdfxlate": {
      "command": "/path/to/python/.venv/bin/python",
      "args": ["-m", "pdfxlate.sidecar", "--mcp",
               "--config-dir", "/path/to/your/userData"]
    }
  }
}
```

`--config-dir` makes the server read the *same* `config.yaml` as the desktop app,
so the providers and models you configured in the UI are the ones it uses.

Tools exposed:

| Tool | What it does |
|---|---|
| `list_providers` | Lists configured providers and models (never the API key) |
| `extract_segments` | Collects the translatable paragraphs and returns a `job_id` |
| `typeset_translations` | Typesets a job with translations you supply |
| `translate_pdf` | Does the whole job in one call |

`translate_pdf` takes `translator="provider"` (a model from `config.yaml`; an
unspecified provider means the first one in the file) or `translator="client"`,
which asks **your** model to do the translating via MCP sampling and requires
the client to support `sampling`. The two-phase `extract_segments` +
`typeset_translations` pair is the same engine made explicit, and works with any
client: collect the paragraphs, translate them however you like, hand them back.

Configuration is **read-only** over MCP: there is deliberately no tool that adds
a provider, edits a model or writes an API key, so pointing an agent at this
server cannot change your setup.

The server is a separate stdio process. It shares your config, cache and
downloaded assets, but not the desktop app's in-memory state: it does not reuse
an already-loaded layout model, and its jobs do not appear in the app's progress
pane.

## Configuration

`config.yaml` lives in the platform config directory:

| Platform | Location |
|---|---|
| Windows | `%APPDATA%\pdfxlate\config.yaml` |
| macOS | `~/Library/Application Support/pdfxlate/config.yaml` |
| Linux | `$XDG_CONFIG_HOME/pdfxlate/config.yaml` (default `~/.config/pdfxlate/`) |

The desktop app uses Electron's `userData` directory, which resolves to the same
place. A commented template is written on first run.

```yaml
# This file describes what exists; it does not say which one to use. The model
# for a run is chosen per run (the options panel, --provider/--model, or an MCP
# tool argument), so there is no "default model" to set.
providers:
  deepseek:
    kind: openai_compat
    base_url: https://api.deepseek.com/v1
    api_key: ${DEEPSEEK_API_KEY}   # expanded from the process environment
    models:
      chat:
        model: deepseek-chat
        temperature: 0             # 0 keeps the {vN} formula markers intact
        context_length: 65536      # the model's window; used to warn, not to
                                   # set max_tokens for you
      reason:
        model: deepseek-reasoner
        reasoning_effort: high     # optional, forwarded verbatim
        max_tokens: 8192           # optional; omitted leaves it to the API

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

Notes:

- `${VAR}` is read from the **process environment**. An unset variable expands to
  empty and is reported as "not configured" rather than being sent as a literal
  string. Saving the config never writes an expanded secret back to the file.
- The project does **not** load a `.env` file (there is no dotenv dependency).
  `.env.example` only lists the conventional variable names; export them through
  your shell, the OS environment or your launcher.
- `--config-dir` redirects the **config** directory only. Cache and downloaded
  models/fonts always use the platform directory so the CLI, the desktop app and
  MCP share one copy instead of downloading the same 75 MB model three times.
- A legacy flat `profiles:` block is migrated to `providers:` on load, and
  `--profile` on old command lines still works.
- Validation is strict: unknown fields are an error, so a typo is never silently
  ignored.

## Supported model services

| API shape (`kind`) | Endpoint | Typical services |
|---|---|---|
| `openai_compat` (default) | `/v1/chat/completions` | OpenAI, DeepSeek, DashScope, Moonshot, GLM, Groq, xAI, OpenRouter, SiliconFlow, vLLM, Ollama, LM Studio |
| `openai_responses` | `/responses` | services exposing the Responses API |
| `anthropic_messages` | `/v1/messages` | Anthropic Claude |

Every model can set `temperature`, `top_p`, `max_tokens`, `stream`,
`reasoning_effort`, `context_length` and `extra_body` independently. Requests
retry with exponential backoff (up to eight attempts), and the translation is
checked for intact `{vN}` formula placeholders before anything is written to the
page.

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
provider. The PDF pipeline needs no changes.

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

- **Why stdin/stdout rather than a local HTTP server:** it binds no port (no
  firewall prompt), needs no auth token, and dies with its parent process. The
  MCP server makes the same choice for the same reasons.
- **The PDF engine is Python:** `pdfminer.six` parses content streams, PyMuPDF
  renders and writes back, and ONNX Runtime runs the layout model. The engine is
  packaged with PyInstaller and shipped inside the app.
- **The UI never touches Node directly:** the renderer reaches the main process
  only through a narrow `contextBridge` API, with `contextIsolation` on and
  `nodeIntegration` off.
- **Cache:** SQLite (`cache.v1.db`, WAL mode) keyed on engine, parameters and
  source text, so the same paragraph is cached separately per model. A finished
  job reports both local hits and, when the service returns them, provider-side
  prompt-cache token counts.

The file-by-file map ("which file do I change for feature X") is in
[docs/project-structure.md](docs/project-structure.md); architecture and porting
decisions are in [docs/development-plan.md](docs/development-plan.md) (both in
Chinese).

## Building installers

```bash
npm install
bash scripts/build.sh          # Git Bash on Windows
```

The script stages the Chinese font into the sidecar bundle, builds the Python
engine with PyInstaller, compiles the TypeScript, and packages with
electron-builder. Output lands in `dist/`:

- `dist/win-unpacked/` — runnable app directory (useful for a smoke test)
- `dist/pdfxlate Setup <version>.exe` — NSIS installer for Windows
- `npm run pack` builds only the unpacked directory

For scale: `win-unpacked/` is about 589 MB unpacked (mostly the Python runtime
and its dependencies) and the installer is about 193 MB.

The first electron-builder run downloads NSIS and winCodeSign. `scripts/build.sh`
points it at the npmmirror binary mirror by default because `github.com` release
assets are unreachable from some networks; override with
`ELECTRON_BUILDER_BINARIES_MIRROR` if you prefer another mirror.

PyInstaller output is platform-specific, so each target must be built on its own
OS. Signing is not configured: unsigned builds trigger SmartScreen on Windows and
Gatekeeper on macOS.

## Tests

```bash
cd python && .venv/bin/python -m pytest ../tests -q
# Windows: .venv\Scripts\python -m pytest ..\tests -q

npm run typecheck     # main/preload and renderer
npm run check:i18n    # English and Chinese string tables stay in sync
```

The Python suite covers adapters against a real local HTTP server (including
SSE), configuration and migration, cache and placeholders, layout and
typesetting, the sidecar protocol and cancellation, and the two-phase MCP flow.
The frontend has no automated DOM tests; UI changes are verified by hand and
with DevTools.

## Limitations and troubleshooting

- **Long translations can overflow** the original box by a few points on the
  right margin, because wrapping inherits the reference implementation's
  behaviour. Keeping `temperature: 0` and a concise prompt reduces it.
- **`ultrafast` protects less:** it avoids graphics by proximity only, not by
  class, so complex layouts are better served by `fast`.
- **Encrypted PDFs are not supported.**
- **Scanned PDFs need OCR:** image-only pages get a Tesseract text layer (the
  matching language data is downloaded). OCR is configurable only through
  `config.yaml`; there are no UI controls.
- **Preview limitations:** the preview uses Chromium's built-in PDF viewer inside
  a `file://` frame, which cannot be scripted from the page around it. The page
  number shown is the app's own record; a file dropped directly onto the page is
  handled by the viewer, while the border around it is handled by the app.
- **First run is slow:** the ~75 MB layout model is downloaded, retrying another
  endpoint if the first fails.
- **SmartScreen / Gatekeeper:** see [Download and install](#download-and-install).

## Contributing

- Enable `git config core.hooksPath .githooks`; it scans the staged changes for
  secrets and oversized files.
- False positives belong in the `.gitleaks.toml` allowlist, not in
  `--no-verify`.
- Run `npm run check:i18n` after touching UI strings, and add both languages.
- `references/` is a read-only clone of the upstream project used for comparison.
  It is not part of the build and must not be imported.
- Development notes (progress, pitfalls, open items) go in
  `requirements/development-log.md`.

## License and credits

Released under [AGPL-3.0-or-later](LICENSE).

- The layout and typesetting pipeline was written with
  [PDFMathTranslate](https://github.com/Byaidu/PDFMathTranslate) as a reference.
  This repository is an independent implementation and contains none of its code.
- [PyMuPDF](https://github.com/pymupdf/PyMuPDF) and MuPDF are likewise AGPL; a
  commercial licence from [Artifex](https://artifex.com/licensing/) is required
  for proprietary distribution.
- Layout model: [DocLayout-YOLO](https://huggingface.co/wybxc/DocLayout-YOLO-DocStructBench-onnx).
- Fonts: [Source Han Serif](https://github.com/adobe-fonts/source-han-serif)
  (SIL OFL) and [Go Noto](https://github.com/satbyy/go-noto-universal).
- OCR data: [tesseract-ocr/tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast).
