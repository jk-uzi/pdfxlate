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
| 接入新的模型后端 | `python/pdfxlate/models/` 新建文件 + `@register` 装饰器 |
| 调整发给模型的请求参数 | `models/openai_compat.py`（`reasoning_effort` / `extra_body` / `stream`） |
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
| 改「两遍式」段落收集/回填引擎 | `pdf/two_phase.py`（`CollectingTranslator` / `DictTranslator` / `collect()` / `typeset()`） |
| 改窗口 / 应用生命周期 / IPC | `src/main/main.ts` |
| 改子进程管理与 JSON 协议 | `src/main/sidecar.ts`（`Sidecar` 类） |
| 改渲染进程可用的 API | `src/preload/preload.ts`（`contextBridge`） |
| 改 Electron 界面 | `src/renderer/app.ts` + `index.html` + `style.css` |
| 改标题栏 / 窗口按钮 | `src/main/main.ts`（`titleBarOverlay` / `TITLEBAR_HEIGHT`）+ `style.css` 的 `--titlebar-height`，两处必须一致 |
| 改应用图标 / 标记 | `src/renderer/icon.svg` 是唯一来源；改完跑 `npm run make:icon` 重新生成 `assets/`，标题栏靠构建期内联自动跟上 |
| 改任务/设置模式与设置项 | `src/renderer/app.ts` 的 `appMode` / `setAppMode()` / `paintSettingsMode()`，菜单与面板在 `index.html` |
| 改侧栏折叠 | `src/renderer/app.ts` 的 `setSidebarCollapsed()` + `style.css` 的 `body.sidebar-collapsed` |
| 改进度显示 | `src/renderer/app.ts` 的 `paintProgress()`，落点在 `index.html` 侧栏底部的 `.sidebar__status`（与设置图标同一行） |
| 改默认输出目录 | `src/main/main.ts` 的 `defaultOutputDir()`（**必须走 `app.getPath("documents")`**，别在 Python 里拼路径） |
| 改「另存为」存哪一份 | `src/renderer/app.ts` 的 `saveAsSource()`；对话框与拷贝在 `src/main/main.ts` 的 `dialog:saveAs` |
| 改浮层显隐时机 | `src/renderer/app.ts` 的 `flashViewerOverlay()` + `VIEWER_OVERLAYS`；滚轮那条还需 `src/main/main.ts` 的 `registerWheelForwarding()` |
| 改右侧 PDF 预览（视图/翻页/缩放） | `src/renderer/app.ts` 的 `renderPreview()` / `viewerUrl()` |
| 改左侧栏分组 | `src/renderer/index.html` 的 `details.group` + `app.ts` 的 `expandGroup()` |
| 改供应商/模型的界面编辑 | `src/renderer/app.ts`（供应商与模型两级下拉 + 字段） |
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
│       ├── sidecar.py       335 行  stdio JSON 行协议（Electron 调用）
│       ├── mcp_server.py    509 行  MCP 服务（外部 agent 调用，stdio）
│       ├── models/
│       │   ├── __init__.py     23 行  导出翻译层公开符号
│       │   ├── base.py       243 行  ModelAdapter 基类 + 注册表
│       │   └── openai_compat.py 115 行  OpenAI 兼容适配器
│       └── pdf/
│           ├── __init__.py      1 行
│           ├── pipeline.py   580 行  管线编排：解析→版面→翻译→排版
│           ├── converter.py  643 行  排版回填 + 字号适配（移植上游 converter.py）
│           ├── interp.py     366 行  内容流解释器（上游照搬）
│           ├── layout.py     187 行  DocLayout-YOLO ONNX 版面检测
│           ├── ocr.py        161 行  OCR 段落重建与排版
│           ├── two_phase.py  489 行  两遍式段落收集/回填（MCP 用）
│           ├── ultrafast.py  101 行  免模型快速路径
│           └── text_spacing.py 44 行  中英混排间距（上游照搬）
├── src/
│   ├── main/
│   │   ├── main.ts          332 行  窗口 + 应用生命周期 + IPC（自建标题栏、默认输出目录、另存为、滚轮转发）
│   │   └── sidecar.ts       204 行  Python 子进程管理 + JSON 行协议
│   ├── preload/
│   │   └── preload.ts       132 行  contextBridge，向渲染进程暴露 API
│   └── renderer/
│       ├── index.html       368 行  界面结构（文案走 data-i18n* 属性；图标在构建时内联）
│       ├── app.ts          1511 行  界面逻辑（任务/设置双模式、折叠、视图与缩放、进度、另存为、结果）
│       ├── i18n.ts          499 行  中英文字符串表 + t()/applyStaticTranslations()
│       ├── icon.svg          49 行  应用标记的**唯一来源**（标题栏内联 + 打包图标）
│       └── style.css        863 行  样式（含标题栏控件、查看器浮层、折叠与侧栏状态行）
├── assets/
│   ├── icon.png               512×512，由 make_icon.py 从 icon.svg 生成后提交
│   └── icon.ico               16–256 多尺寸，同上（Windows 用）
├── tests/
│   ├── test_translation.py   187 行  缓存、占位符、推理标签剥离、注册表、缓存开关
│   ├── test_openai_compat.py 137 行  适配器参数透传、校验、提示词渲染
│   ├── test_config_providers.py 336 行  供应商→模型 schema、resolve、迁移、env 引用
│   ├── test_typesetting.py   172 行  字号适配纯函数（放不下时的缩字逻辑）
│   ├── test_cli_options.py   286 行  CLI 排版选项解析与三态覆盖语义
│   ├── test_sidecar_providers.py 181 行  providers.list 形状、密钥不外传
│   ├── test_mcp_server.py    600 行  MCP 工具、stdio 握手、两遍式端到端与确定性护栏
│   └── test_assets_fonts.py  113 行  语言→字体映射、status() 兼容性
└── scripts/
    ├── copy-static.mjs        81 行  拷贝 CSS、**把 icon.svg 内联进 index.html**
    ├── make-icon.mjs          33 行  用 venv 解释器调用 make_icon.py（npm run make:icon）
    ├── make_icon.py          186 行  从 icon.svg 生成 assets/icon.png 与 .ico（PyMuPDF）
    ├── check-i18n.mjs        100 行  校验中英文字符串表一致（npm run check:i18n）
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

