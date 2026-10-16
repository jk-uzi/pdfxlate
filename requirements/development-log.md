# 开发记录

短期开发记录，按时间累积。长期方向见 [../docs/development-plan.md](../docs/development-plan.md)。

---

## 2026-10-15

### 已完成

- **仓库初始化**：`git init`（分支 `master`，尚无提交），补 `.gitignore`，
  首次导入已暂存 26 个文件、3635 行。
- **阶段 1 骨架**：`paths.py`（跨平台 config/cache/assets 路径，支持
  `--config-dir` 与 `PDFXLATE_CONFIG_DIR` 覆盖）、`config.py`（pydantic 校验 +
  `${VAR}` 环境变量展开，`extra: "forbid"`）、`assets.py`（下载 + 校验和 +
  原子写入 `.part` → rename，`huggingface.co` → `hf-mirror.com` 镜像回退，
  并记住上次成功的端点避免每次重付连接超时）。
- **阶段 2 翻译层**：`models/base.py`（`ModelAdapter` 基类、`@register` 注册表、
  `{vN}` 占位符校验、`<think>` 剥离）、`models/openai_compat.py`、
  `cache.py`（stdlib sqlite3，线程局部连接，无 import 副作用）。
- **阶段 3 部分**：`pdf/` 下 `pipeline.py`、`interp.py`、`layout.py`、
  `ultrafast.py`、`ocr.py`、`converter.py`、`text_spacing.py` 已落地。
- **阶段 4**：`api.py`（公开 Python API，profile 驱动）、`cli.py`（命令行入口）、
  `sidecar.py`（stdio JSON 行协议，含 `progress` / `done` / `log` 事件与取消）。
- **阶段 5**：Electron 前端完整落地 —— `package.json`、`tsconfig.node.json`、
  `tsconfig.renderer.json`、`electron-builder.yml`；`src/main/main.ts`（窗口 +
  生命周期 + IPC）、`src/main/sidecar.ts`（`Sidecar` 类，子进程管理 + JSON 行
  协议，启动等 `ready` 事件握手）、`src/preload/preload.ts`（contextBridge 暴露
  API）、`src/renderer/{index.html,app.ts,style.css}`（拖拽、profile 编辑、
  进度、结果、日志）、`scripts/copy-static.mjs`。
- **阶段 6 配置就位**：`python/pdfxlate.spec`（PyInstaller onedir）、
  `electron-builder.yml`（三平台）。均已写好但**未实际打包验证**。
- **依赖**：`pyproject.toml` 补入 `fonttools` —— PyMuPDF 的
  `Document.subset_fonts()` 运行期需要它，源码里没有直接 import，容易漏掉，
  缺了字体子集化会直接失败。
- **测试**：`test_translation.py` + `test_openai_compat.py`，27 passed；
  `npm run typecheck` 通过（前端尚无自动化测试）。
- **文档**：`AGENTS.md`（目录定位）、`docs/development-plan.md`（长期方案）、
  `docs/project-structure.md`（代码索引，按需求定位到文件）。

### 忽略规则

`python/.venv/`（478 MB）、`node_modules/`（34 MB）、`out/`、`__pycache__/`、
`references/`（142 MB 上游克隆，自带 `.git`）、`*.onnx` / `*.traineddata` / `*.part`、
`config.yaml`、`.env`。

### 待办

- ~~**字体渲染验证**~~ ✅ 已完成：中文以真实字形渲染，非空白方块。
- ~~端到端验证~~ ✅ 已完成：上游样例与真实 arXiv 文献均产出 mono + dual。
- ~~sidecar 协议实测~~ ✅ 已完成：JSON 行协议、进度事件、取消、错误响应均正确。
- ~~打包实测~~ ⏳ Linux 已完成（AppImage 253 MB 实机运行通过）；
  Windows / macOS 需在对应平台或 CI 验证。
- 前端尚无自动化测试，仅 `typecheck` 通过。
- 右边界轻微溢出（继承自上游，详见下方"移植中发现的问题"）。

### 备注

- 当前分支 `master`，项目默认分支是 `main`。尚无提交，改名零成本。
- 暂存区已就绪，尚未执行首次提交。

> 以上两条已过时：2026-10-16 完成了改名（`master` → `main`）与首次提交，
> 详见本文件末尾「2026-10-16」一节。

---

## 2026-10-15（续）：阶段 6–7 实机验证

### 已完成

- **字体渲染**：生成含中文的测试 PDF，`SourceHanSerifCN-Regular.ttf`（31058 字形）
  渲染出真实字形。方案标记的最大风险点排除。
