# 项目复核：代码与文档的独立核对（2026-11-08）

一次通读式复核：12 个子系统各自独立深读代码，再对每一份结论做对抗式核对
（逐条 grep 验证符号存在性、文件行数、文档偏差是否成立）。目的不是重写
[../docs/project-structure.md](../docs/project-structure.md)，而是给出**独立于文档的
现状事实**与**文档过期清单**。

复核基线：`14a9949`（`main` 与本次工作树同一提交）。

## 0. 先说结论

- **代码质量很高，文档质量也高，但文档已经明显落后于代码。**
  `docs/project-structure.md` 自称「与项目实际结构一一对应」，实际上它更像
  「上次同步时的快照」：文件树行数普遍过期（有几处差一倍以上）、sidecar 方法表
  少 3 个方法、测试清单少 3 个文件、若干行为描述已被后续重构改掉。
  它作为「去哪改」的索引仍然非常有用，但**不要拿它的行数、清单或细节描述当真相**。
- **12 个子系统的主干描述全部经得起核对**：注册表机制、缓存键、批处理阈值、
  取消语义、状态机、打包耦合、拖放的坑——抽查全部属实。偏差集中在
  「计数类事实」（行数、条目数）和「重构后未回写的描述」，不是理解错误。
- **真正的行为级陷阱只有少数几个**，已单列在第 3 节，它们会静默出错而不是报错。

## 1. 规模与环境的实测事实

| 事实 | 数值 | 说明 |
|---|---|---|
| `python/pdfxlate/**/*.py` | 13,701 行 | 引擎主体 |
| `src/**`（ts/html/css） | 9,834 行 | 其中 `app.ts` 4,764、`style.css` 2,505 |
| `tests/**/*.py` | 9,752 行 / 31 个 `test_*.py` | |
| `scripts/**` | 619 行 | |
| 受版本控制的 py/ts/html/css/mjs 合计 | **33,848 行** | 文档第 12 行写的「约 6000 行」是 ≈1/5 的实际规模 |
| Todo 标记 | 全仓仅 1 处 `FIXME`（`pdf/converter.py:579`） | 干净 |
| 测试状态 | 文档记「578 passed」，本次**未复跑** | 本工作树无 venv，见下 |

本次工作树的环境限制（影响可验证范围）：

- **没有 `python/.venv`**：本机 PATH 上的 Python 是 3.13.9，而项目要求 3.11–3.12，
  因此本次**没有运行 pytest**；上面的「578 passed」是文档记录，不是本次实测。
- **没有 `node_modules`**：`npm run typecheck` / `npm run check:i18n` 本次未运行。
- **没有 `references/PDFMathTranslate/`**（被 `.gitignore` 排除）：任何「与上游逐字节一致」
  的断言在本次复核中**无法验证**，只能核对本仓库内的证据。
- **仓库没有 CI**（无 `.github/`）：`build.sh`、pre-commit、electron-builder 都不调用
  `typecheck` 与 `check:i18n`，这两个前端门禁**只能靠人工执行**。

## 2. 复核确认成立的核心机制

以下是逐条核对后确认的现状（可作为「不看文档也能信」的基线）：

- **翻译层注册表**：`models/base.py` 的 `@register` 按适配器类索引，注册键 = 缓存引擎名，
  要求 `< 32` 字符（`TranslationCache.__init__` 硬校验）。内置 **4 个 kind**：
  `openai_compat` / `openai_responses` / `anthropic_messages` / `agent_cli`。
  供应商的 `kind` 就是这个键，**模型是数据，不进注册表**。
- **缓存键** = `(engine, params_json, original)`；`params` 由 `ModelAdapter._cache_params()`
  折叠，含 `base_url` / `model` / `temperature` / `top_p` / `max_tokens` /
  `reasoning_effort` / `extra_body` / `prompt`。**新增任何影响输出的参数都必须加进这里**，
  否则换设置会命中旧译文。
- **`ignore_cache` 与 `cache.enabled` 语义不同**：前者只跳过**读**（`use_cache=False`），
  写仍照旧；只有 `no_cache`（ultrafast 路径置位）才读写全跳过。文档与 `api.py`
  docstring 说的「跳过缓存读写」只对 ultrafast 成立。
