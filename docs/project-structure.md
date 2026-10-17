# 项目结构

本文件是**代码索引**：先读这里，快速定位「改某个功能该动哪个文件」。
与项目实际结构一一对应，改动代码后请同步更新。

- 长期方向与架构决策 → [development-plan.md](development-plan.md)
- 短期开发记录 → [../requirements/development-log.md](../requirements/development-log.md)

**分工**：本文件是结构信息的唯一来源（文件树、模块职责、符号、数据位置）。
`development-plan.md` 只讲架构决策、移植对照与阶段划分，不重复维护结构。

规模：约 6000 行（不含 `python/.venv`、`references/`、`node_modules`）。
Python 3.12，venv 在 `python/.venv`，包以 editable 方式安装，改源码即时生效。
前端为 TypeScript + Electron（无前端框架），`npm run dev` 启动。

## 一、按需求定位

| 我想…… | 去改 |
|---|---|
| 接入新的模型后端 | `python/pdfxlate/models/` 新建文件 + `@register` 装饰器（内置三个 `kind`：`openai_compat` / `openai_responses` / `anthropic_messages`） |
| 调整发给模型的请求参数 | 对应适配器：`openai_compat.py` / `openai_responses.py` / `anthropic_messages.py`（`reasoning_effort` / `extra_body` / `stream` / `temperature`） |
| 改重试策略 / 取消行为 | `pdf/converter.py` 的 `translate_segment()`（有界重试）+ `models/base.py` 的 `PERMANENT_TRANSLATION_ERRORS` |
| 改「测试连接」 | `sidecar.py` 的 `method_providers_test` + `app.ts` 的 `testProvider()` |
| 改「从上游拉取模型」 | `models/catalog.py` + `sidecar.py` 的 `method_providers_models` + `app.ts` 的 `openModelsDialog()` |
| 改供应商改名 | `app.ts` 的 `renameProvider()` / `startRenamingProvider()` / `stopRenamingProvider()` |
| 改翻译提示词 | `models/base.py` 的 `DEFAULT_PROMPT`，或配置里的 `translation.prompt` 指向模板文件 |
| 改配置项 / 加新配置字段 | `config.py`（`Provider`、`ModelEntry`、`TranslationSettings` 等 pydantic 模型） |
| 改「一个供应商挂多个模型」的结构 | `config.py` 的 `Provider` / `ModelEntry` / `AppConfig.resolve()` |
| 改配置/缓存的存放位置 | `paths.py` |
| 换模型或字体的下载地址 | `assets.py`（`_LAYOUT_REPO`、`_GO_NOTO_URL`、`_SOURCE_HAN_URL`、`_hf_endpoints`） |
| 改「哪种语言用哪款字体」 | `assets.py` 的 `font_filename()`——语言→字体的**唯一来源**；`_CJK_FONTS` 与 `_NOTO_LANGS` 只是它的两张表 |
| 改翻译缓存的键或行为 | `cache.py`（`TranslationCache`）；影响输出的参数要加进 `models/base.py` 的 `_cache_params()` |
| 改 PDF 排版回填逻辑 | `pdf/converter.py`（`TranslateConverter`，最难的部分） |
| 改页面解析流程 / 阶段编排 | `pdf/pipeline.py`（`translate` / `translate_stream`） |
| 改内容流解释器 | `pdf/interp.py`（`PDFPageInterpreterEx`，上游照搬） |
| 改版面检测 | `pdf/layout.py`（`OnnxModel`，DocLayout-YOLO） |
| 改免模型的快速路径 | `pdf/ultrafast.py` |
| 改 OCR 段落重建 | `pdf/ocr.py` |
| 改中英混排间距 | `pdf/text_spacing.py`（`add_cjk_latin_spacing`） |
| 改命令行参数 | `cli.py`（`build_parser`） |
| 加新的 sidecar 方法 | `sidecar.py` 的 `METHODS` 字典 + 一个 `method_*` 函数 |
| 改 Python 公开 API | `api.py`（`translate` / `translate_stream` / `build_translator`） |
| 改外部 agent 能调用的能力（MCP 工具） | `mcp_server.py` 的 `build_server()` 里的 `@mcp.tool()` 函数 |
| 改「一键配置 MCP」/ 支持的 agent | `mcp_config.py`（写入）+ `main.ts` 的 `mcpAgents()`（探测五个 agent 的路径） |
| 改「两遍式」段落收集/回填引擎 | `pdf/two_phase.py`（`CollectingTranslator` / `DictTranslator` / `collect()` / `typeset()`） |
| 改窗口 / 应用生命周期 / IPC | `src/main/main.ts` |
| 改子进程管理与 JSON 协议 | `src/main/sidecar.ts`（`Sidecar` 类） |
| 改渲染进程可用的 API | `src/preload/preload.ts`（`contextBridge`） |
| 改 Electron 界面 | `src/renderer/app.ts` + `index.html` + `style.css` |
| 改标题栏 / 窗口按钮 | `src/main/main.ts`（`titleBarOverlay` / `TITLEBAR_HEIGHT`）+ `style.css` 的 `--titlebar-height`，两处必须一致 |
| 改应用图标 / 标记 | `src/renderer/icon.svg` 是唯一来源；改完跑 `npm run make:icon` 重新生成 `assets/`，标题栏靠构建期内联自动跟上 |
| 改任务/设置模式与设置项 | `src/renderer/app.ts` 的 `appMode` / `setAppMode()` / `paintSettingsMode()`，菜单与面板在 `index.html`；设置页版式在 `style.css` 的 `.settings-panel` / `.grid--form`；模型页的两栏布局由 `.settings-host--split` + `.model-split` 提供 |
| 改侧栏折叠 | `src/renderer/app.ts` 的 `setSidebarCollapsed()` + `style.css` 的 `body.sidebar-collapsed` |
| 改进度显示 | `src/renderer/app.ts` 的 `paintProgress()`，落点在 `index.html` 侧栏底部的 `.sidebar__status`（与设置图标同一行） |
| 改默认输出目录 | `src/main/main.ts` 的 `defaultOutputDir()`（**必须走 `app.getPath("documents")`**，别在 Python 里拼路径） |
| 改「另存为」存哪一份 | `src/renderer/app.ts` 的 `saveAsSource()`；对话框与拷贝在 `src/main/main.ts` 的 `dialog:saveAs` |
| 改选项面板（模型/推理/语言，只影响本次） | `src/renderer/app.ts` 的 `optionValues` / `selectedModel()`；引擎侧覆盖在 `api.py` 的 `translate()` |
| 改侧栏宽度 | `src/renderer/app.ts` 的 `applySidebarWidth()` / `clampSidebarWidth()` + `style.css` 的 `--sidebar-width`、`.sidebar__resizer` |
| 改浮层显隐时机 | `src/renderer/app.ts` 的 `flashViewerOverlay()` + `VIEWER_OVERLAYS` |
| 改右侧 PDF 预览（标签/翻页/缩放） | `src/renderer/app.ts` 的 `renderPreview()` / `viewerUrl()` / `activateTab()` |
| 改标签栏（宽度/箭头/拖动排序） | `src/renderer/app.ts` 的 `renderTabs()` / `updateTabbarScroll()` / `dragTabOver()` / `endTabDrag()` + `style.css` 的 `.tabbar` / `.tab` |
| 改左侧文件列表（文件夹浏览/勾选） | `src/renderer/app.ts` 的 `openFolder()` / `renderFiles()`；目录列举在 `src/main/main.ts` 的 `app:listPdfs` |
| 改拖放行为（复制/改名） | `src/renderer/app.ts` 的 `handleDropped()`；复制与改名在 `src/main/main.ts` 的 `app:importPdf` / `uniquePath()` |
| 改左侧栏分组 | `src/renderer/index.html` 的 `details.group` + `app.ts` 的 `expandGroup()` |
| 改供应商/模型的界面编辑 | `src/renderer/app.ts` 的 `renderProviderList()` / `renderModelList()` / `toggleModelEditor()` / `setModelEnabled()`；后端字段在 `config.py` 的 `ModelEntry`（含 `enabled`） |
| 改界面文案 / 加一门语言 | `src/renderer/i18n.ts`（`en`/`zh` 两张表），静态文案用 `data-i18n*` 属性标注 |
| 改原生对话框文案 | `src/main/main.ts` 的 `DIALOG_STRINGS`（主进程另有一份小表，原因见该处注释） |
| 改前端构建配置 | `package.json`、`tsconfig.node.json`、`tsconfig.renderer.json` |
| 改打包配置 | Electron → `electron-builder.yml`；Python → `python/pdfxlate.spec` |
| 加测试 | `tests/` |
| 改依赖 | Python → `python/pyproject.toml`；Node → `package.json` |

## 二、完整文件树