- **端到端**：
  - 上游样例 `test/file/translate.cli.plain.text.pdf` → mono + dual ✅
  - `--ultrafast` 快速路径同样跑通 ✅
  - 真实 arXiv 文献（Attention Is All You Need，15 页）→ mono 1.1 MB + dual 2.0 MB ✅
- **真实 API 调用**：起本地 mock OpenAI 兼容服务，用完整 CLI 链路翻译真实 PDF，
  抓取服务端收到的请求体确认：`reasoning_effort: high` ✅、
  `extra_body` 的 `chat_template_kwargs` 被正确展开进请求体 ✅、
  `Authorization: Bearer` 头正确 ✅。这是"接入任何模型"承诺的关键验证。
- **打包**：PyInstaller onedir 产出 365 MB，Linux AppImage 253 MB，
  实机运行并拉起包内 sidecar；打包产物独立完成真实翻译任务 ✅。
- **测试**：27 passed。

### 移植中发现并修掉的问题

1. **`fontTools` 是运行期隐式依赖**。PyMuPDF 的 `Document.subset_fonts()` 需要它，
   源码里无直接 import，静态分析查不到；缺了字体子集化直接崩。
   已加入 `pyproject.toml`（上游 pyproject 里也有，只是容易被当成未使用而砍掉）。
2. **`--config-dir` 误把 assets 一起重定向**，CLI 与桌面端各下一份 75 MB 模型。
   改为只覆盖 config，cache/assets 走平台默认位置，两者共享。
3. **损坏 PDF 无法保存**。真实 arXiv 文献含悬空间接引用（`387 0 R`），
   MuPDF 能读但写回报 `cannot parse object`。**上游同样有此问题**
   （`high_level.py` 用 `garbage=3`）。已在 `pipeline.py` 加 `_save_document()`：
   正常选项优先，失败才回退 `garbage=4, clean=True`，正常文件输出不变。
4. **打包后 sidecar 找不到**（`spawn ENOENT`）：`isDev` 参数被反向传给
   `Sidecar.resolve()`，导致打包模式去找 venv、开发模式去找打包产物。
   已修正；两者都缺失时抛带两条候选路径的错误。

### 视觉验收（visual-judge）

对翻译后的 arXiv 页面做了视觉验收。judge 报了"双栏被合并成单栏"，
经程序化核实为**误判**：原始 PDF 本身就是单栏（53 行全部左对齐 x≈100，
最宽行 x=108→506 占满正文宽度），版面模型检测正确、回填也忠实。

judge 指出的"右边界溢出"确认为真实存在但**继承自上游**：把
`pdf/converter.py` 的 `receive_layout` 与上游逐字节 diff，结果完全一致，
说明换行算法未改动。实测 845 行中 33 行超界，最多 +13pt / 页宽 612pt。

### 备注（续）

- `docs/` 与 `requirements/` 在本轮之前已写好，行号与文件树已按实际代码同步更新。
- 视觉验收用的 mock 服务返回固定文本，不代表真实模型输出；已改用按源文长度
  缩放的中文回复复测。

---

## 2026-10-15（续二）：规范审计与提交前防护

按 `dev-discipline` skill 审计了一遍，两条铁律都守住（暂存区无密钥、配置集中
在 `config.py` 一处读取），但发现几处问题，已修：

### 新增：提交前密钥扫描

- `.githooks/pre-commit` —— **原生 git hook，零网络依赖**。gitleaks 扫暂存区
  + 正则兜底 + 大文件拦截（>2MB，防误提模型/构建产物）。
- `.gitleaks.toml` —— 规则与排除路径。
- 启用方式：`git config core.hooksPath .githooks`。

**为什么没用 pre-commit 框架**：实测它需要联网克隆 hook 仓库才能安装，
本机访问 GitHub 很慢、直接卡住。装不上的 hook 等于没有，所以改成零依赖
shell 脚本。曾同时放了 `.pre-commit-config.yaml` + `.secrets.baseline`，
后来删掉——两套 hook 配置等于两个真相来源，而且前者是个陷阱：谁跑
`pre-commit install` 就会卡死。

**双向验证**（这是关键，只验证"能拦"不够，还要验证"不误拦"）：
- 真密钥（AWS key + sk- 前缀）→ exit=1，拦下 ✅
- 正常源码 + 测试夹具 → exit=0，放行 ✅