- **失败语义**：`PERMANENT_TRANSLATION_ERRORS` = 占位符丢失 / 输出被 max_tokens 截断 /
  空译文 / agent 未登录，四者不重试；普通异常由 `converter.translate_segment()`
  重试 `MAX_SEGMENT_ATTEMPTS = 3` 次。**`converter.py` 已无 tenacity**——
  文档第 684–687 行「包在 `@retry` 里、默认谓词会无限重试」是过期描述，
  同文件第 468 行反而是新的，前后自相矛盾。
- **sidecar 协议**：`METHODS` 实际 **17 项**；只有 `translate.start` 与 `agents.test`
  在后台 daemon 线程跑（`_BACKGROUND_METHODS`），这是取消可达性的前提。
  `main()` 进请求循环**之前**在主线程 `import numpy` 是硬要求（Windows 子线程首次导入
  会永久卡死），`tests/test_sidecar_cancel.py` 用源码顺序钉住。
- **取消建模为正常结束**：回复 `{job_id, cancelled: true, outputs: []}` 且**不发 `done`**；
  渲染进程据此显示「已取消」。取消粒度是段落之间。
- **`--config-dir` 只覆盖 config**，cache 与 assets 永远走平台目录——这是
  「CLI / 桌面 / MCP 共用一份 75 MB 模型」的原因。
- **输出落点**：`pipeline.OUTPUT_SUBDIR = "translated"`（逐文件解析，在源文件旁）；
  主进程侧另有常量 `TRANSLATED_SUBDIR`，**两者只是值相同、名字不同**。
- **`pages` 是双重语义**：既决定翻译哪几页，也决定输出保留哪几页；
  `_selected_pages()` 负责排序/去重/丢越界。产物重名走 `_unique_paths()`，
  mono/dual **共用同一序号**。
- **`fit_font_size()` 是纯函数**（`test_typesetting.py` 的测试对象）：先压行高（下限 1.0），
  压不下才二分缩字号（下限 `MIN_FIT_SCALE = 0.6`）。两个不变量：本来放得下的段落输出
  「逐字节一致」；纯公式段落 `wrap=False`，沿用绝对坐标不换行。
- **agent 后端**：一个 agent = 一个 `kind=agent_cli` 的 provider；批队列
  8 段 / 12000 字符（argv 类 8000） / 首段后 0.2s。WorkBuddy 桌面版是一等 backend
  （HTTP SSE + app 内登录态），ZCode **所有模型**走官方 app-server，
  **只有当前模型**失败才回退 CLI（非当前模型绝不回退，否则会静默给出错误模型的译文）。
- **MCP 两条路线**：`provider` 走普通一遍式管线（带缓存）；`client` 走两遍式
  （`two_phase.py`），并用 `sequence_fingerprint` 校验第二遍段落序列，不一致就中止，
  而不是排出错位 PDF。MCP 工具面**只读配置**，没有任何改配置或写 key 的工具。

## 3. 会静默出错的行为级陷阱（复核确认）

按「出错时不报错、只是结果不对」排序：

1. **`config.yaml` 的 `translation.prompt` 不能填文件路径。**
   `api.py:77` 把它原样当模板字符串交给 `string.Template`，从不读文件——填路径会把
   路径文字本身当 prompt 发给模型。只有 CLI 的 `--prompt`（`cli.py:203`）会 `read_text()`。
   文档第 31 行「指向模板文件」是错的。
2. **`assets.py` 的校验和字段没有任何资产填值。** `_verify()` 因此对所有真实资源恒为
   `True`；唯一有效的完整性检查是 `_download_one` 在响应带 `Content-Length` 时比长度。
   文档说的「带 sha256 校验」目前是空承诺。
3. **`verify_layout_metadata()` 无任何调用者**（`assets.py:374`）。`ensure()` 不调它，
   真正的元数据读取在 `pdf/layout.py:98`，缺字段时抛 `KeyError` 而非可读错误。
4. **`detect.check_auth()` 无生产调用者**（只有 `__all__`、自身 docstring 和测试）。
   「不再自动探测登录」是真的，但文档没点明它已无调用者。
5. **`#model-editor` 是单个 DOM 元素在列表里搬家**：重建列表前**必须先 `editor.remove()`**，
   否则未提交的输入被 `innerHTML=""` 一起销毁；也因为靠字段 id 取值，
   `expandedModel` 是单值，绝不能同时展开两行。