**`config.py`** — `AppConfig`（`default_provider` / `default_model` / `providers` /
`translation` / `layout` / `cache` / `ocr`）、`Provider`、`ModelEntry`、
`ResolvedModel`、`TranslationSettings`、`LayoutSettings`、`CacheSettings`、
`OcrSettings`、`load()`、`save()`。全部 `extra: "forbid"`，配置写错启动即报错。
`${VAR}` 引用在加载时从环境展开，保存时写回字面形式，所以密钥不会被固化进文件。

**供应商 → 模型是两层，不是扁平的。** `Provider` 持有 `kind`（适配器注册名）、
`base_url`、`api_key` 和 `models: dict[str, ModelEntry]`——一个供应商配一次凭据
和地址，下面挂任意多个模型（`models` 的键是别名，UI 与 `--model` 用它寻址）。
`ModelEntry.model` 省略时由别名回填（`_fill_model_names`），因为「快速加一个模型」
是最常见的操作。

`AppConfig.resolve(provider, model) -> ResolvedModel` 是**唯一的解析入口**：
填默认值、报错点名可用项，并把两层**拍平**成一个 `ResolvedModel`。拍平这一步是
控制爆炸半径的关键——`ModelAdapter` 子类、`openai_compat.py`、`pdf/` 全都只做
`getattr(profile, "x")`，所以加这层嵌套时它们一行都没改。

**旧 `profiles:` 配置会自动迁移**（`_migrate_profiles`）：按 `(kind, base_url,
api_key)` 分组，旧 profile 名成为模型别名，`default_profile` 映射成
`default_provider`/`default_model`，并打一条 warning。`resolve()` 也接受旧 profile
名（`_find_provider_with_alias`），所以 `--profile deepseek` 这类老命令仍然可用。
迁移在**内存里**做，下次保存写回新结构。

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