```
pdfxlate/
├── AGENTS.md                        agent 入口：目录定位说明
├── .gitignore                       忽略 venv / references / 下载资源 / 密钥
├── .githooks/pre-commit             提交前密钥扫描（原生 hook，零网络依赖）
├── .gitleaks.toml                   gitleaks 规则与排除路径
├── .env.example                     环境变量名清单（不含值）
├── .npmrc                           Electron 二进制下载镜像
├── package.json                     Electron 依赖与构建脚本
├── electron-builder.yml             三平台打包配置
├── tsconfig.node.json               主进程 / preload 的 TS 配置
├── tsconfig.renderer.json           渲染进程的 TS 配置
├── docs/
│   ├── development-plan.md          长期方案（架构、移植对照表、阶段、风险）
│   └── project-structure.md         ← 本文件
├── requirements/
│   └── development-log.md           短期开发记录
├── python/
│   ├── pyproject.toml               依赖、入口点、extras（ultrafast / cuda / mcp / dev）
│   ├── pdfxlate.spec                PyInstaller 打包配置（onedir）
│   └── pdfxlate/
│       ├── __init__.py              惰性暴露 translate / translate_stream
│       ├── paths.py          93 行  跨平台 config / cache / assets 路径
│       ├── config.py        491 行  pydantic 配置模型（供应商→模型）+ YAML 读写
│       ├── assets.py        349 行  模型 / 字体 / OCR 数据下载与校验
│       ├── cache.py         144 行  sqlite3 翻译缓存
│       ├── api.py           192 行  公开 Python API
│       ├── cli.py           216 行  命令行入口
│       ├── sidecar.py       407 行  stdio JSON 行协议（Electron 调用）；--mcp 转交 MCP 服务
│       ├── mcp_server.py    505 行  MCP 服务（外部 agent 调用，stdio）
│       ├── mcp_config.py    246 行  把 MCP 条目写进各 agent 配置（备份/合并/校验）
│       ├── models/
│       │   ├── __init__.py     23 行  导出翻译层公开符号
│       │   ├── base.py       326 行  ModelAdapter 基类 + 注册表 + 永久错误分类
│       │   ├── openai_compat.py 128 行  OpenAI Chat Completions 适配器
│       │   ├── openai_responses.py 136 行  OpenAI Responses 适配器
│       │   ├── anthropic_messages.py 247 行  Anthropic Messages 适配器
│       │   └── catalog.py   146 行  上游模型清单（GET .../models）
│       └── pdf/
│           ├── __init__.py      1 行
│           ├── pipeline.py   580 行  管线编排：解析→版面→翻译→排版
│           ├── converter.py  699 行  排版回填 + 字号适配 + 有界段落重试（移植上游 converter.py）
│           ├── interp.py     366 行  内容流解释器（上游照搬）
│           ├── layout.py     187 行  DocLayout-YOLO ONNX 版面检测
│           ├── ocr.py        161 行  OCR 段落重建与排版
│           ├── two_phase.py  489 行  两遍式段落收集/回填（MCP 用）
│           ├── spread.py     160 行  双栏：把页对合成一个派生 PDF（矢量，不栅格化）
│           ├── ultrafast.py  101 行  免模型快速路径
│           └── text_spacing.py 44 行  中英混排间距（上游照搬）
├── src/
│   ├── main/
│   │   ├── main.ts          473 行  窗口 + 应用生命周期 + IPC（自建标题栏、默认输出目录、另存为、目录列举、拖入归档）
│   │   └── sidecar.ts       234 行  Python 子进程管理 + JSON 行协议
│   ├── preload/
│   │   └── preload.ts       190 行  contextBridge，向渲染进程暴露 API
│   └── renderer/
│       ├── index.html       720 行  界面结构（文案走 data-i18n* 属性；图标在构建时内联）
│       ├── app.ts          3252 行  界面逻辑（任务/设置双模式、折叠、标签页、文件夹浏览、拖放、缩放、进度、另存为、选项覆盖、侧栏拖拽、供应商/模型两栏编辑器、MCP 配置）
│       ├── i18n.ts          641 行  中英文字符串表 + t()/applyStaticTranslations()
│       ├── icon.svg          49 行  应用标记的**唯一来源**（标题栏内联 + 打包图标）
│       └── style.css       1947 行  样式（含标题栏标签、查看器浮层、折叠与侧栏状态行、侧栏拖拽手柄、模型页两栏布局与开关）
├── assets/
│   ├── icon.png               512×512，由 make_icon.py 从 icon.svg 生成后提交
│   └── icon.ico               16–256 多尺寸，同上（Windows 用）
├── tests/
│   ├── test_translation.py   232 行  缓存、占位符、推理标签剥离、注册表、缓存开关、超窗口告警
│   ├── test_openai_compat.py 137 行  适配器参数透传、校验、提示词渲染
│   ├── test_openai_responses.py 146 行  Responses 形状映射、流式增量、截断、空回复
│   ├── test_adapters_http.py 359 行  三个适配器打真实本地 HTTP 服务（含 SSE）
│   ├── test_translate_segment.py 111 行  有界重试、取消、永久错误不重试
│   ├── test_sidecar_provider_test.py 172 行  providers.test 探针
│   ├── test_catalog.py      171 行  上游模型清单：URL、两种信封、翻页、错误
│   ├── test_sidecar_catalog.py 113 行  providers.models 探针
│   ├── test_config_env_refs.py 142 行  **改名前后的密钥不落明文**
│   ├── test_anthropic_messages.py 227 行  Messages 端点拼接、请求体、SSE、错误分级
│   ├── test_config_providers.py 349 行  供应商→模型 schema、resolve、迁移（含已移除字段）、env 引用
│   ├── test_typesetting.py   172 行  字号适配纯函数（放不下时的缩字逻辑）
│   ├── test_cli_options.py   286 行  CLI 排版选项解析与三态覆盖语义
│   ├── test_sidecar_providers.py 181 行  providers.list 形状、密钥不外传
│   ├── test_mcp_server.py    600 行  MCP 工具、stdio 握手、两遍式端到端与确定性护栏
│   ├── test_mcp_config.py   239 行  写入 agent 配置：合并/幂等/备份/坏文件不动
│   ├── test_sidecar_mcp_flag.py 159 行  --mcp 真的能起一个 MCP 握手
│   ├── test_pdf_spread.py    152 行  双栏派生文档：页对、宽度、缓存、坏文件
│   ├── test_per_job_overrides.py 136 行  按次覆盖语言/推理；调用方配置对象不被改动
│   └── test_assets_fonts.py  113 行  语言→字体映射、status() 兼容性
└── scripts/
    ├── copy-static.mjs       110 行  拷贝 CSS、**把 icon.svg 内联进 index.html**、校验 CSS 注释配对
    ├── make-icon.mjs          33 行  用 venv 解释器调用 make_icon.py（npm run make:icon）
    ├── make_icon.py          186 行  从 icon.svg 生成 assets/icon.png 与 .ico（PyMuPDF）
    ├── check-i18n.mjs        154 行  校验字符串表一致 + **引用的 key 确实存在**（npm run check:i18n）
    └── build.sh               40 行  一键构建：PyInstaller → tsc → electron-builder
```

## 三、模块职责与关键符号

### 基础设施

**`paths.py`** — 所有路径的唯一来源，不要别处硬编码。
`config_dir()` / `cache_dir()` / `assets_dir()` / `config_file()` / `cache_file()` / `ensure_dirs()`。
`--config-dir` 或环境变量 `PDFXLATE_CONFIG_DIR` **只覆盖 config**（Electron 传
`app.getPath('userData')`），cache 与 assets 始终走平台默认位置——这样 CLI 与
桌面端共享同一份下载资源，不会各下一份 75 MB 模型。
平台默认：Windows `%APPDATA%`、macOS `~/Library/Application Support`、
Linux `~/.config` + `~/.cache`。

**`config.py`** — `AppConfig`（`providers` / `translation` / `layout` / `cache` /
`ocr`）、`Provider`、`ModelEntry`、
`ResolvedModel`、`TranslationSettings`、`LayoutSettings`、`CacheSettings`、
`OcrSettings`、`load()`、`save()`。全部 `extra: "forbid"`，配置写错启动即报错。
`${VAR}` 引用在加载时从环境展开，保存时写回字面形式，所以密钥不会被固化进文件。

**供应商 → 模型是两层，不是扁平的。** `Provider` 持有 `kind`（适配器注册名）、
`base_url`、`api_key` 和 `models: dict[str, ModelEntry]`——一个供应商配一次凭据
和地址，下面挂任意多个模型（`models` 的键是别名，UI 与 `--model` 用它寻址）。
`ModelEntry.model` 省略时由别名回填（`_fill_model_names`），因为「快速加一个模型」
是最常见的操作。

**配置里没有"默认模型"。** 设置描述**有哪些模型可用、怎么连**；**用哪一个由每次都决定**
（选项面板、`--provider`/`--model`、或 MCP 工具参数）。曾经有 `default_provider`/
`default_model`，但它只带来混淆：UI 上那个「（默认）」标记其实只是"文件里第一个"，
没有任何地方会写它，而且**设置面板的下拉会漏进任务**——在设置里点几下，下次翻译用的就
变成了它。现已彻底移除。

`AppConfig.resolve(provider, model) -> ResolvedModel` 是**唯一的解析入口**：
补齐缺省、报错点名可用项，并把两层**拍平**成一个 `ResolvedModel`。**不指定 provider 时
回退到文件里第一个**（`all_models()[0]`），这样 CLI / MCP 不带参数仍可用；代价是**重排
config.yaml 会改变这个回退目标**。拍平这一步是
控制爆炸半径的关键——`ModelAdapter` 子类、`openai_compat.py`、`pdf/` 全都只做
`getattr(profile, "x")`，所以加这层嵌套时它们一行都没改。

**两处迁移，都为了旧文件还能打开**：

- **旧 `profiles:` 布局**（`_migrate_profiles`）：按 `(kind, base_url, api_key)` 分组，
  旧 profile 名成为模型别名；旧 `default_profile` 指的那一组**排到最前**，因为"第一个"
  就是现在不指定时的回退目标。
- **已移除的 `default_provider` / `default_model`**（`_drop_stored_default`）：**必须**丢弃，
  否则是灾难性的——`AppConfig` 是 `extra: "forbid"`，而这两个键**在旧模板里**，几乎每份
  存量配置都有，留着会让 `model_validate` 直接抛错、**引擎完全起不来**。丢弃时打 warning，
  下次保存自然消失。

`resolve()` 也接受旧 profile 名（`_find_provider_with_alias`），所以 `--profile deepseek`
这类老命令仍然可用。迁移在**内存里**做，下次保存写回新结构。

**`save()` 会在配置对象丢失 `_env_refs` 时回读磁盘上的引用**：UI 的
`config.get` → `config.save` 往返必然拿到展开后的值，若不回读，只存在于环境里的
密钥就会被明文写进 config.yaml。回读必须走 `_read_env_refs()`（它同样先做迁移），
因为迁移过的文件里引用在 `profiles.*` 而文档在 `providers.*`，路径对不上就漏——
这个坑已修并有回归测试锁住。

`ocr.language` / `ocr.tessdata_prefix` 是 OCR 的配置入口——不要再用环境变量
（早期版本从上游照搬了 `PDF2ZH_OCR_LANGUAGE`，前缀与本项目不一致，已移除）。

**`assets.py`** — `Asset` 数据类 + `ensure()` 系列（`ensure_layout_model` /
`ensure_font` / `ensure_ocr_data`）。下载带 sha256 校验、原子写入
（`.part` → rename）、镜像回退（`HF_ENDPOINT` → `huggingface.co` → `hf-mirror.com`），
并把上次成功的端点记在 `assets/.hf_endpoint` 以免每次重付连接超时。
`verify_layout_metadata()` 校验模型带 `stride` / `names` 元数据。

字体选择：`font_filename(lang)` 是**唯一来源**——CJK 查 `_CJK_FONTS` 取
SourceHanSerif 的地区字形（CN/TW/JP/KR），其余一律 GoNotoKurrent。
`_NOTO_LANGS` 列出 GoNoto 已知覆盖的 19 种非 CJK 文字，**它只用于区分「已识别的
GoNoto 语言」与「未知语言码」，不是「支持/不支持」的分界**——拉丁等脚本不在这张表里
也照样用 GoNoto。未知语言码**宽容回退**到 GoNoto 并记一条 debug 日志，而不是抛错：
目标语言来自用户输入，一个拼写错误不该让整个翻译任务失败。