6. **`checkedFiles` 不受搜索过滤影响**：打字过滤只改画出来的行，绝不能顺手清理勾选集，
   否则「打字」会静默改变这次要翻什么。
7. **`cachedConfig` 是一份共享草稿**（模型面板 / Agents 面板 / 选项下拉共用），
   并且必须有 `configLoaded` 闸门：一次失败的 `config.get` 之后保存，会拿空文档
   覆盖用户的真实配置。
8. **`PROVIDER_KINDS`（`app.ts:2476`）必须手工与 Python 的 `@register` 对齐**：
   多写一个没有适配器的 kind，会得到一个能选中、到翻译时才失败的选项。
9. **`agentName()` 必须是字面量 `t("agents.name.claude")` 的 switch**，不能拼动态 key——
   `check:i18n` 只能看见源码里的字面量，打错会原样把 key 显示给用户。
   **而 `check-i18n.mjs` 的引用检查本身有洞**：正则只匹配单段后缀，
   `data-i18n-aria-label="status.localeLabel"`（`index.html:748`）被跳过。
10. **改返回形状要同时改三处 `DoneEvent` 类型**（`src/main/sidecar.ts`、`src/preload/preload.ts`、
    `src/renderer/app.ts`），只有渲染进程那份声明了 `cache`/`provider_cache`。
11. **`showOutputs()`/`paintDoneProgress()` 正常跑完会被执行两次**（`translate.start` 回复 +
    `done` 事件）。目前安全，前提是 `showOutputs` 保持幂等——改它时别引入一次性副作用。
12. **`renderPreview()` 会 `host.innerHTML=""`**：所有浮层必须先取出、清空后 append 回去，
    列在 `VIEWER_OVERLAYS` 里；新建 iframe 还要按 `frameDroppable` 复原 `pointer-events`。
13. **`assets.download` 不在 `_BACKGROUND_METHODS`**：下载 75 MB 模型期间请求循环被占住，
    `translate.cancel` 要等下载完成。首启场景通常无任务，掩盖了这一点。
14. **`two_phase._JOBS` 永不淘汰**，`mcp_server` 从不 `drop_job`：长跑的 MCP 服务会把每份
    文档的段落文本一直留在内存里。
15. **`TwoPhaseError` 继承 `BaseException` 而非 `Exception`**：调用方必须显式捕获，
    只写 `except Exception` 会漏掉。

## 4. 必须保持一致的地方（隐含耦合）

这些成对出现的常量/清单，改一处必须改另一处，且多数**没有测试兜底**：

| 项目 | 两处（或多处）位置 |
|---|---|
| 标题栏高度 | `src/main/main.ts` 的 `TITLEBAR_HEIGHT = 50` ↔ `style.css` 的 `--titlebar-height` |
| 输出子目录 | `pipeline.OUTPUT_SUBDIR` ↔ `main.ts` 的 `TRANSLATED_SUBDIR`（值同、名不同） |
| sidecar 可执行名 | `pdfxlate.spec` 的 `name="pdfxlate-sidecar"` ↔ `electron-builder.yml` 的 `extraResources.from` ↔ `sidecar.ts` 的 `resolveLaunch` |
| app id | `electron-builder.yml` 的 `appId` ↔ `main.ts` 的 `setAppUserModelId` |
| preload API 面 | `preload.ts` 的 `PdfxlateApi` ↔ 渲染进程各自声明的同名类型（不能互相 import） |
| MCP agent 清单 | `main.ts` 的 `mcpAgents()` ↔ `mcp_config.py` 的 target 名称 ↔ i18n 的 `mcp.agent.*` |
| 侧栏默认宽度 | `style.css` 的 `--sidebar-width` ↔ `restoreSidebarWidth()` 的 340 兜底 |
| provider kind | `app.ts` 的 `PROVIDER_KINDS` ↔ Python 各适配器的 `@register` name |
| 语言→字体 | 文档称 `assets.font_filename()` 是唯一来源，但 `pipeline._CJK_TARGETS` 仍存着同内容的重复表（当前无引用） |
| 打包内置字体路径 | `assets._bundle_root()` 读 `sys._MEIPASS/bundle_assets` ↔ spec 的 datas 键名 |

## 5. 文档与代码的偏差清单