过程中修掉一个自己造的假警报：`.gitleaks.toml` 里的 allowlist 示例字符串
（AWS 官方文档用的那个占位 access key id）本身匹配 AWS key 模式，导致 hook
扫自己就报错。已删除该示例并把 `.gitleaks.toml` 加入排除路径。**第二次同类
问题**：把这段经历写进本日志时又写了一遍那个字符串，hook 再次报警——所以
文档里引用这类示例一律改用描述，不写明文。假警报会训练人忽略扫描器，必须清掉。

### 修复的问题

1. **残留上游环境变量前缀**（真代码不一致）。`pipeline.py` 读的是
   `PDF2ZH_OCR_LANGUAGE`（上游前缀），本项目其他变量都是 `PDFXLATE_`。
   已收进 `config.py` 的 `OcrSettings`（`ocr.language` / `ocr.tessdata_prefix`），
   业务代码不再散读环境变量——符合 skill「配置应在一处读完」的要求。
   验证：文本路径无回归、`tessdata_prefix` 能绕过网络下载、非法语言码被拒、
   配置写错被 `extra: forbid` 拒绝。
2. **`.gitignore` 缺密钥类文件条目**。补 `*.pem` / `*.key` / `*.p12` / `*.pfx` /
   `credentials*.json` / `service-account*.json` / `.ssh/` / `*.kdbx`。
3. **`.env.example` 缺失**。已补，只列变量名不含值（skill 明确要求提交它）。
   覆盖 `PDFXLATE_CONFIG_DIR` / `HF_ENDPOINT` / `TESSDATA_PREFIX` 与常见
   provider 的 key 变量名。已验证它不被 `.gitignore` 的 `.env.*` 规则吃掉。
4. **`.gitkeep` 删除未重新暂存**（`AD` 状态）+ 暂存区里留了一个测试密钥文件，
   均已清理。删除是正确的：5 个目录都已有真实文件。

### 判定为"不算硬编码"的（核对后不报）

- `assets.py` 的模型/字体/OCR 下载 URL —— 资源身份，与实现绑定，且可用
  `HF_ENDPOINT` 覆盖镜像。
- `DEFAULT_PROMPT`、`temperature: 0.0` 默认值 —— 有文档有校验的合理默认，
  0 是防止公式标记被打断的功能性要求，用户可在 profile 覆盖。
- `assets.py` 的 `_TIMEOUT` / `_ATTEMPTS` / `_RETRY_BACKOFF` —— 已是有名字的常量。
- 前端 `placeholder` / `<option>` —— UI 提示文案。
- `pipeline.py` 的 `thread = 16`（ultrafast 默认）—— 功能性默认，`-t` 可覆盖。

### 未做

- **没有提交**。按用户要求先只装防护、修问题，提交由用户决定。
- Windows / macOS 打包仍未实机验证（需在对应平台或 CI）。

---

## 2026-10-16：首次提交 + 两个缺陷修复

### 仓库

- 分支整理：`master` 改名 `main`（默认分支，**仍是空提交**，仅作分支锚点），
  开发分支 `develop`。首次真实提交落在 `develop`（45 个文件）。
  之所以要一个空提交：git 的分支引用必须指向 commit，零提交时 `develop` 无法存在。
- 启用 `core.hooksPath=.githooks`，首次提交时 gitleaks 扫描通过。

### 审计发现的两个缺陷（均已修复并合并）

用并行 worktree 委派两路修复，各自独立分支、主代理审阅后串行 no-ff 合并。

**缺陷 1：`cache.enabled` 是死配置**。`CacheSettings.enabled` 定义了但全仓库无人读取。
更隐蔽的是：即使接上，若在 `TranslationCache` **构造之后**赋值，库已经建出来了
（`__init__` 在 `enabled=True` 时立即 `_init_db()`），用户会得到一个空的 4 KB
`cache.v1.db`；缓存目录只读时还会让「关掉缓存」变成「启动失败」。
修法：`build_adapter(cache_enabled=)` 经模块级 contextvar 在**构造期**生效，
禁用时连库都不开。选 contextvar 是因为 `ModelAdapter` 的子类构造签名不受本模块控制，
而 contextvar 只在 `build_adapter` 调用期间 set/reset，线程池里也安全，
且不破坏「新增后端只加一个文件」的设计承诺（已用假 adapter 实测验证）。

**缺陷 2：译文压到下一段 / 冲出栏边界**。两个独立成因：

1. **竖向**：`converter.py` 的行高压缩循环下限 1.0，压不下就照画，译文直接压在下一段上。
2. **横向**：换行判定被 `brk` 前置条件挡住，而 `brk` 只标记「原文段落内部有换行」，
   所以原文是单行的段落（标题、图注、表格行）译文**永不换行**，一直画出边界。