`status()` 供 UI 首次运行引导使用。它**保留** `font_zh` / `font_ja` / `font_ko` 三个旧键
（前端 `app.ts` 仍读 `font_zh` 决定是否显示引导条），并在传入 `lang` 时追加
`font_ready_for`，这样目标语言是 `ru`/`ar`/`th`（GoNoto）时不会被误报缺字体。
`lang` 默认 `None` 且此时该键不出现，因此 `sidecar.py` 的无参调用行为不变。

**`cache.py`** — `TranslationCache`。stdlib sqlite3（上游用 peewee），
线程局部连接（排版从线程池调用），无 import 副作用。
主键 `(engine, params, original)`，`params` 由 `_cache_params()` 折叠所有影响
输出的参数，换参数不会命中脏缓存。
`enabled=False` 在 `get` / `set` / `clear` 全部短路，且**构造时就不开库**——
所以关掉缓存不会留下空的 `cache.v1.db`，缓存目录只读也不会导致启动失败。

### 翻译层

**`models/base.py`** — `ModelAdapter` 抽象基类（`translate()` 管缓存与占位符校验，
子类只实现 `do_translate()`）、`@register` 注册表、`PlaceholderLossError`、
`strip_reasoning()`、`DEFAULT_PROMPT`、`build_adapter()`。

`build_adapter(..., cache_enabled=)` 是 `CacheSettings.enabled` 的唯一入口：
它把开关经模块级 contextvar `_CACHE_ENABLED` 传给构造函数，`ModelAdapter.__init__`
再转交 `TranslationCache`。**为什么用 contextvar 而不是构造后赋值**：`TranslationCache`
在构造时就会开库建表，构造完再改 `enabled` 太晚，会留下空数据库文件。contextvar 只在
`build_adapter` 调用构造函数期间生效（`finally` 里 reset），因此对线程池里的调用也安全，
且**新增模型后端只需加文件 + `@register`，无需碰任何缓存接线**。

**三个内置适配器**，对应设置页「类型」下拉的三项。选哪个由 `Provider.kind` 决定，
`api.build_translator()` 把它传给注册表，PDF 管线完全不需要知道是哪一个。

**`models/openai_compat.py`** — `OpenAICompatAdapter`，`name = "openai_compat"`
（`/v1/chat/completions`）。覆盖 DeepSeek / Qwen / Kimi / Grok / GLM / 硅基流动 /
vLLM / Ollama / OpenRouter 等。一等支持 `reasoning_effort`、`extra_body`
（厂商私有参数透传）、`stream`，429 限流用 tenacity 指数退避重试。

**`models/openai_responses.py`** — `OpenAIResponsesAdapter`，`name = "openai_responses"`
（`/responses`）。同一个 `openai` SDK，但请求形状不同：prompt 进 `input` 而不是
`messages`，输出上限叫 `max_output_tokens`，推理强度是嵌套的
`reasoning={"effort": ...}`，回复用 `response.output_text` 取。流式只累加
`response.output_text.delta`，推理摘要事件不进正文。

**`models/anthropic_messages.py`** — `AnthropicMessagesAdapter`，
`name = "anthropic_messages"`（`/v1/messages`）。**不用 `anthropic` SDK**，用已有的
`requests`：一个端点、一种 body，为它再引一个厂商 SDK 及其传递依赖不划算。三处需要
注意：

- 端点拼接：`base_url` 已以 `/v1` 结尾就只补 `/messages`，否则补 `/v1/messages`，
  避免出现 `/v1/v1/messages`。
- `max_tokens` 在该 API 是必填：用模型配置值；没填则取 `context_length / 4`；
  再没有就用 4096。
- **不发 `reasoning_effort`**：Anthropic 用 `thinking.budget_tokens` 表达，
  无法从 low/medium/high 推出一个预算。要开 thinking 请在 `extra_body` 里写
  `{"thinking": {...}}`。

### 供应商（不只是模型）也有 enable/disable

`Provider.enabled`（默认 `true`，老配置无需迁移）。关掉之后：

- `all_models(only_enabled=True)` 跳过它的**全部**模型，因此选项面板不列出、无参 `resolve()`
  也不会回退到它；
- **显式点名**该供应商或它的任一模型 → 报错 `Provider ... is disabled`；
- `providers.list` / MCP `list_providers` 都多返回一个 `enabled`；
- 左栏该行加 `is-off`（名称与图标变灰），但仍然留在列表里——那是唯一能重新打开它的地方。

删除与关闭是两件事：关闭保留地址和密钥，删除才真的丢掉。界面因此同时保留垃圾桶与开关。

### 供应商改名与密钥安全

供应商在配置里是**以名称为键**的，改名 = 换键。`renameProvider()` 重建对象并**保留原位置**
（用 `delete` + 赋值会把它挪到末尾，而文件顺序决定无参调用落到哪个模型）。同时改写：
`editingProvider`、localStorage 里形如 `provider|alias` 的选项、`cachedProviders` 摘要的键。

**这里有一个会泄露密钥的坑**：`config.save` 收到的是渲染进程回传的 `config.get` 文档，其中的
`api_key` 已经是**展开后**的字面值；`_restore_env_refs` 原本靠**路径**
`providers.<名字>.api_key` 把 `${VAR}` 写回去。改名后该路径不存在，密钥就会**以明文落盘**。

`_restore_env_refs` 现在有两段：先按路径还原（行为不变），路径对不上的部分再走一次
**按值匹配**的兜底——任何等于某个 `${VAR}` 展开值的字符串都改回模板引用。这覆盖了改名、
条目重排，也覆盖了落在任意嵌套位置的同一个密钥。代价是：如果用户恰好把一个手写字面值填成
与某个环境变量完全相同，它也会被替换成引用——两个方向都会在下次加载时展开成同一个值，而不
替换则意味着明文写盘，所以这个方向的错误更轻。`tests/test_config_env_refs.py` 钉住了这条。

### 从上游拉取模型

`models/catalog.py` — `list_models(kind, base_url, api_key)` 只问「这个端点自称有哪些模型」，
与翻译适配器分开：两者关心的东西不同，合在一起会让适配器多背一个与请求无关的端点。

- `openai_compat` / `openai_responses`：`{base}/models`，`Authorization: Bearer`，
  读 `{"data": [{"id": ...}]}`；也接受裸数组（部分网关如此）。
- `anthropic_messages`：`{base}/v1/models`（已以 `/v1` 结尾时不重复拼），
  `x-api-key` + `anthropic-version`，按 `has_more` / `last_id` 翻页，上限 10 页，
  游标不前进时立即停止。

结果排序去重。失败抛 `CatalogError`，消息带 HTTP 状态与 URL——选错 API 格式在这里读作
`HTTP 404 from .../models`，与翻译请求报错时同样直接。

**不自动填 `context_length`**：各家 `/models` 返回的元数据差异太大，猜一个数字比留空更糟；
留空时「最大输出」的 placeholder 只是 1/4 窗口的建议值，不会被写进配置。

界面上：模型列表标题行的「从上游拉取」打开原生 `<dialog>`，带搜索框（OpenRouter 这类有几百
个 id）；**已配置的项勾选且禁用**并标「已添加」，避免导入时覆盖用户调好的参数。导入的别名
取 id 本身，之后可在行编辑里把别名与 API id 分开。

### 失败语义：什么会重试，什么不会

三件事必须分清，否则一个必然失败的任务会永远挂着：

1. **适配器内部**（`do_translate` 上的 tenacity）只重试 429 / 连接错误 / 5xx，8 次指数退避。
2. **段落级**（`converter.translate_segment`）在外面再加最多 3 次尝试，每次之间有 1s、2s
   退避。**永久错误直接抛出、不重试**：`PlaceholderLossError`、`TruncatedResponseError`、
   `EmptyTranslationError`（见 `base.PERMANENT_TRANSLATION_ERRORS`）。同样的输入对同一个
   模型必然同样失败，再试只是把错误信息推迟。
   - 历史：这里原本是 `@retry(wait=wait_fixed(1))`，tenacity 默认 `stop_never` +
     `retry_if_exception_type(Exception)`，即**任何异常每秒重试一次、永不停止**。选错协议
     返回 404 就会让整个任务永久卡死；而且取消标志只在翻页时检查，点取消也停不下来。
   - 现在 `translate_segment` 在**每次尝试前**检查 `cancellation_event`。它打断不了正在
     进行的 HTTP 请求（做不到），但能保证重试循环及时退出。
3. **空回复与截断**不算译文：`base.translate` 在写缓存前就抛 `EmptyTranslationError`；三个
   适配器各自检查 `finish_reason == "length"` / `status == "incomplete"` /
   `stop_reason == "max_tokens"`。以前这两种情况会被当成正常结果排版，页面上留下半句话
   或一块空白，没有任何提示。

**`temperature` 是可选参数**：`None` 表示**请求里根本不带这个字段**，这是使用拒绝该参数的
推理模型（o 系列、gpt-5 等）的唯一办法。设置页新建模型时仍写入 `0`（保护 `{vN}` 占位符），
只有用户主动清空输入框才不发送。

**排版质量与协议无关**：三个适配器都只返回一个字符串，之后走完全相同的解析、版面检测、
字号适配与回填流程。影响成品的是译文内容本身（截断、空串、占位符丢失），而这三项由上面
第 2、3 条统一处理。

### PDF 管线

**`pdf/pipeline.py`** — 编排层。`translate()` / `translate_stream()` /
`translate_patch()` / `download_font()`，内建取消支持。
产 mono（纯译文）与 dual（双语对照）两个 PDF。
上游的 doc/docx 转换与 PDF/A 输出去掉了，只处理 PDF。

**`pdf/converter.py`** — 排版回填，**项目最难的部分**。`TranslateConverter`、
`PDFConverterEx`、`Paragraph`、`OpType`。掩码值为 0 表示原样保留，≥2 归为可译段落——
这是公式、表格、图片得以保真的原因。改动前务必理解掩码语义。

`fit_font_size()` 是**纯函数**（字号、段落宽高、排版单元列表 → 最终字号与行高），
也是 `test_typesetting.py` 的测试对象——把它独立出来就是为了能脱离 PDF 构造做单元测试。
适配顺序：先用实排行数压缩行高（历史行为，下限 1.0）；压到下限仍溢出才**二分缩小字号**，
下限 `MIN_FIT_SCALE = 0.6`，到下限仍放不下才接受溢出。缩小后恢复默认行高，不再挤行。
两个关键不变量：① 本来放得下的段落走原路径，输出与修复前**逐字节一致**；
② 纯公式段落（`sstk` 剥掉 `{vN}` 后为空）`wrap=False`，沿用上游绝对坐标锚定、不换行——
一旦换行公式会错位。`wrap` 取代了原来的 `brk`（`brk` 只标记「原文段落内部有换行」，
导致原文单行的段落译文永不换行、一直画出边界）。