按类型分组。**建议的修法是：行数不要写死，或加一句「以 `wc -l` 为准」；
清单类内容改成不写条目数的说法。**

### 5.1 过期行数（`docs/project-structure.md` 文件树 / 测试树）

| 位置 | 文档值 | 实测 |
|---|---|---|
| `:135` `pipeline.py` | 580 | **730** |
| `:140` `two_phase.py` | 489 | 496 |
| `:121` `workbuddy_desktop.py` | 1588 | **1610** |
| `:126` `models/__init__.py` | 23 | 29 |
| `:147` `src/main/sidecar.ts` | 234 | 239 |
| `:190` `copy-static.mjs` | 110 | 121 |
| `:172` `test_agents_detect.py` | 472 | **1079** |
| `:176` `test_sidecar_agents.py` | 417 | 425 |
| `:179` `test_sidecar_providers.py` | 181 | 206 |
| `:180` `test_sidecar_cancel.py` | 221 | 244 |
| `:183` `test_mcp_server.py` | 600 | 590 |
| `:162` `test_openai_responses.py` | 146 | 147 |
| `:178` `test_cli_options.py` | 286 | 280 |
| `:187` `test_per_job_overrides.py` | 136 | 134 |
| `:186` `test_pdf_spread.py` | 152 | 168 |
| `:12` 全项目规模 | 约 6000 行 | **33,848 行** |

### 5.2 清单缺条目

- **sidecar 方法表（`:563–577`）少 3 个**：`pdf.spread`、`mcp.snippet`、`mcp.installConfig`。
  （`docs/development-plan.md:159–164` 的列表反而是全的。）
- **tests 树（`:159–188`）少 3 个文件**：`test_config_enabled.py`（230 行）、
  `test_sidecar_pdf_info.py`（135 行，正文 `:1269` 引用了但树里没有）、
  `test_workbuddy_desktop.py`（1053 行，全文未提）。
- **preload 暴露面（`:719–726`）列出 9 个键，实际 19 个**：漏 `onReady`、`getState`、
  `pickFolder`、`importPdf`、`mcpLaunch`、`mcpAgents`、`copyText`、`openExternal`、
  `pathForFile`、`platform`。
- **文件树（`:83–196`）列了并不存在的 `.npmrc`**（被 `.gitignore` 忽略，镜像实际由
  `build.sh` 的环境变量设置），同时漏了 `README.md`、`README.en.md`、`package-lock.json`、
  `docs/images/`、`requirements/agents-feature-brief.md`。
- **PyInstaller「五处不能删」（`:1192`）实际更多**：还有 `pdfxlate.mcp_server`
  （打包版 `--mcp` 的唯一入口）与 `_collect_if_installed("mcp", "mcp_types")` 等。
- **`providers.list` 冻结合同（`:607–610`）写窄了**：实际还返回供应商级 `enabled`，
  以及每个模型的 `max_tokens` / `top_p` / `stream` / `context_length` / `enabled`；
  `app.ts` 的 `ProviderSummary` / `ProviderModelSummary` 依赖这些字段。

### 5.3 行为描述已过期

- **`:684–687`** 说 `converter.py` 的 worker 包在 tenacity `@retry` 里、会无限重试普通异常：
  **已无 tenacity**，现在是 `translate_segment()` 的 3 次有界重试。同文件 `:468` 是新描述，
  前后自相矛盾。
- **`:696`** 「侧栏固定 340px」：侧栏**可拖拽**，范围 240 – min(600, 窗口一半)，存 localStorage。
- **`:763–764` / `:1068–1069`** 描述的侧栏底部三行结构与齿轮图标形状与实际不符
  （实际是六齿、单条描边路径）。
- **`:1076`** 「`appendLog` 是 7 条失败路径的唯一出口」：实际 23–24 处调用点，20+ 个 key。
- **`:1089`** 「结果列表」**已不存在**：产物直接开成标签页。
- **`:951`** 「每个下拉首项都是『跟随默认』」：只有 `#opt-reasoning` / `#opt-lang-in` /
  `#opt-lang-out` 有空值项，`#opt-model` 明确没有。
- **`:954`** 模型下拉标签是 `供应商 / 模型`（斜杠），不是「供应商 · 模型」；且不是「全部组合」
  （禁用项与不匹配的 agent provider 被过滤）。