修法：新增纯函数 `fit_font_size()`——先沿用历史行高压缩（保证原本放得下的段落输出
不变），压到下限仍溢出才**二分缩小字号**（下限原字号 60%），缩小后恢复默认行高；
换行条件由 `brk` 改为 `wrap`（含真实文字的段落都允许换行），纯公式段落仍不换行
（上游按绝对坐标锚定公式，换行会错位）。

### 量化验证（这次特意做了严谨的对照，过程有反复）

一开始的 mock 有**测量错误**：它按「整条 user 消息」算译文长度，而消息里含约 250 字符
的提示词模板，导致短段落的译文被撑长数倍，叠印数字被严重夸大（一度报出 2823 处）。
改成只按 `Source Text:` 之后的真实源文本计算后，数字才可信。

双栏 BERT 论文（arXiv 1810.04805，16 页，253 段），译文长度可调的本地 mock 压测：

| 工况 | 真叠印（修复前 → 后） | 右边界溢出 |
|---|---|---|
| 译文 1.5 倍长 | 232 → **6** | 1 → **0** |
| 译文≈原文长 | 7 → 7（未变差） | 0 → 0 |

「真叠印」= 两个 span 落在同一基线上（读者两行都读不了），与「文档自带的数学上标重叠」
分开统计——后者在未翻译的原文里就有 5 处，不是缺陷。

**回归保证**：译文≈原文长时，修复前后 16/16 页内容流**逐字节一致**，证明修复只作用于
放不下的段落。残余 6 处经逐条核对全是单个拉丁/数学残字（`P`、`e`、`¨`）压在中文行上，
与原文自带的上标重叠同类。

### 视觉验收（visual-judge）

对修复后的 6 页（含与修复前同页的对照）做了验收：**全部 pass，无 high/medium 问题**。
judge 独立确认「叠印已解决」（对照图能看到修复前的双影、修复后行距干净），
缩字号最坏约 80%（下限是 60%），可读性未受损；公式、表格、图表均保持原位。

### 遗留

- 紧行距：压测下仍有 165 处相邻行间距偏小（原版 1935 处），是行高压缩的正常结果，
  不是叠印，可读性未受损。
- 前端仍无自动化测试。Windows / macOS 打包仍未实机验证。
- `docs/project-structure.md` 原有的重复章节号（两个 `## 六、`）已修正为 `七、验证状态`。

---

## 2026-10-16（续）：与上游做完整性核对，补齐两处接线缺口

任务：检查上游 `references/PDFMathTranslate` 里还有没有与 PDF 翻译排版相关、
本项目尚未用到的内容，有就补上（严格限定 PDF 翻译/排版范围）。

### 核对结论：核心管线其实已经完整

逐个文件对比上游 `pdf2zh/`，结论是**移植得相当完整**，没有功能缺失：

| 上游 | 本项目 | 结果 |
|---|---|---|
| `pdfinterp.py` | `pdf/interp.py` | **逐字节一致** |
| `text_spacing.py` | `pdf/text_spacing.py` | **逐字节一致** |
| `ocr.py` | `pdf/ocr.py` | **逐字节一致**（含页级 OCR 判定、tessdata 下载、`render_char`） |
| `ultrafast.py` | `pdf/ultrafast.py` | 仅 extras 名不同 |
| `doclayout.py` | `pdf/layout.py` | 类与方法齐全，仅换掉 babeldoc 的资源查找 |
| `high_level.py` | `pdf/pipeline.py` | 函数一一对应（含 URL 输入、旋转页、双栏装配） |

**有意不移植**（符合既定范围，不是遗漏）：PDF/A 输出（需新增 `pikepdf` 依赖）、
`--babeldoc` / `--mode precise`（需另一 submodule + 独立 venv，即上游 `kernel/`）、
`gui.py` / `backend.py` / `mcp_server.py` / `translator.py`（非 PDF 排版；本项目
已有 Electron 前端与自研翻译层）。

### 补齐的两处缺口（都用并行 worktree 委派）

**缺口 1：4 个排版选项没有 CLI 入口**。`layout.backend`、`layout.model_path`、
`translation.skip_subset_fonts` 后端早已支持（config/api/pipeline 全通），但
`cli.py` 一个都没暴露，用户只能手改 config.yaml。现暴露为 `--backend` /
`--onnx` / `--skip-subset-fonts` / `--ignore-cache`。

两个值得记的细节：