**`pdf/interp.py`** — `PDFPageInterpreterEx`，用了 pdfminer 大量内部 API，
所以 `pdfminer-six` 硬钉版本。上游照搬。

**`pdf/layout.py`** — `DocLayoutModel` / `OnnxModel`，YOLO 检测页面区域
（标题、正文、图、表、公式……），`set_backend()` 切 cpu/cuda/dml。

**`pdf/ultrafast.py`** — 跳过 ONNX 模型的快速路径，`extract_layout()` / `layout_mask()`。

**`pdf/ocr.py`** — `ocr_paragraphs()` / `translate_ocr_page()`，扫描件场景。

**`pdf/text_spacing.py`** — `add_cjk_latin_spacing()`，纯 stdlib，上游照搬。

### 入口

**`api.py`** — `translate()` / `translate_stream()` / `build_translator()` /
`load_layout_model()`，供应商+模型驱动，调用方不碰 provider 内部。
`__init__.py` 惰性转出前两个。
`translate(provider=, model=)` 是现在的写法；`profile=` 保留为**废弃别名**（映射到
`provider`），所以旧调用还能跑。`load_layout_model()` 是公开的，供**自带 translator
对象**的调用方（MCP 的两遍式引擎）在 fast 模式下拿到那个缓存的单例版面模型。
`ignore_cache=True` 让本次调用的每个段落都重译（跳过缓存读写）；它与
`cache.enabled`（写进配置的全局开关）**语义不同**，别混用。

**`cli.py`** — `build_parser()` / `parse_pages()` / `main()`，调试与批处理用。
寻址用 `--provider` / `--model`（`--profile` 是 `--provider` 的废弃别名）；
`--list-models` 打印「供应商 → 模型」树，并标出**不指定时会被用到的那一个**（即第一项），
`--list-profiles` 是它的隐藏别名。
除语言 / 页码 / 模式外，还暴露了 4 个**排版**选项，都会覆盖 config.yaml
里的同名字段：`--backend {auto,cpu,cuda,dml}`（ONNX 执行后端，覆盖 `layout.backend`）、
`--onnx PATH`（自定义版面模型，覆盖 `layout.model_path`）、
`--skip-subset-fonts` / `--no-skip-subset-fonts`（字体子集化，覆盖
`translation.skip_subset_fonts`）、`--ignore-cache`（本次强制重译）。

`--skip-subset-fonts` 用 `argparse.BooleanOptionalAction` + `default=None` 做成**三态**：
不传是 `None`（不动配置）、传是 `True`、传 `--no-` 是 `False`。这里不能用普通的
`store_true`——它的默认值是 `False`，不传时会把配置里写好的 `true` 静默改回 `false`。
`test_cli_options.py` 专门锁住这个语义。

**`sidecar.py`** — Electron 调用的 stdio JSON 行协议。stdout 只走 JSON，
日志全部走 stderr。方法表：

| 方法 | 作用 |
|---|---|
| `ping` | 存活探测 |
| `config.get` / `config.save` | 读写配置 |
| `providers.list` | 列出供应商及其模型（只回 `has_api_key`，绝不回密钥） |
| `assets.status` / `assets.download` | 资源状态 / 触发下载 |
| `pdf.info` | 只读元数据（页数），供预览工具栏显示「N / M」 |
| `translate.start` / `translate.cancel` | 启停翻译任务 |

请求 `{id, method, params}`，响应 `{id, ok, result|error}`，
事件 `{event: "progress"|"done"|"log", ...}`。
进程启动后先发一次 `{event: "ready", version}`，Electron 侧以此确认链路可用。

`providers.list` 的结果形状是**与渲染进程的冻结合同**，改它必须同时改
`src/renderer/app.ts` 里的 `ProvidersResponse`：
`{providers: {名: {kind, base_url, has_api_key,
models: {别名: {model, reasoning_effort, temperature}}}}}`。
它由 `profiles.list` 改名而来（`translate.start` 的 `profile` 参数同时换成
`provider` + `model`）；**旧名不再作为别名保留**——结果形状也变了，留个别名只会把
调用方的错藏起来。`translate.start` 现在也转发 `ignore_cache` 与
`skip_subset_fonts`（后者原样透传，`None` 仍表示「用配置」）。

`pdf.info` 是**只读**的元数据探测（页数），打开文档读 `page_count` 后即关闭，
不渲染也不修改；非 PDF、损坏文件、路径不存在都给出可读错误而不是库的 traceback。
预览工具栏拿它显示「N / M」，取不到时退化为只显示当前页码。

**`mcp_server.py`** — 供**外部 agent**（Claude Code / ZCode / 任何 MCP 客户端）
调用的 MCP 服务，同样走 stdio：不占端口（无防火墙弹窗）、无鉴权面、随父进程回收。
`build_server()` 注册四个工具，`main()` 收 `--config-dir` / `--debug`：

| 工具 | 作用 |
|---|---|
| `list_providers` | 列出供应商与模型（只回 `has_api_key`） |
| `extract_segments` | 第一遍：收集待译段落，返回 `job_id` + `[{id, text}]` |
| `typeset_translations` | 第二遍：用回传的译文排版落盘 |
| `translate_pdf` | 一次完成。`translator="provider"` 用应用里配的模型；`"client"` 用**连接方自己的模型**（MCP sampling） |

**配置是只读的**：故意没有任何增删供应商/模型或写 API key 的工具，所以把 agent
指向这个服务**不可能改动用户的配置**。

**怎么启动（打包版的关键）**：`pdfxlate-sidecar --mcp`。`--mcp` 在 `sidecar.main()`
里**最先判断**，有则把剩余参数原样转交 `mcp_server.main()`。之所以要这样，是因为
**打包产物没有独立的解释器**——PyInstaller onedir 的 exe 本身就是"解释器 + 写死的入口"，
没有 `python -m` 可用，所以只能让这个二进制自己当入口、用开关选角色。（早先 spec 注释
里写的 `python -m pdfxlate.mcp_server` 是**错的**，已修。）源码检出下用
`python -m pdfxlate.sidecar --mcp`，等价。

**应用里的「设置 › MCP」面板**（`mcp_config.py` + 三个新 IPC）负责把这件事交给用户：
片段由**主进程**按真实安装算出（`Sidecar.resolveLaunch` 是**唯一**判断 dev/打包的地方，
与应用启动用的同一条），所以给用户的路径一定是这台机器上真正可用的那个，而不是文档里的
样例。代写只覆盖五个 agent，因为**它们的格式并不一样**：

| agent | 文件 | 形状 |
|---|---|---|
| Claude Desktop / Claude Code / WorkBuddy | `claude_desktop_config.json` / `~/.claude.json` / `~/.workbuddy/mcp.json` | JSON，`mcpServers` |
| ZCode | `~/.zcode/cli/config.json` | JSON，**`mcp.servers`**，另需 `type`/`enabled`/`timeoutMs` |
| Codex | `~/.codex/config.toml` | **TOML**，`[mcp_servers.<名>]` |

**没有"通用一键"**（键名、格式、路径都不同，agent 还在增加），所以**片段是主、代写是辅**。
写入三条护栏：**先备份** → **只合并我们那一个键** → **写后重读校验，失败即从备份还原**；
**解析不了的文件直接报错、绝不"修复"**（那是别人的配置，我们没资格猜内容）。TOML 的读与
校验用 stdlib `tomllib`，写入是**手写一小段固定块**（stdlib 没有 TOML writer），只替换
`[mcp_servers.pdfxlate]` 这一段。实现全部可被 pytest 覆盖，因为它写的是**别人的文件**。

两条翻译路线的差别：`provider` 就是普通的一遍式管线（带翻译缓存）；
`client` 走 MCP sampling，需要客户端声明 `sampling` 能力——**先查
`ctx.client_capabilities.sampling` 再动手**，不支持就报一条可执行的错误而不是中途炸掉。

**`pdf/two_phase.py`** — `client` 路线（以及「先取段落、人工/外部翻完再回填」这种
用法）的引擎。**为什么需要它**：`pdf/converter.py` 的 `receive_layout()` 在**同一遍
扫描里**既切段落又立刻排版，翻译是同步的线程池调用，而 MCP sampling 是 `async` 的。
硬桥接（反向阻塞事件循环）会死锁，且要动项目里最微妙的那个文件。管线是**确定的**
（同一 PDF + 同一 `pages`/`mode` + 同一版面模型 → 同一 `sstk` 序列），于是改成两遍：

1. **收集**：`CollectingTranslator` 记录每段原文并**原样返回**（原样返回才能让基类的
   `{vN}` 占位符校验通过），产出的 PDF 丢弃。
2. **排版**：`DictTranslator` 查表回填；**查不到就报错**，绝不静默透传——静默透传会
   产出一份悄悄没翻的 PDF，这是唯一值得直接放弃的失败模式。

`converter.py` **一行都没改**。两遍之间用 `sequence_fingerprint()` 存下段落序列的
哈希，第二遍重算比对，不一致就 `DeterminismError` 中止，而不是排出一份错位的 PDF。

三个不变量值得记住：① 两遍都用 `TWO_PHASE_THREAD = 1`——线程池的**调用**顺序不确定，
并发记录会让段落编号和指纹不确定；两遍式本来就把真正的翻译挪出管线（一遍只记录、
一遍只查表），单线程零成本。② 所有错误继承 `TwoPhaseError`，它是 **`BaseException`
而非 `Exception`**：`converter.py` 的 worker 包在 `@retry(wait=wait_fixed(1))` 里，用的
是 tenacity 默认谓词（只匹配 `Exception` 且 `stop_never`），所以普通 `Exception`
会被**无限重试**，而 `BaseException` 立刻穿透。③ 相同源文只能对应一个译文——
`_build_text_map` 对重复段落给了不同译文时报错（管线按 `text → translation` 建表）。

### Electron 前端

**`src/main/main.ts`** — 窗口创建、应用生命周期、IPC 注册。
`--dev` 参数区分开发/打包模式；退出时调 `sidecar.stop()` 确保不留下孤儿进程。
`webPreferences.plugins = true` **是 PDF 预览的前提**：Chromium 的内置查看器是个
plugin，不开这一项时 iframe 会加载成空白页。窗口默认 1280×820、最小 1000×640——
侧栏固定 340px，窗口再窄预览区就放不下完整页宽。

**自建标题栏**：`titleBarStyle: "hidden"` 去掉系统标题栏与菜单条，界面自己画一条
空的拖拽栏。但**三个窗口按钮仍交给系统**——`titleBarOverlay` 让 OS 在右上角继续
绘制最小化/最大化/关闭。自绘这三个按钮要自己实现窗口控制 IPC、hover 态、
双击最大化、Aero Snap、多显示器 DPI 和无障碍标签，做残的概率远大于收益。
`TITLEBAR_HEIGHT = 50` **必须与 `style.css` 的 `--titlebar-height` 一致**：
overlay 的位置由 OS 按这个值摆放，不一致时系统三键会在栏内垂直偏心。