- **`:266`** 「下载带 sha256 校验」/ **`:269`** 「`verify_layout_metadata()` 校验元数据」：
  见第 3 节第 2、3 条，两者目前都不成立。
- **`:238` / `:550`** 无参回退与 `--list-models` 标记的是第一个 **enabled** 项
  （`all_models(only_enabled=True)[0]`），不是「第一项」。
- **`:216`** 称 `agent_cli` 是「第五种供应商」：内置是 **4 个 kind**（同文件 `:20` 写对了）。
- **`:207–208`** 平台目录只写了 config：cache 用 `%LOCALAPPDATA%` / `~/Library/Caches` /
  `XDG_CACHE_HOME`，文档未提。
- **`:1220–1222`** 「三端共享同一份 config、cache 与 assets——MCP 通过 `--config-dir` 指过去」：
  **只有 config 响应 `--config-dir`**；cache/assets 被共享与 `--config-dir` 无关。
  `sidecar.py:874` 与 `mcp_server.py:484` 的帮助文字也失真。
- **`:743` / `:62`** 说主进程常量与 Python 侧「同名」：Python 侧叫 `OUTPUT_SUBDIR`，不同名。
- **`:1133–1136`** 「不是隐藏若干元素」：`paintSettingsMode()` 实现**就是** `toggle("hidden")`。
  集中化是真的，这句话字面上不成立。
- **`:283–287`** `status(lang)` 追加 `font_ready_for`：唯一生产调用
  （`sidecar.py:541` 的 `method_assets_status`）**不传 `lang`**，渲染进程永远拿不到它。
- **`:272`** `bundle_assets/`「目前是默认中文字体」：`stage_assets.py` 构建时必装 zh，
  还会尝试装 ru（GoNoto），失败只警告。
- **`:1344`** 「`interp.py` / `text_spacing.py` / `ocr.py` 与上游逐字节一致」：
  `ocr.py:85` 与 `ultrafast.py:61` 带 `# ponytail:` 自研标记，`ocr.py` 含
  断行/软连字符重建与 `insert_htmlbox` overlay 方案。**`references/` 不在本工作树，无法逐字节复核**。
- **`:1163`** `DIALOG_STRINGS`「四条文案、key 与渲染进程 `dialog.*` 一致」：实际 5 个键，
  且 `pickFolder` / `saveAs` 在渲染进程表里**没有**对应 key，只是部分对齐。
- **`docs/development-plan.md:87–88`** 配置示例仍写 `default_provider` / `default_model`：
  两者已被 `_drop_stored_default()` 在加载时丢弃，README 与 `project-structure.md` 都明确说
  「没有默认模型」。
- **`docs/development-plan.md:176`** 事件列表含 `{event:"log"}`：sidecar **只发**
  `progress` / `done` / `ready`；日志走 stderr，主进程转成 `sidecar:log` IPC。
- **`docs/development-plan.md:179`** 取消用 `asyncio.Event`：实际是 `threading.Event`，
  再经 `_as_asyncio_event()` 包成只实现 `is_set()` 的 shim。
- **`docs/development-plan.md:143`** assets「直连下载到 userData，带校验和 + 原子写入」：
  目标是 `cache_dir()/assets`（与 userData 无关），临时后缀是 `.part` 不是 `.tmp`，
  校验和见上。
- **`docs/development-plan.md:330`** 「完成后打开输出目录，展示 mono / dual 两个结果」：
  现在是直接开成标签页，且单侧模式另一侧是空串会被跳过。
- **`docs/development-plan.md:334–344`** 依赖对比表（约 13 → 14 个）未含
  `cryptography`（2026-10-19 为 WorkBuddy 桌面认证加入）与 `fonttools`。
- **`docs/development-plan.md:348–372`** 阶段 6/7 仍把 Windows/macOS 打包验证写成未来事项：
  Windows NSIS 0.2.0、Linux AppImage 均已实机验证，只有 macOS 未验证。
- **`docs/development-plan.md:212`** 「四个决策值得记」，随后列了 **5** 条。
- **`docs/development-plan.md:14`** 「converter.py 26KB」：实测 34,195 字节；
  **`:40`** 的 `ModelAdapter.__init__` 签名漏了 `ignore_cache`。