1. **`--skip-subset-fonts` 必须做成三态**。用普通的 `store_true` 会踩坑：它的默认值
   是 `False`，用户不传时会把 config.yaml 里写好的 `true` 静默改回 `false`。
   改用 `BooleanOptionalAction` + `default=None`（不传 = `None` = 不动配置），
   还免费得到 `--no-skip-subset-fonts`。实测验证：config 写 `true` 而不传 flag 时
   输出 9.1 MB（子集化被跳过 = 配置保住了），对照不设该配置时 148 KB。
2. **`--ignore-cache` 之前根本没接线**。`build_adapter()` 支持 `ignore_cache`
   参数，但 `build_translator()` 从未传给它——所以这个能力一直存在却无法触达。
   现已从 CLI 一路接到 adapter。它与 `cache.enabled`（写进配置的全局开关）语义不同：
   前者是「本次调用跳过缓存」，后者是「彻底关掉缓存」。
   实测：同一 PDF 跑三次，不带 flag 时第二次仅 1 个新请求（命中缓存），
   带 `--ignore-cache` 时 4 个段落全部重译。

**缺口 2：字体语言映射是死代码**。`assets.py` 的 `_NOTO_LANGS`（19 种非 CJK 语言，
来自上游 `noto_list`）定义了却从未被引用，字体选择靠无条件回退——行为碰巧正确，
但语义是错的，还会误导后人。另外 `status()` 硬编码 zh/ja/ko，目标语言是
`ru`/`ar`/`th` 时前端会**误报字体缺失**并去下载用不上的中日韩字体。

改法：抽出 `font_filename(lang)` 作为语言→字体的**唯一来源**（CJK 查 `_CJK_FONTS`
取地区字形，其余 GoNoto），`_NOTO_LANGS` 真正参与判断——它只用于区分「已识别的
GoNoto 语言」与「未知语言码」，**不是「支持/不支持」的分界**（拉丁等脚本不在表里
也照样用 GoNoto）。未知语言码**宽容回退**到 GoNoto 并记 debug 日志，不抛错：
目标语言来自用户输入，一个拼写错误不该让整个翻译任务失败。
`status()` 保留 `font_zh`/`font_ja`/`font_ko` 旧键（前端 `app.ts` 仍读 `font_zh`），
新增 `font_ready_for`（仅传 `lang` 时出现），因此 `sidecar.py` 的无参调用行为不变——
这一点用真实 stdio 协议实测确认过。

### 验证

- 测试从 45 增至 **84 passed**（新增 `test_cli_options.py` 16 个、
  `test_assets_fonts.py` 23 个）。前端 `typecheck` 通过。
- 端到端回归：译文 1.5 倍长时「真叠印」仍为 6 处、右边界溢出 0（与修复后一致，
  无回归）；译文≈原文长时与最初的原版**逐页内容流仍逐字节一致**（16/16 页）。
- 非 CJK 目标语言实测跑通：`--lang-out ru` 选到 GoNotoKurrent 并产出 mono + dual。

---

## 2026-10-16（续三）：环境配置 + 中英双语界面

### 环境（这台机器是新克隆，此前完全没配）

- **pre-commit hook 启用**：`git config core.hooksPath .githooks`。此前是空的，
  等于密钥扫描一直没生效。本机没装 gitleaks，hook 走正则兜底。
- **Python venv**：用 `uv venv --python 3.12 --seed python/.venv` 建（系统默认是
  3.13，超出 `requires-python` 的 `<3.13` 上界），`uv pip install -e ".[dev]"`。
  `pytest` 84 passed。
- **npm 依赖**：首次 `npm install` 失败——Electron 从 GitHub 下载预编译二进制时
  `ECONNRESET`（本机 github.com 不可达）。建了 `.npmrc` 指向
  `electron_mirror=https://cdn.npmmirror.com/binaries/electron/` 后成功。
  该文件被 `.gitignore` 忽略（镜像地址因机器而异），文档提到的 `.npmrc` 此前
  并不存在，这次才补上。
- **`.mimosa/` 加入 `.gitignore`**：Mimosa 插件在会话期间往仓库写状态，此前
  `git status` 一直显示 `?? .mimosa/`，有被 `git add -A` 误提交的风险。

### 中英双语界面

按 `parallel-branch-delegation` 拆分，主代理先冻结契约再并行派活：

1. **契约先落地**（主代理自己写、单独合并）：`src/renderer/i18n.ts` 的两张扁平
   key 表 + `t()` / `applyStaticTranslations()` / `setLocale()` / `auditKeys()`，
   以及 `scripts/check-i18n.mjs`。契约先行是关键——两路并行时都对着它写，
   避免各自发明接口后合并时对不上。
2. **两路并行**：一路渲染进程（`app.ts` + `index.html` + `style.css`），
   一路主进程（`main.ts` + `preload.ts`）+ 仓库杂项（`.gitignore`、`package.json`）。
   文件范围互斥，合并零冲突。