`Menu.setApplicationMenu(null)` 去掉默认菜单（File / Edit / View… 在本界面没有
对应物）。副作用是**菜单的快捷键一起没了**，所以 `registerDevShortcuts()` 用
`before-input-event` 在 `--dev` 下补回 `F12` / `Ctrl+Shift+I`（DevTools）与
`Ctrl+R`（重载）；打包版不注册。文本编辑类快捷键是 Chromium 行为，不受影响。

**`src/main/sidecar.ts`** — `Sidecar` 类。请求按 `id` 关联响应，
事件推给监听者，stderr 日志转发到终端。`Sidecar.resolve()` 决定用哪个可执行文件：
打包后用 `process.resourcesPath/sidecar/` 下的 PyInstaller 产物，
开发时用 `python/.venv/bin/python -m pdfxlate.sidecar`；两者都不存在时抛出
带两条候选路径的错误（而不是让 spawn 报裸 `ENOENT`）。
`start()` 会等到 `ready` 事件才 resolve。
主进程同时维护 `engineState` 并通过 `sidecar:state` 暴露给渲染进程，
避免渲染进程加载晚于 sidecar 启动时丢掉一次性的 `ready` 事件而卡在"启动中"。

**`src/preload/preload.ts`** — `contextBridge.exposeInMainWorld("pdfxlate", ...)`，
在 `contextIsolation` 开启的前提下向渲染进程暴露：
`request()`（转发到 sidecar）、`onEvent()` / `onLog()`（订阅事件）、
`defaultOutputDir()` / `saveAs()`（见下）、
`pickPdf(locale?)` / `pickOutputDir(locale?)`（原生对话框，语言由渲染进程传入）、
`reveal()` / `openPath()`（打开输出）、`fileUrl(path)`（绝对路径 → `file://` URL）。

`fileUrl()` 是**纯函数、不走 IPC**：渲染进程在 `contextIsolation` 下没有 Node，
拿不到 `pathToFileURL`，而 Windows 的盘符、反斜杠、空格与非 ASCII 文件名都必须
由它处理（已实测中文名 + 空格的文件能正常打开）。

**默认输出目录必须由主进程解析**（`app:defaultOutputDir` → `app.getPath("documents")/pdfxlate`）。
不要在 Python 里拼 `~/Documents`：Windows 的「文档」常被重定向到别的盘，只有 Electron
问得到真实位置。主进程还在转发 `translate.start` 时**兜底注入**这个路径，所以任何调用方
漏传都不会退化成引擎的工作目录。

**这修的是一个真 bug**：以前输出目录留空时，`api.py` 退回 `Path.cwd()`，而 sidecar 的工作
目录是**开发时的 `python/`**、**打包后的安装目录**。于是产物被写进源码树（仓库里那几份
`-mono`/`-dual` 就是这么来的），打包后往 `Program Files` 写还可能直接失败。`.gitignore`
里加了 `*-mono.pdf` / `*-dual.pdf` 作兜底。

**滚轮唤醒浮层的功能已移除。** 曾经用主进程转发滚轮事件来显隐浮层，但 PDF 查看器是
Chromium 内置扩展的**独立 `remote` WebContents**，OS 把滚轮路由给它，顶层窗口收不到；
即使在 `web-contents-created` 里认领该 guest 并监听 `input-event`，真实硬件滚轮路径
仍未能验证通过（本机 RDP 会话过滤了 `SendInput`）。按需求整体删除，不留死代码：
`registerWheelForwarding()` / `attachViewerWheelForwarding()` / `onWheel()` /
`pointerOverPreview` 全部去掉。**浮层仍会在翻页、缩放、切换适应模式时出现**——
那几条走的是 `flashViewerOverlay()`，与滚轮无关。

**`saveAs()` 存的就是当前标签那一份**。以前要在"原文/译文/对比"之间选，现在一个标签
只装一份文档，没有歧义可言。对话框在渲染进程之外，拷贝用 `copyFile` 在主进程完成
（渲染进程是沙箱，没有文件系统）。

**`src/renderer/app.ts`** — 界面逻辑，无前端框架，直接操作 DOM。
两栏工作区，且**有任务 / 设置两种模式**（`appMode`）：

- **任务模式**（默认）：左侧栏是**可折叠分组**（文档 / 选项），
  右侧是**预览区**。侧栏底部动作区是：`[开始翻译]`、其下一行 `[进度条][⚙]`、
  再下一行进度文本。
- **设置模式**（点 ⚙）：**左栏换成设置菜单**（顶部「← 返回」，下接模型 / 外观 /
  常规），**右侧换成该设置项的表单**（此时不显示预览）。底部动作区**整体隐藏**；
  标题栏的**标签栏**也一并隐藏，因为它属于被设置表单顶替掉的那块区域。

设置项四项：**模型**（右侧是「供应商列表 + 详情」两栏，见下）、**外观**（界面语言）、
**常规**（引擎状态、版本、配置目录，加**引擎日志**——都是只读诊断）、**MCP**（给别的
agent 用的接入配置）。标题栏清空后**引擎状态必须有地方显示**，就落在「常规」里。

**只有「模型」这一项是两栏。** 它自己的内容分成左列供应商列表（图标 + 名称 + 状态点，
固定 232px，独立滚动）和右列详情（供应商的接口地址 / 类型 / 密钥，加模型行列表，
占满剩余宽度、内容限宽 720px）。切换设置项时 `paintSettingsMode()` 给
`#settings-host` 加/去 `settings-host--split`，其它三项回到居中 760px 单栏。
这样做而不是新开第三栏：窗口最小 1000px，减去 340px 侧栏后再分三栏，详情只剩 400px
左右，字段放不下。

### 文档浏览与标签页（本轮重构）

**没有「原文 / 译文 / 对比」了。** 那套设计有个根子上的毛病：`lastResults` —— 唯一
决定译文/对比能否点开的东西 —— **只在一次翻译跑完时被赋值**。导入一份**已经翻好的**
PDF，它是空的，两个按钮就被禁用并提示"请先翻译"：程序**没有"这份文件本身是不是译文"
的概念**，只认识自己产出的东西。现在**任何 PDF 都能直接打开**，问题从根上消失。

- **文件夹选择器是一整行可点控件**（`#btn-pick-folder`，`.folder-picker`）：图标 +
  文件夹名 + 完整路径（小字、超长省略）+ 右侧「选择…」。整行点击都能打开对话框。
  未选文件夹时只显示一行「尚未选择文件夹」，路径行 `hidden`。**不要再加一个独立的小按钮**：
  原来那版是个普通尺寸按钮 + 下面一行裸路径，两者没有视觉归属，而且它引用的
  `.btn--block` 类**从未定义**过。
- **左侧栏列出所选文件夹里的全部 PDF**（`app.listPdfs`）。列表项 = **复选框 + 文件名**：
  点文件名开标签页，勾选表示"要翻这份"。**默认不勾任何一项**——文件夹里通常同时躺着
  原文与 `-mono`/`-dual` 译文，自动勾选会把译文再翻一遍。「开始翻译」只翻**勾中的**。
- **标签栏在标题栏里**（`#tabbar`，标记与拖拽条之间）。省下一条独立标签栏的整条高度。
  `flex: 0 1 auto`：没标签时宽 0、拖拽条独占整条；标签多了才收缩滚动。
  **拖拽条必须保留 `min-width: 40px`**，否则标签铺满后窗口拖不动。
- **标签宽度自适应，下限 96px、上限 210px**（`.tab { flex: 0 1 auto }`）。少时按内容宽度
  最多 210px；多了按比例压缩，到 96px 就不再压，改由横向滚动接管。96px 是取舍点：再窄
  文件名只剩三四个字符，标签就认不出来了。约 1000px 的标签区能放 10 个，再多交给箭头。
- **溢出时出现左右箭头**（`updateTabbarScroll()`）。`#tabbar` 是外层、`#tabbar-strip` 才是
  滚动容器——箭头若放在滚动容器内，会跟着标签一起滚走。只有
  `scrollWidth > clientWidth` 时才加 `.is-overflowing`，箭头用 `display: none` 而非
  `opacity: 0`：不可见的箭头仍占 22px，反而可能*造成*溢出，而隐藏只会释放宽度，两个状态
  因此不会来回抖。到两端时对应箭头 `disabled`（留 1px 余量，亚像素布局下两端很少精确相等）。
- **重建标签会丢掉滚动位置**，所以 `renderTabs()` 先存 `scrollLeft` 再恢复，并在下一帧
  `revealActiveTab()` 把激活项滚进可视区（仅在确实不可见时滚，否则会在指针下抽动）。
- **拖动标签排序**：`draggable` + 自定义 MIME `application/x-pdfxlate-tab`（**不写
  `text/plain`**，否则拖出窗口时浏览器地址栏会接受它）。`dragover` 按各标签中点直接
  `insertBefore` 实时预览；`dragend` 从 DOM 读回顺序写进 `openTabs`（DOM 已经是用户看到的
  排列，再自己推一遍等于两个真相）。文字 `user-select: none`；`dragend` 后 250ms 内忽略
  click，否则拖动结束会顺手激活那个标签。
- **拖动标签不能触发 PDF 拖放**：`dragenter` 对任何拖动都会发生，于是 `isFileDrag()`
  只认 `dataTransfer.types` 含 `"Files"` 的拖动。不加这层判断，把标签拖过预览区就会点亮
  拖放边框、并在落点时对空文件列表走一遍 `handleDropped`。
- **页号按标签各自记住**（`pageState: Map<path, {page, pages}>`）；**缩放是全局的**
  ——那是阅读偏好，每份文档不一样反而更糟。
- 翻译完成后**产出自动开成标签**（mono + dual），替代原来的「结果」分组。

### 设置 = 描述模型，选项 = 决定用哪个

这是两个**不同的问题**，界面分开放：