**`models/openai_compat.py`** — `OpenAICompatAdapter`，`name = "openai_compat"`。
覆盖 DeepSeek / Qwen / Kimi / Grok / GLM / 硅基流动 / vLLM / Ollama / OpenRouter 等。
一等支持 `reasoning_effort`、`extra_body`（厂商私有参数透传）、`stream`，
429 限流用 tenacity 指数退避重试。

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
`--list-models` 打印「供应商 → 模型」树并标出默认项，`--list-profiles` 是它的隐藏别名。
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
`{default_provider, default_model, providers: {名: {kind, base_url, has_api_key,
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
`defaultOutputDir()` / `saveAs()`（见下）、`onWheel()`（滚轮通知，见下）、
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

**`onWheel()` 只通知"发生了滚轮"，不带坐标或增量**，因为主进程的 `input-event` 载荷
就只有这些。原因：PDF 查看器是**另一个进程**，它里面的滚轮事件既不到父页面的监听器，
查看器的 `postMessage` 接口在本版 Chromium 也没暴露。父页面**能**收到 iframe 元素的
`mouseenter`/`mouseleave`，所以渲染进程把两者配对——**指针在预览区上方时为真**才显示控件。
`postMessage` 那条路已实测无响应，别再试。

**`saveAs()` 存的是"当前预览的那一份"**：原文视图存源文件，译文/对比视图存译文。对比模式
下同时可见两份、说"当前份"本就有歧义，所以定死为译文（那是本工具的产出）。对话框在渲染
进程之外，拷贝用 `copyFile` 在主进程完成（渲染进程是沙箱，没有文件系统）。

**`src/renderer/app.ts`** — 界面逻辑，无前端框架，直接操作 DOM。
两栏工作区，且**有任务 / 设置两种模式**（`appMode`）：

- **任务模式**（默认）：左侧栏是**可折叠分组**（文档 / 选项 / 结果），
  右侧是**预览区**（画布，**没有工具栏**）。侧栏底部动作区是：`[开始翻译]`、
  其下一行 `[进度条][⚙]`、再下一行进度文本。
- **设置模式**（点 ⚙）：**左栏换成设置菜单**（顶部「← 返回」，下接模型 / 外观 /
  常规），**右侧换成该设置项的表单**（此时不显示预览）。底部动作区**整体隐藏**——
  不在翻译状态；标题栏的视图控件也一并隐藏，因为那时右侧是设置表单。

设置项三项：**模型**（供应商→模型两级编辑器，从任务侧栏**移进来**，避免两处编辑器
状态打架）、**外观**（界面语言）、**常规**（引擎状态、版本、配置目录，加**引擎日志**
——都是只读诊断）。标题栏清空后**引擎状态必须有地方显示**，就落在「常规」里。

**视图控件在标题栏右侧，翻页/缩放在文档右下角的浮层里。**

原来这些控件占着预览区上方一整条工具栏。把**视图切换与适应宽/高**移进标题栏后，
那条工具栏整条删掉，省下的高度全部归文档（实测 1280×820 下预览区 695 → **770px**）。
之所以是净赚而不是搬家：标题栏右侧那 140px 本来就是给系统三键预留、**纯属空转**的
空间。控件是拖拽条的**兄弟节点**，因此不在拖拽区内（拖拽区会静默吞点击）。

**两个浮层**：`#viewer-overlay`（翻页 / 缩放 / 「用默认应用打开」）在 `#preview-host`
**右下角**，`#viewer-saveas`（另存为）在**右上角**。两个都**内缩 18px**——查看器自己的
竖向滚动条贴在最右侧，压住它下端会很难抓。两者共用同一套 `.is-visible` / `:hover` 规则
与同一个 `flashViewerOverlay()`，外观也一致，这样读起来是**一组控件**而不是两个无关部件。

**三个显示条件**：① CSS `:hover`（浮层保持 `opacity: 0` 但**留着布局盒与命中测试**，
所以不需要额外热区、也没有死角）；② 页面/缩放变化时 `flashViewerOverlay()` 加的
`is-visible` 类（约 1.6s 后移除，每次调用**重置计时器**）；③ **滚轮**——见下。
三者并存，鼠标停在上面时不会被计时器藏掉。无文档时由 `updatePreviewControls()` 整块
`hidden`。

**滚轮为什么需要主进程配合**：查看器在另一个进程里，其滚轮事件**不到父页面的监听器**，
`postMessage` 接口在本版 Chromium 也没暴露（都实测过）。但主进程的 `input-event` 能
看到 `mouseWheel`，只是**没有坐标也没有增量**；而父页面**能**收到 iframe 的
`mouseenter`/`mouseleave`。于是主进程只转发"发生了一次滚轮"，渲染进程再配对
`pointerOverPreview` 标志，**两者同时成立**才显示——否则在侧栏上滚滚轮也会弹出文档控件。

**加新浮层时注意 `renderPreview()`**：它 `host.innerHTML = ""` 清空 `#preview-host`，
浮层现在都住在里面，所以必须先把它们取出来再 `append` 回去（见 `VIEWER_OVERLAYS`）。
漏掉这一步浮层会在第一次渲染时被永久删除——写这一轮时就踩过一次。

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

预览有三个视图：原文 / 译文（mono）/ 对比（左右并排；只有对比模式给窗格加标签，
单栏时视图按钮的激活态已经说明了是哪一份，重复是噪音）。译文与对比在有结果前
禁用，并把原因写在 `title` 上。无文件时整个右侧画布本身就是投放区。

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
脚本**，因此对比模式的两个窗格**滚动无法同步**（各自独立滚动），也读不到查看器
当前的页码——浮层显示的页码是界面自己的状态。别试图用脚本去同步。

供应商与模型编辑以 **`config.get` 返回的文档为结构的唯一真相**（名字、别名、默认项、
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
- `npm run make:icon` — 从 `src/renderer/icon.svg` 重新生成 `assets/icon.png` 与
  `assets/icon.ico`。**改了图标就要跑一次**，否则打包图标会停在旧版本（标题栏靠
  构建期内联，会自动跟上）。
- `npm run dev` — 构建后以 `--dev` 启动 Electron，走本地 venv 里的 sidecar。
- `npm run typecheck` — 只做类型检查，两个 tsconfig 各跑一次。
- `npm run check:i18n` — 校验中英文字符串表一致：key 对齐、`{placeholder}` 对齐、
  无空值。它用 `ts.transpileModule` 现编译 `i18n.ts` 再加载，因此不需要先构建。
  改动 `i18n.ts` 后跑一下，表漂移会以非零退出报出来。
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
`test_openai_compat.py` 用 mock 客户端测参数透传与校验，不打真实 API。
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
（配一个本地 mock OpenAI 服务，让真实翻译产出 mono/dual 后检查译文与对比视图）。
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