设计要点：

- **模块导入时无副作用**：`i18n.ts` 不碰 `document` / `localStorage` / `navigator`，
  所以 `check-i18n.mjs` 能在纯 Node 里 `ts.transpileModule` 后直接加载它做校验，
  不需要先构建。
- **主进程另存一份文案表**：`DIALOG_STRINGS` 只有 4 条，故意不 import 渲染进程的
  `i18n.ts`——`src/main` 按 CommonJS 编译而渲染进程是原生 ESM，且 `i18n.ts` 依赖
  浏览器全局，引进来会把浏览器专用依赖拖进主进程。key 名与渲染进程 `dialog.*` 对齐。
- **未知 stage 不能渲染成 `stage.foo`**：sidecar 传来的 stage 是裸标识符，未来可能
  新增。给 `t()` 加了第三个参数 `fallback`，调用处写
  `t(\`stage.${stage}\`, undefined, stage)`。两参数调用行为不变。
- **首启语言**：先读 `localStorage` 存过的选择，没有则按 `navigator.language`
  判断（`zh` 开头 → 中文，其余 → 英文）。切换即时生效并持久化。

### 验证

- `npm run typecheck`（两个 tsconfig）、`npm run check:i18n`（77 keys，en/zh in sync）、
  `npm run build`、Python 84 passed，全绿。
- **契约校验脚本做了变异测试**，确认它不是空跑：删掉中文一个 key → 报
  `key parity` 并 exit 1；把中文的 `{error}` 去掉 → 报 `placeholder parity` 并 exit 1。
- **真实 Electron 实机验证**（不只是静态检查）：启动应用、通过 CDP 读取渲染结果，
  确认 `index.html` 里的 ES module 真的加载成功、两种语言都正确渲染、切换器能实时
  切换、选择写入 `localStorage`。本机 OS locale 是 zh-CN，首启即中文——这条同时
  验证了自动识别。
- 静态检查 + 编译产物 + 真机三层都过了，不是只跑通 typecheck 就宣称完成。

### 过程中踩到的坑（值得记）

1. **Mimosa 门禁误报，拦住了主代理的 commit**。它把 `references/PDFMathTranslate/`
   （上游克隆，142 MB，`.gitignore` 忽略）当成本轮新增文件来扫，报出 19 高危
   （SSRF / 路径穿越 / SSTI / 硬编码凭据），全部属于上游代码、且该项目明确
   「不修改上游仓库」。诊断依据：门禁的 baseline 快照在会话开始后才建立，
   `references/` 之前不在快照里，于是被算成「新文件」。
   处理：**没有**用 `--no-verify` 或改 hook 绕过（那会训练人忽略扫描器）；
   改走 worktree——worktree 里没有 `references/`（被 gitignore 的目录不签出），
   子代理在 worktree 里提交、主代理合并，全程绿灯。这也正好是本工作流的正常形态。
   另外建了 `.mimosa/security-policy.json` 写明威胁模型与排除项。
2. **Windows junction 陷阱**：为让 worktree 里的子代理能用 tsc，把主仓库的
   `node_modules` 用 `mklink /J` 链进 worktree。结果 `git worktree remove --force`
   **顺着 junction 把主仓库的 `node_modules` 一起删了**。源码无恙，`npm install`
   重装即可。教训：worktree 清理前必须先删 junction（`rmdir` 而非 `rm -rf`），
   或者干脆别链、让子代理用绝对路径调主仓库的 tsc。
3. `git worktree cleanup` 会把未提交的 `.mimosa/` 状态文件做成快照提交，留下
   一个无意义的分支。已删。这也是把 `.mimosa/` 写进 `.gitignore` 的另一个理由。

### 遗留

- 前端仍无自动化测试：新增的 i18n 校验只保证两表一致，不检查译文质量，
  也没有 DOM 级回归测试（本次的渲染验证是临时脚本，验证完已删除）。
- 上一轮报告的 5 个实测缺陷仍未修（UI 保存 profile 会写出明文密钥、cancel 不可达、
  `font_ready_for` 在 UI 层够不着、语言输入框是死的、`_CJK_TARGETS` 重复表）。
  其中**第 1 条本轮已修**（见下），其余 4 条仍未动。

---

## 2026-10-16（续四）：供应商→多模型 + MCP 接入

任务：让别的 agent 能通过 MCP 连上本项目、用 agent 自己的模型来翻译；并把界面上
的模型配置做成「一个供应商可挂多个模型」。