- **`docs/development-plan.md:268` / `:293`** DSH 模型数一处写 25、一处 26；开发记录里
  又是 25 与 29。这些是本机安装快照，不该当稳定事实写进文档。

### 5.4 README 层面的偏差

- **`:80–88`** 从源码运行的步骤只装 `pip install -e .`，但功能列表把 `ultrafast` 当默认可选：
  按此步骤选 ultrafast 会因缺 `pdf-inspector` 失败（该包只在 `[ultrafast]` / `[dev]` extra）。
- **`:70–71`** 模型下载源「依次是 huggingface.co 与 hf-mirror.com」：漏了排在最前的
  显式 `HF_ENDPOINT`。
- **`:49`** 把该设置项叫「Agents」：中文界面文案是**「外部 Agent」**
  （`i18n.ts` 的 `settings.sectionAgents`），英文才是 Agents。
- **`:141`** `--pages` 「只翻译指定页」：它还决定**输出保留**哪几页。
- **`:64` vs `:347`** 安装包文件名一处 `pdfxlate-Setup-0.2.0.exe`、一处
  `pdfxlate Setup <版本>.exe`；开发记录里 0.2.0 的实际产物是连字符形式。
- **`:383–385`** 「拖到四周边框才会由应用处理」「拖入的 PDF 会放进当前文件夹」：
  实测右/底窗口边缘**无法**由应用处理，未选文件夹时只开标签、不复制文件。

### 5.5 文档之间互相矛盾 / 记录方式问题

- **`AGENTS.md:32–35` 的摘要漏了已存在的模块**：`models/` 还有 `openai_responses.py`、
  `anthropic_messages.py`、`catalog.py`；`pdf/` 还有 `spread.py`、`two_phase.py`、
  `text_spacing.py`。用于快速定位的摘要会让人以为这些不存在。
- **README/AGENTS 的「自研 / 独立实现」与开发记录的「逐字节照搬」口径不一致**：
  实际是混合——`interp.py`、`text_spacing.py` 照搬，`converter.py` 有实质重构，
  `two_phase.py` 纯自研，`ocr.py` 有自研改动。建议在文档里明确写成「混合」。
- **`requirements/development-log.md` 不是严格按时间序**：`:62–63` 说「详见本文件末尾
  『2026-10-16』」，而 `2026-10-16` 主节在 `:179`，`:1404` 是另一个同名小节。
  按位置读会把「上一轮/下一轮」理解错。
- **开发记录里反复出现的「上一轮 4 条遗留仍未修」是当时快照，不是当前待办**：
  `cancel` 与「语言输入框是死的」都已在 2026-10-17 修好；
  `_CJK_TARGETS` 重复表仍在但现在已是死代码；`font_ready_for` 仍在 `assets.py:370`
  生成、前端未使用。
- **`requirements/agents-feature-brief.md` 是冻结的历史契约，不是当前规格**：
  里面的「ZCode 只有 default」「自定义模型 ID 添加入口」「测试结果在卡片里显示」
  「WorkBuddy 只提示安装 CLI」都已被后续实现推翻。
- **`.env.example` 的 `TESSDATA_PREFIX` 无人读取**（只有 config 的 `ocr.tessdata_prefix`），
  且项目不加载 `.env`（无 dotenv 依赖）。该条目与实际行为不符。
- **`check-i18n.mjs:8` 写「Three checks」却列了 4 条**（文档写的是「四项检查」，文档对）。

## 6. 建议的后续动作

1. **修 `docs/project-structure.md` 的过期事实**（5.1–5.4）：优先修 sidecar 方法表、
   tests 清单、`converter.py` 的重试描述、`translation.prompt` 与校验和两处错误描述。
   行数建议改为不写死，或统一加「以 `wc -l` 为准」。
2. **清掉已确认的死代码**（或补一句注释说明保留意图）：`pipeline._CJK_TARGETS`、
   `assets.verify_layout_metadata()`、`detect.check_auth()` 的生产调用者缺席问题。
3. **给 `check-i18n.mjs` 的引用检查补多连字符属性**（`data-i18n-aria-label`）。
4. **把 `typecheck` 与 `check:i18n` 接进某个门禁**（仓库目前没有 CI，也没有
   pre-commit 调用），否则这两项永远只能靠人记得跑。
5. **补 `README.md` 的 ultrafast 安装说明**，并统一安装包文件名的写法。