| | 设置 › 模型 | 选项 › 模型 |
|---|---|---|
| 回答 | **有哪些模型可用、怎么连** | **这次用哪一个** |
| 存哪 | `config.yaml`（永久） | localStorage（仅供本次/记住上次） |
| 内容 | 供应商：`kind`/`base_url`/`api_key`/**是否启用**；模型：模型 ID、上下文长度、最大输出、top_p、推理强度、温度、流式、extra_body、**是否启用** | 选一个 `供应商 / 模型 ID` |

**设置里的供应商列表和展开的模型行是「编辑光标」，不是「这次用哪个」。** 它们曾经是同一个
变量，导致在设置里浏览几下就会**静默改变下次翻译用的模型**——这正是要修的 bug。现在变量叫
`editingProvider`/`editingModel`/`expandedModel`，名字即含义，且**翻译任务不再读它们**
（只读选项面板的值）。

供应商列表底部的 `.hint` 说明两者的分工，否则两个都能选模型的地方必然让人困惑。

**模型行的开关是真的启用/禁用**，不是装饰：`ModelEntry.enabled`（默认 `true`，老配置无需
迁移）。关掉之后，无参 `resolve()` 跳过它、选项面板不列出它，**显式点名它则报错**
（`... is disabled; enable it in Settings first`）——用户关掉就是为了不被用，静默继续翻译
更糟。开关**点了立即保存**，不经过「保存」按钮：它是这一行唯一会改变翻译可用范围的东西，
藏在保存后面太容易漏。

### `context_length`：存下来用来诊断，不替用户决定请求

`ModelEntry.context_length` 记录模型的上下文窗口。它**做什么**：

- 在「最大输出」输入框上显示**推导出的建议值**（窗口的 1/4），仅作 placeholder。
- 某段落的提示词估算超出窗口时，**每个 adapter 打一条 warning**（不是每段一条，否则长文档
  会把日志刷屏）。

它**不做什么**：**不会在用户没填时自动塞 `max_tokens`**。输出被截断比一条警告更糟，而且多数
OpenAI 兼容服务自己就有默认值，替它们决定会改变现有行为。token 数是按"约 4 字符 1 token"
粗估的（这里没有分词器），估错也无害：误报只是一行日志，漏报则由接口自己的报错兜底。

### 选项面板：只影响本次任务

选项面板里的**模型 / 推理强度 / 源语言 / 目标语言**只作用于**下一次翻译**，**不写
config.yaml**——与同面板的页码、线程数、模式一致。存进 localStorage 是为了记住上次的选择，
而不是把它变成默认值。

每个下拉的**首项都是「跟随默认」**（值是空串）：真正的默认在设置里，如果这里直接显示某个
具体模型，会让人以为模型是在这里选定的。

- **模型下拉**列出 `供应商 · 模型` 全部组合，值形如 `provider|model`（两半都不含 `|`，所以
  能无歧义地还原）。
- **语言列表是常见的那些，不是全集**。引擎对未知码**有意宽容回退**到 GoNoto
  （`font_filename` 的注释写明了理由），所以列表里没有 ≠ 不支持。语言名用
  `Intl.DisplayNames` 现取，不手写二十种译名。
- **两种语言的作用不对称**：目标语言进提示词、决定字体与行高；**源语言只影响 OCR**
  （`_ocr_pages` 映射到 tesseract 码）。非扫描件改源语言不会有任何变化，这不是 bug。
- 两者都在缓存键（`_cache_params`）里，所以覆盖会正确失效缓存。

### 只影响本次任务：引擎侧怎么做到的

`translate.start` 现在透传 `lang_in` / `lang_out` / `reasoning_effort`。**用 `None` 表示
"不覆盖"**（与 `skip_subset_fonts` 同一个三态约定）：空串是一次"覆盖成空"的显式请求，
两者不能混为一谈。

覆盖落在**深拷贝**上（`cfg.model_copy(deep=True)`），`reasoning_effort` 则通过 `model_copy`
改 `ResolvedModel` 的副本。**调用方持有的配置对象绝不会被改**——设置面板正持有它，而
"只影响这一次"的全部意义就在于不把临时选择变成永久默认。`tests/test_per_job_overrides.py`
把这个不变量钉死了，包括"传入的 cfg 未被改动"和"不传时沿用配置"两条。

### 侧栏宽度可拖拽

`--sidebar-width` 是**唯一**被写入的值：宽度和折叠位移
（`margin-left: calc(-1 * var(--sidebar-width) - 1px)`）都由它派生，所以折叠逻辑一行没动。
范围夹在 **240 – min(600, 窗口宽度一半)**，窗口缩放时重新夹紧，启动时也重新夹紧（存下来的
值可能来自更宽的窗口，或被手改过）。

**拖动监听挂在 `window` 上，不是手柄上。** 手柄只有 5px，一拖就离开它；再往右就是预览区的
`<iframe>`——另一个文档，它的事件不会到达本页。曾经想用 `setPointerCapture`，但**实测在此
环境下不生效**（移动事件到了 `window`，手柄却收不到），所以改用一个普通标志位，既简单又
确实可用。

### 控件位置

**适应宽度/高度 + 双栏**：`#viewer-fit`，**文档左上角**，三个内联 SVG 图标（一页纸外框 +
双向箭头，`fit-height` 只把**箭头组**旋转 90°；双栏是两个并排的页面轮廓）。
**翻页/缩放/打开**：`#viewer-overlay` **右下角**。**另存为**：`#viewer-saveas`
**右上角**。三者共用同一套 `.is-visible` / `:hover` 规则与同一个
`flashViewerOverlay()`，外观一致，读起来是**一组控件**。都**内缩 18px**——查看器自己的
竖向滚动条贴在最右侧，压住它下端会很难抓。

**三个显示条件**：① CSS `:hover`（浮层保持 `opacity: 0` 但**留着布局盒与命中测试**，
所以不需要额外热区、也没有死角）；② 页面/缩放变化时 `flashViewerOverlay()` 加的
`is-visible` 类（约 1.6s 后移除，每次调用**重置计时器**）。两者并存，鼠标停在上面时
不会被计时器藏掉。无文档时由 `updatePreviewControls()` 整块
`hidden`。**已知取舍**：自动隐藏意味着平时看不出当前是哪种适应模式。

### 双栏（左右两页）

Chromium 内置查看器**没有**双页模式的 URL 参数（`spread` / `two-page` 都不认）。
**也不能拼两个 iframe**：查看器是独立进程，每个 frame 有自己的滚动条，而父页面
**读不到它的滚动位置**，两条滚动条永远无法同步——那就不是"一个文档的左右对照"，
而是把同一份 PDF 开了两遍。

所以做法是**把两页合成一个派生文档**，再用现有的单个查看器显示：一个文档、一条滚动条。

- 引擎侧 `python/pdfxlate/pdf/spread.py` 的 `build_spread()`：每两页并排画到一张新页
  （左 = 第 2k−1 页，右 = 第 2k 页）。用 `show_pdf_page()` 贴的是 **Form XObject**，
  **不栅格化**——文字仍可选中、可搜索。
- 输出在 `cache_dir()/spread/<sha1>.pdf`，键是「绝对路径 + 大小 + mtime」，所以改过的
  文件不会命中旧缓存。7 天未用的自动清理。写入先落 `.part` 再 rename。
- 边栏 `pdf.spread` 返回 `{path, pages, spreads}`。
- 渲染侧 `renderPreview()` **永远只建一个 iframe**；双栏时 `src` 指向派生文件，页码用
  `ceil(page / 2)`（源页 3 在第 2 张合成页上）。
- `spreadFiles: Map<源路径, 派生路径>` 是会话缓存；`toggleSpread()` 是**异步**的——
  首次需要生成，失败则保持原状并在日志里说明，绝不"切了但显示错的"。
- `state.page` 始终是**左页**，所以关掉双栏停在屏幕上真实存在的那一页。
- 翻页步长 2；页号显示 `3–4 / 20`；"下一页"边界按"左页 + 1"。
- 末页奇数时右侧留白，**但宽度不变**：若把它做窄，查看器会把这一页放大到两倍宽，
  翻到最后一页时会突然跳一下。

**第四个条件：标题栏的图钉按钮**（`#btn-toggle-overlays`）可以把三个浮层**钉住常显**，
给不想每次去"找"控件的人用。实现只有**一个类 + 一条 CSS**（`body.overlays-pinned` 把三块
设为 `opacity: 1`），**没有另起一套显隐逻辑**——`:hover` / `is-visible` / 计时器都照旧，
这条规则只是让它们变得不必要。`.hidden` 是 `display: none !important`，**优先级高于钉住**，
所以没有文档时钉住也不会凭空造出控件来。图钉在**窗口拖拽条之后、系统三键之前**，且必须
`-webkit-app-region: no-drag`（拖拽区会静默吞点击，本项目已踩过三次）。

**加新浮层时注意 `renderPreview()`**：它 `host.innerHTML = ""` 清空 `#preview-host`，
浮层现在都住在里面，所以必须先把它们取出来再 `append` 回去（见 `VIEWER_OVERLAYS`），
且新建的 frame 要按 `frameDroppable` 复原 `pointer-events`。漏掉会出问题——写这两轮时
各踩过一次。

### 拖放：两个真实的坑（都实测过）

**① `File.path` 在 Electron 32 被移除了。** 原来的拖放处理读 `file.path`，在
本项目的 33.4.11 上它恒为 `undefined`，于是 `filter(Boolean)` 把"每个路径都是 undefined"
变成"没拖进任何文件"——**拖放在此之前一直是坏的**，只是报了一句"无法读取拖入文件的路径"。
改用 `webUtils.getPathForFile()`（preload 暴露为 `pathForFile`，渲染进程是沙箱拿不到）。
**这个 bug 早该被发现**：以前的测试用 `Object.defineProperty(f, "path", …)` **自己造了
path**，正好把真实失败掩盖掉。现在用 CDP 的真实 drag 事件（带真实文件）测。

**② 打开文档后，iframe 会吃掉拖放。** 查看器是**另一个文档**，拖到它上面的文件由它接收，
父页面**一个事件都收不到**（实测：`dragenter`/`drop` 全无）。所以：
- `#preview-host` 留了 **6px 内边距**，这条边框属于父页面，拖进这里父页面能看见；
  随后 `dragenter` 里把 frame 设成 `pointer-events: none`，就把**后续整个拖放**接管过来。
- **仍然无法**从窗口右边缘/底边缘（即页面外侧）直接拖进来——那条路径无解，只能绕过边框。
- 拖入的 PDF **复制进当前文件夹**（`app.importPdf`）：**同一路径不复制**、**同名自动改名
  `C (2).pdf` 绝不覆盖**、**未选文件夹则不复制**只开标签。非 PDF 忽略并提示。

**侧栏底部**：进度条与设置按钮**同一行**（进度条在左、设置只占一个方形图标位）。
设置图标是**内联 SVG**（8 齿齿轮：一个粗描边圆得到齿盘与中心孔 + 8 个旋转小矩形当齿），
用 `currentColor` 着色——内联 SVG 是文档的一部分、**能继承 CSS `color`**，这点与
`<img>` 引用不同。

**一处已知取舍**：进度回到侧栏后，**侧栏折叠时看不到进度**。上一轮曾把进度放到窗口
底部通栏正是为了避免这个；现在按需求删掉通栏，折叠态下这个信息就消失了。

**引擎日志移进「常规」，没有删除**。`appendLog` 是 7 条失败路径的唯一出口
（`log.engineFailed` / `assetCheckFailed` / `providersFailed` / `engineFactsFailed` /
`pageCountFailed` / `cancelFailed` / `preview.dropUnreadable`），还承接 sidecar 的
stderr 流；删掉面板等于这些失败全部变静默。`#log-pane` 的 **id 没改**，所以
`appendLog` 一行都没动（隐藏元素照样能追加）。

**侧栏折叠**由 `<body>` 的 `sidebar-collapsed` 类驱动，而不是逐个元素开关：侧栏有
**两个**（任务 / 设置），类名一次覆盖两者，设置模式下折叠也成立。折叠用**负 margin
位移**而非 `display: none`，以便过渡；状态存 `localStorage`。

**一个已知取舍**：设置模式下折叠侧栏会连「← 返回」一起藏起来，退出设置只能靠标题栏
那个图标。图标常驻，所以可恢复；这是「图标折叠侧栏」的直接推论，没有为它做特例。

另有拖拽与文件选择、页码范围解析、结果列表。
用户可见文案一律走 `t()`，不写字面量。

`appMode` / `settingsSection` 是**状态变量 + 重绘**（`setAppMode()` /
`setSettingsSection()` / `paintSettingsMode()`），不是隐藏若干元素：模式切换要动的
是「哪一栏显示什么」，散落的 `hidden` 开关很快会互相矛盾。

**平台差异靠 `<body>` 类名**（`platform-win32` / `platform-darwin`，由
`api.platform` 写入）：Windows 的系统三键在**右上**，拖拽条要 `padding-right` 让开；
macOS 的交通灯在**左上**，改留 `padding-left`。同一份 CSS 靠类名分叉，而不是运行时
改内联样式。

**拖拽区是自建标题栏最容易踩的坑**：`-webkit-app-region: drag` 只加在标题栏的
**空拖拽条**上（`.titlebar__drag`），**任何可交互元素都不能落在里面**——拖拽区会
静默吞掉点击，按钮看起来在、就是点不动，且没有任何报错。所以折叠图标是拖拽条的
**兄弟节点**。实测方式是经 CDP 发真实 `mousePressed` / `mouseReleased` 并断言处理器
真的跑了，光看截图证明不了这件事（`getComputedStyle` 里 `.titlebar` 是 `none`、
拖拽条是 `drag`、各按钮都是 `none`）。

**应用标记（`icon.svg`）在构建时被内联进 `index.html`，不能用 `<img src>`**。页面跑在
`file://` 下，**origin 是不透明的，CSP 的 `'self'` 匹配不到相对路径**，`<img>` 会被
直接拒绝（控制台报 `Refused to connect ... "default-src 'none'"`）——和 PDF 预览必须
放行 `frame-src file:` 是同一个原因。`scripts/copy-static.mjs` 把 SVG 内联进去，内联
**不是子资源请求**，因此不需要放宽任何 CSP 指令（`frame-src file:` 仍是有意为之的
唯一放宽）。这也意味着 `icon.svg` 不再被单独拷到 `out/renderer/`，脚本里有 `retired`
列表把它从旧构建里删掉，否则陈旧副本会一直留着。

**应用图标与标题栏标记同源**：`scripts/make_icon.py` 从同一个 `icon.svg` 光栅化出
`assets/icon.png`（512）与 `assets/icon.ico`（16–256），由 `electron-builder.yml` 引用。
用 PyMuPDF 而不是 Electron 截图：**PyMuPDF 是既有依赖且原生渲染 SVG**，而 Electron 的
离屏渲染在隐藏窗口下**永不合成，`capturePage` 直接挂住**（已实测）。图标提交在
`assets/` 而非惯例的 `build/`，因为 `.gitignore` 把 `build/` 当打包产物忽略掉了，
而图标是需要入库的源材料。

**驱动内置查看器的方式（都是实测结论，别改）**：URL 片段
`#toolbar=0&navpanes=0` 关掉查看器自带的深色工具栏与缩略图栏（这是能用自建
控件的前提），`&page=N` 设定页码。**但改已加载 iframe 的 `src` 或片段不会导航**
——所以 `renderPreview()` 每次都**重建 iframe 元素**，这是切页/缩放唯一可行的办法。
（同样原因，早期探针只改片段、不重建元素，导致每个 zoom 值渲染结果都一样，白测了
一轮。）

**三种缩放，用两种不同的片段参数**（实测，别想当然地统一）：

| 模式 | 片段 | 说明 |
|---|---|---|
| 适应宽度（默认） | `&zoom=page-width` | 查看器**认**这个 |
| 适应高度 | `&view=FitV` | 查看器**不认** `zoom=page-height` / `zoom=page-fit` |
| 显式百分比 | `&zoom=125` | |

用真实窗口抓屏对比过两帧：适应宽度下整页宽度铺满、底部被裁；适应高度下**整页纵向
完整**（页脚可见）。所以 `FitV` 确实生效，而两个 `zoom=*` 的 fit 值在本版 Chromium
里被接受但忽略。默认取适应宽度：预览区比整页窄得多，固定 100% 会让每份文档一打开
就带横向滚动条。

**一个无法绕过的限制**：查看器跑在 `file://` 的 iframe 里，**父页面无法给它注入
脚本**，也读不到查看器
当前的页码——浮层显示的页码是界面自己的状态。别试图用脚本去同步。

供应商与模型编辑以 **`config.get` 返回的文档为结构的唯一真相**（名字、别名、
明细字段），`providers.list` 只提供 `has_api_key` 与下拉列表——所以界面需要首屏
同时取这两者。删除条目前会 `confirm()`，删除后从**已改动的文档**重新选一次，
因此选中项不可能指向已删除的条目。API key 的掩码语义保留：输入框永远从空开始，
只有用户真的敲了才覆盖已存的密钥。

**`src/renderer/i18n.ts`** — 界面双语（English / 简体中文）。两张扁平 key 表
`EN` / `ZH` 加一层解析运行时：`t(key, params?, fallback?)` 用于代码里拼的字符串，
`data-i18n*` 属性用于静态标记（由 `applyStaticTranslations()` 应用，支持
`data-i18n` / `-html` / `-title` / `-placeholder` / `-aria-label`）。
启动语言取 `localStorage` 里存过的选择，没有则按 `navigator.language` 判断
（`zh` 开头 → 中文，其余 → 英文）。模块**导入时无副作用**（不碰 `document` /
`localStorage`），因此 `scripts/check-i18n.mjs` 能在纯 Node 里加载它。
`t()` 的第三个参数 `fallback` 是给 sidecar 传来的 stage 标识用的：
`t(\`stage.${stage}\`, undefined, stage)` 让未知阶段渲染成原始标识而不是 `stage.foo`。

**`src/main/main.ts` 的 `DIALOG_STRINGS`** — 原生对话框的四条文案**在主进程另存一份**，
不 import 渲染进程的 `i18n.ts`。原因：`src/main` 与 `src/preload` 按 CommonJS 编译
（`tsconfig.node.json`），渲染进程是原生 ESM，且 `i18n.ts` 依赖 `document` /
`localStorage` / `navigator` 等浏览器全局——引进来会把浏览器专用依赖拖进主进程。
key 名与渲染进程的 `dialog.*` 保持一致，便于人工对照。

### 构建与打包

- `npm run build` — `tsc` 编译主进程与渲染进程，再由 `scripts/copy-static.mjs`
  把 `style.css` 拷进 `out/renderer/`、并把 `icon.svg` **内联**进 `index.html`。
  **拷贝 CSS 时会校验注释定界符配对**（`/*` 与 `*/` 数量相等、且没有游离的 `*` 开头行）：
  少一个 `/*` 会把后面的注释变成畸形选择器，**整条规则被浏览器静默丢弃**——不报错、
  不影响构建、不影响任何测试，只是界面坏掉。这个 bug 真的发生过（另存为浮层因此丢失
  定位与 `opacity: 0`，跑到了左上角且永不隐藏），所以才在这里加一道必然经过的检查。
- `npm run make:icon` — 从 `src/renderer/icon.svg` 重新生成 `assets/icon.png` 与
  `assets/icon.ico`。**改了图标就要跑一次**，否则打包图标会停在旧版本（标题栏靠
  构建期内联，会自动跟上）。
- `npm run dev` — 构建后以 `--dev` 启动 Electron，走本地 venv 里的 sidecar。
- `npm run typecheck` — 只做类型检查，两个 tsconfig 各跑一次。
- `npm run check:i18n` — 四项检查：key 对齐、`{placeholder}` 对齐、无空值，
  以及**渲染进程引用的 key 确实存在**。它用 `ts.transpileModule` 现编译 `i18n.ts` 再加载，
  因此不需要先构建。

  第四项是**因为前三项全都只把两张表互相对照**：删掉一个仍被引用的 key，它们照样全绿，
  而界面会把原始 key 当文案显示出来。**这个 bug 真的发生过**——一个 key 被当作"没人用了"
  删掉，而两个下拉仍在用它，只有手动 grep 才发现。第四项扫描 `src/renderer/` 里所有
  `t("...")` 与 `data-i18n*` 属性并逐个查表，缺失即非零退出。
- `python/pdfxlate.spec` — PyInstaller onedir 打包 Python 侧，
  产物作为 Electron 的 `extraResources` 打进安装包（见 `electron-builder.yml`）。

## 四、运行期数据位置

都不在仓库内，走 `paths.py` 解析：

| 内容 | 位置 |
|---|---|
| `config.yaml` | `config_dir()`（Electron 传入的 userData） |
| 翻译缓存 `cache.v1.db` | `cache_dir()` |
| 版面模型 `.onnx` | `assets_dir()/models/` |
| 字体 `.ttf` | `assets_dir()/fonts/` |
| OCR 数据 `.traineddata` | `assets_dir()/tessdata/` |

三者（桌面端 / CLI / MCP 服务）共享同一份 config、cache 与 assets——MCP 服务
通过 `--config-dir` 指到桌面端同一个 userData 目录，所以它看到的就是界面上配好的
供应商与模型。缓存是 sqlite 且开了 WAL + `busy_timeout`，多进程并发读写安全。
唯一的代价：MCP 服务是**独立进程**，因此它不共享桌面端已加载的版面模型，界面也
看不到它发起的任务进度。

## 五、测试与检查

```bash
python/.venv/bin/python -m pytest tests/ -q          # Python，当前 166 passed
python/.venv/bin/python -m pytest tests/ -q -k cache # 单个方向
npm run typecheck                                    # 前端类型检查
npm run check:i18n                                   # 中英文字符串表一致性
```

`test_translation.py` 用假 adapter（计数器、故意丢占位符）测缓存命中、
`{vN}` 守卫、`<think>` 剥离、注册表冲突、`cache.enabled` 接线。
`test_openai_compat.py` / `test_openai_responses.py` 用 mock 客户端测参数透传与校验，
`test_anthropic_messages.py` mock `requests.Session`，都不打真实 API。
`test_config_providers.py` 测供应商→模型的 `resolve()`、默认值校验、旧
`profiles:` 迁移，以及 `${VAR}` 在保存往返后不被写成明文（含 UI 重建配置那条路径）。
`test_typesetting.py` 测 `converter.fit_font_size()` 这个纯函数（放得下/放不下/
下限/空段落/零高度/公式固定宽），不构造 PDF，跑得很快。
`test_cli_options.py` 测 CLI 排版选项的解析与三态覆盖语义（不传时不动配置），
以及 `--provider` / `--model` / `--list-models` 的接线。
`test_sidecar_providers.py` 钉住 `providers.list` 的结果形状与「密钥不外传」。
`test_sidecar_pdf_info.py` 测 `pdf.info`：页数正确、结果形状、以及路径不存在 /
非 PDF / 损坏文件都给出可读错误（用 PyMuPDF 现场生成夹具，不依赖 `references/`）。
`test_mcp_server.py` 覆盖 MCP 工具的 stdio 握手、两遍式端到端、确定性护栏与
「缺失译文必须被拒」；其中的端到端用例不需要任何模型 API（收集/字典翻译器都不打
网络），因此排版正确性可以离线验证。缺 `mcp` extra 或样例 PDF 时会带原因 skip。
`test_assets_fonts.py` 测语言→字体映射与 `status()` 的向后兼容（旧键不能丢）。
前端目前只有 `typecheck` 与 `check:i18n` 两个静态检查，尚无自动化测试。
`check:i18n.mjs` 会真的加载 `i18n.ts` 并逐 key 比对，因此它能挡住"某门语言漏了一
条文案""译文把 `{error}` 吃掉了""某条译成空串"这类漂移；它不检查译文质量。

**界面改动怎么验证**：没有 DOM 级测试，所以布局与预览这类改动只能靠
`typecheck` + `check:i18n` + **真跑 Electron 核对**。可行的做法是用
`--remote-debugging-port` 启动，再经 CDP 读 DOM/量尺寸/截图——本项目已用这种方式
验证过双语切换、两栏布局、翻页/缩放是否真的重建了 iframe，以及完整翻译链路
（配一个本地 mock OpenAI 服务，让真实翻译产出 mono/dual）。
这些验证脚本是临时的，跑完即删，不进仓库。

## 六、提交前密钥扫描

仓库带一个原生 git hook，阻止密钥进入提交。**克隆后需启用一次**：

```bash
git config core.hooksPath .githooks
```

| 文件 | 作用 |
|---|---|
| `.githooks/pre-commit` | hook 本体：gitleaks 扫暂存区 + 模式兜底 + 大文件拦截 |
| `.gitleaks.toml` | 扫描规则与排除路径（构建产物、`references/`、`package-lock.json`） |

**为什么不用 pre-commit 框架**：实测它需要联网克隆 hook 仓库才能安装，本机访问
GitHub 很慢，直接卡住。装不上的 hook 等于没有——所以改为零依赖的 shell 脚本：
gitleaks 存在就用，不存在则退回正则匹配。

手动跑：

```bash
bash .githooks/pre-commit                 # 扫当前暂存区
gitleaks detect --source . --no-git       # 扫整个工作区
gitleaks detect                            # 扫 git 历史
```

绕过（仅用于确认过的误报，且应在提交信息里说明）：
`SKIP_SECRET_SCAN=1 git commit ...`。误报的正确处理是往 `.gitleaks.toml`
的 `allowlist` 加条目，而不是习惯性绕过。

> 注意：`.gitleaks.toml` 自身已加入排除路径，因为它含规则示例（例如 AWS
> 官方文档用的那个占位 access key id），不排除会自己扫出自己、制造假警报。
> 同理，文档里引用这类示例字符串时要改写描述，别写明文。


## 七、验证状态

阶段 1–7 均已实机验证，见 [../requirements/development-log.md](../requirements/development-log.md)。

- **字体渲染已确认**：中文以真实字形渲染，非空白方块（曾是最担心的风险点）。
- **端到端已跑通**：上游样例 `translate.cli.plain.text.pdf` 与真实 arXiv 文献
  （Attention Is All You Need，15 页）均产出 mono + dual。
- **打包已验证**：Linux AppImage（253 MB）实机运行，包内 sidecar 正常拉起，
  打包产物能完成真实翻译任务。
- **真实 API 调用已验证**：用本地 mock OpenAI 兼容服务确认 `reasoning_effort`
  与 `extra_body` 确实进入请求体、`Authorization` 头正确。
- **排版适配已量化验证**（2026-10-16）：双栏 BERT 论文 16 页 / 253 段，用译文长度
  可调的本地 mock 压测。译文 1.5 倍长时「真叠印」从 232 处降到 6 处、右边界溢出从
  1 处降到 0；译文≈原文长时输出与修复前**逐页内容流逐字节一致**（16/16 页），
  证明修复不影响本来排得下的段落。残余 6 处经逐条核对均为单个拉丁/数学残字
  （`P`、`e`、`¨`）压在中文行上，与原文自带的数学上标重叠同类，非段落相撞。
- **与上游的完整性核对**（2026-10-16）：逐个文件对比上游 `pdf2zh/`，
  `pdfinterp.py` / `text_spacing.py` / `ocr.py` 与上游**逐字节一致**（含页级 OCR
  判定、tessdata 下载、`render_char`），`doclayout.py` 类与方法齐全（仅换掉
  babeldoc 的资源查找），`high_level.py` 函数一一对应（含 URL 输入、旋转页、
  双栏装配）。当时发现并补齐的只有两类接线缺口：CLI 未暴露 4 个排版选项、
  字体语言映射未落地（见下）。**有意不移植**：PDF/A 输出（需新增 `pikepdf`
  依赖）、`--babeldoc`/`--mode precise`（需另一 submodule + 独立 venv）、
  `gui.py`/`backend.py`/`translator.py`（非 PDF 排版）。
  上游的 `mcp_server.py` 当时一并列为「不移植」，**后来改成了自己做**
  （`mcp_server.py` + `pdf/two_phase.py`）——理由与做法见 development-plan.md，
  关键差别是上游用 v1 的 `FastMCP` 且只做「用配置里的模型翻」这一条路，本项目
  还支持用**连接方自己的模型**（MCP sampling）。

### 移植过程中修掉的问题

1. **`fontTools` 是运行期隐式依赖**。PyMuPDF 的 `Document.subset_fonts()` 需要它，
   但源码里没有直接 import，静态分析查不到；缺了会直接崩。已加入 `pyproject.toml`。
2. **`--config-dir` 曾把 assets 也一起重定向**，导致 CLI 与桌面端各下一份
   75 MB 模型。现改为只覆盖 config，cache 与 assets 始终走平台默认位置，两者共享。
3. **损坏 PDF 无法保存**。真实 arXiv 文献里有悬空间接引用，MuPDF 能读但
   写回时报 `cannot parse object`。上游同样有此问题（`garbage=3`）。
   已在 `pipeline.py` 加 `_save_document()`：正常选项优先，失败才回退到
   `garbage=4, clean=True`，正常文件输出不变。
4. **打包后 sidecar 找不到**（`spawn ENOENT`）。`isDev` 参数被反向传递，
   打包模式去找 venv、开发模式去找打包产物。已修正，并在两者都缺失时报出
   两条候选路径而不是裸 ENOENT。
5. **`cache.enabled` 曾是死配置**（2026-10-16）。`CacheSettings.enabled` 定义了但
   全仓库无人读取，关掉缓存无效；且接线在构造之后，即使接上也会留下空 db 文件。
   现经 `build_adapter(cache_enabled=)` + contextvar 传到构造期，禁用时连库都不开。
6. **译文压到下一段 / 冲出栏边界**（2026-10-16）。见 `pdf/converter.py` 一节的
   `fit_font_size()`。旧代码只压行高、下限 1.0，压不下就照画；且换行被 `brk` 挡住。
   已改为缩字号兜底 + 含正文段落均可换行。
7. **`_NOTO_LANGS` 曾是死代码**（2026-10-16）。定义了 19 种非 CJK 语言却从未被引用，
   字体选择靠无条件回退——行为碰巧正确但语义是错的，还会误导后人。现抽出
   `font_filename()` 作为语言→字体的唯一来源，`_NOTO_LANGS` 真正参与判断。
8. **`status()` 硬编码 zh/ja/ko**（2026-10-16）。目标语言是 `ru`/`ar`/`th` 时，
   前端会误报字体缺失、并去下载用不上的中日韩字体。现加 `font_ready_for`（传 `lang` 时
   才有），同时**保留旧键**以免破坏前端与 `sidecar.py` 的无参调用。
9. **4 个排版选项没有 CLI 入口**（2026-10-16）。`layout.backend`、
   `layout.model_path`、`translation.skip_subset_fonts` 后端早已支持，但只能手改
   config.yaml。现暴露为 `--backend` / `--onnx` / `--skip-subset-fonts` /
   `--ignore-cache`。注意 `--skip-subset-fonts` 必须做成三态（`BooleanOptionalAction`
   + `default=None`），用 `store_true` 会把配置里的 `true` 静默改回 `false`。

### 已知遗留

- **紧行距**：压测下仍有少量相邻行间距偏小（原版 1935 处 → 修复后 165 处）。
  这是行高压缩的正常结果，不属叠印，可读性未受损。
- **标题栏 overlay 颜色是固定浅色值**：深色主题需要改成运行时
  `setTitleBarOverlay()`，目前只有浅色。
- **应用标记不支持深色主题**：内联的 SVG 自带固定配色，改色要么换文件、要么改内联
  逻辑。与上一条同源，留待深色主题那轮一起解决。
- **设置模式下折叠侧栏会连「← 返回」一起藏起来**，退出设置只能靠标题栏图标。图标
  常驻所以可恢复，属「图标折叠侧栏」的直接推论，未做特例。
- **打包产物未在本环境验证**：应用图标只验到「PNG/ICO 结构正确 + 配置指向正确」，
  `electron-builder` 的实际构建没有跑（与仓库现状一致，打包验证是早前在 Linux 上做的）。
- 前端尚无自动化测试，仅 `typecheck` 通过。自建标题栏的拖拽区属于「静默吞掉点击、
  不报错」的一类问题，只靠 `typecheck` 和截图都测不出来，需经 CDP 发真实鼠标事件
  断言处理器确实执行。