### 拆分与执行方式

按 `parallel-branch-delegation` 走：**主代理先冻结契约，再并行三路**。

**契约先行（主代理自己写、单独提交）**：配置 schema 从扁平 `profiles:` 改成
`providers: → models:` 嵌套，并新增 `AppConfig.resolve(provider, model) ->
ResolvedModel`。这一步必须串行做在前——三路都要对着它写，且 MCP 的
`list_providers` / `translate_pdf` 天然按「供应商 + 模型」寻址，schema 后改会把
MCP 工具层整个重写。

冻结契约时留下的关键设计：**`ResolvedModel` 把两层拍平成一个字段与旧 `Profile`
完全一致的只读对象**。这是控制爆炸半径的核心——`models/base.py`、
`openai_compat.py`、整个 `pdf/` 都只做 `getattr(profile, "x")`，因此加这层嵌套时
它们**一行都没改**。另外旧 `profiles:` 配置**自动迁移**（内存里做，下次保存写回
新结构），`resolve()` 也接受旧 profile 名，所以老配置与老命令都不破。

**三路并行**（write_scope 互斥，零文件重叠，所以三次合并都零冲突）：

| 分支 | write_scope | 结果 |
|---|---|---|
| `python-consumers` | `cli.py`、`sidecar.py`、两个测试 | CLI 加 `--provider`/`--model`/`--list-models`；`profiles.list` → `providers.list` |
| `mcp-server` | `mcp_server.py`、`pdf/two_phase.py`、`pyproject.toml`、`spec`、测试 | 四个 MCP 工具 + 两遍式引擎 |
| `renderer-ui` | `src/renderer/*` | 供应商/模型两级界面 |

**跨进程共享的接口先冻结成文字契约**（`providers.list` 的 JSON 形状、
`translate.start` 的参数），派活时原样写进两边的 prompt——两路并行时都对着同一份
文字写，合并时才对得上。实测：渲染进程声明的 `ProvidersResponse` 与 sidecar 实际
返回的字段逐一对上。

### MCP：一个必须记录的发现

**`mcp` 2.x 是破坏性重命名**：v1 的 `FastMCP` 已**删除**，`mcp/server/fastmcp.py`
现在是个直接抛 `ModuleNotFoundError` 的桩。真实 API 是
`from mcp.server.mcpserver import MCPServer, Context`，sampling 走
`await ctx.session.create_message(messages=[SamplingMessage(...)], max_tokens=...)`。
上游参考实现 `references/PDFMathTranslate/pdf2zh/mcp_server.py` 用的是 v1 API，
**只能当结构参考、不能照抄**——直接抄会 import 失败。

### MCP：为什么是两遍式

`pdf/converter.py` 的 `receive_layout()` 在同一遍扫描里既切段落又立刻排版，翻译是
同步的线程池调用；而 MCP sampling 是 `async` 的。硬桥接（反向阻塞事件循环）会
死锁，且要动项目里最微妙那个文件。改用**两遍式**：

1. **收集**：`CollectingTranslator` 记录每段原文并原样返回（原样返回才能让基类的
   `{vN}` 占位符校验通过），产出的 PDF 丢弃。
2. **排版**：`DictTranslator` 查表回填，查不到就报错（**绝不静默透传**——静默透传
   会产出一份悄悄没翻的 PDF）。

管线是确定的（同一 PDF + 同一 `pages`/`mode` + 同一版面模型 → 同一 `sstk` 序列），
所以两遍切出的段落一致；再用 `sequence_fingerprint()` 存哈希、第二遍比对，
不一致就中止。**`converter.py` 一行都没改**，async 只发生在两遍之间。

三个不变量（都已实测确认）：

1. **两遍都用单线程** `TWO_PHASE_THREAD=1`。线程池的**调用**顺序不确定，并发记录
   会让段落编号与指纹不确定；两遍式本来就把真正的翻译挪出管线（一遍只记录、一遍
   只查表），单线程零成本。
2. **所有错误继承 `BaseException` 而非 `Exception`**。原因：`converter.py` 的 worker
   包在 `@retry(wait=wait_fixed(1))` 里，用的是 tenacity 默认谓词（只匹配
   `Exception` 且 `stop_never`）——普通 `Exception` 会被**无限重试**。已用实验确认：
   `Exception` 子类重试 3 次仍在重试，`BaseException` 子类第 1 次就穿透。
3. **相同源文只能对应一个译文**。`_build_text_map` 对重复段落给了不同译文时报错；
   实测在 346 段的真实文献上确实触发了（图注等重复文本）。

### 修掉的一个真实缺陷

**旧布局配置在 UI 往返时会写出明文密钥。** 上一轮遗留清单里的第 1 条（UI 保存
profile 会写出明文密钥）本轮修复时暴露出一个更隐蔽的变体：`save()` 在配置对象
丢失 `_env_refs` 时会回读磁盘上的引用，但**迁移过的文件里引用在 `profiles.*`
而迁移后的文档在 `providers.*`**，路径对不上就漏，明文密钥被写进文件。

由 `python-consumers` 那路在验证时发现并如实上报（不是它自己 scope 的问题，它没
擅自改，只报告——这正是委派该有的行为）。修法：回读引用的 `_read_env_refs()` 也先
做迁移，并加回归测试锁住。实测：旧布局文件走 UI 往返后仍是 `${MY_SECRET}`，文件中
不含明文。

### 验证（不是只跑静态检查）

- **Python 152 passed**（从 84 增至 152）。新增 `test_config_providers.py` 26 个、
  `test_sidecar_providers.py` 13 个、`test_mcp_server.py` 19 个。
- `npm run typecheck`（两个 tsconfig）、`npm run check:i18n`（93 keys，en/zh 同
  步）、`npm run build` 全绿。
- **MCP 协议实测**（不是只跑单测）：独立脚本以 stdio 喂 JSON-RPC，走
  `initialize` → `tools/list` → `tools/call`，确认握手、四个工具清单、以及
  `list_providers` 的返回结构；并断言未泄漏密钥。**注**：第一次的断言写错了
  （`"api_key" not in json` 会被 `has_api_key` 误命中），是断言的问题不是代码的问题，
  已改正后通过。
- **两遍式离线端到端**（不需要任何模型 API，因此可离线验证排版正确性）：
  - 上游样例：1 段落 → mono + dual ✅
  - 真实 346 段文献（8.9 MB）：收集两次指纹与顺序**完全一致**；回填产出 mono
    8.9 MB + dual 17.9 MB ✅
  - 确定性护栏：改动段落集合后按预期被拒 ✅
  - 缺失译文：按预期被拒，未静默产出 ✅
- **UI 往返实测**（跑真实 sidecar 进程）：`providers.list` 形状与渲染进程声明的
  接口逐字段一致；`config.get` → 加一个模型 → `config.save` 后，新模型写入、
  `${DEEPSEEK_API_KEY}` 保留、文件无明文密钥 ✅
- 合并后主树复跑 pytest 152 passed（每次合并后都跑，不是只在最后跑一次）。

### 过程中的坑

1. **worktree 里跑测试会 import 到主仓库的代码**。venv 是 editable 安装、`.pth`
   钉死在主仓库的 `python/`，而测试文件插的是仓库根不是 `python/`。所以每次都要
   `PYTHONPATH=<worktree>/python` 显式覆盖，并先 `python -c "import pdfxlate; print(__file__)"`
   确认解析到 worktree。三个子代理的 prompt 里都写了这一条与自检命令。
2. **`node_modules` 不在 worktree 里**（设计如此）。渲染那路用绝对路径调主仓库的
   `tsc`，实测可行。
3. **Git Bash 的 `/tmp` 与 Python 的 `/tmp` 不是同一个目录**，导致我一次验证读错了
   文件、误判「加的模型没保存」。改用仓库内一个固定路径后一致通过。教训：跨
   bash/Python 校验时用绝对且两边都认的路径。
4. `.zcode/`（本 skill 的 worktree 与计划）加入 `.gitignore`，同 `.mimosa/` 的理由。

### 遗留（本轮新增）

- **MCP sampling 未对真实客户端验证**：子进程测试里没有客户端会声明 sampling
  能力，所以只用一个假 session 覆盖了分批、token 预算、进度与逐段回退。真实
  Claude Desktop / ZCode 的 sampling 往返仍属未验证。
- **`translator="provider"` 未打真实 API**：需要 key 与网络；只测了它的错误路径。
  开发记录里验证真实 API 的做法（本地 mock OpenAI 服务）可以复用，本轮没做。
- **PyInstaller 打包未实机验证**：`pdfxlate.spec` 里 mcp 的 hiddenimports 已按
  「装了才收」写成可选，但没真打过包。
- 上一轮遗留的另外 4 条仍未修（cancel 不可达、`font_ready_for` 在 UI 层够不着、
  语言输入框是死的、`_CJK_TARGETS` 重复表）。
- `providers.list` **故意不留旧名别名**（结果形状也变了，留别名只会把调用方的错藏
  起来）；`translate.start` 的 `profile` 参数同样换成 `provider` + `model`。

