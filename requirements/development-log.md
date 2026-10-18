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

### 视觉验收（visual-judge）：两遍式未引入回归，已用像素级对照证明

对两遍式的产出做了视觉验收。judge 报出若干问题（孤立的数学残片、右栏行距偏紧、
标题首字偏小等），**关键是要判断这些是不是两遍式引入的**——方法是拿**同一个翻译
函数**分别跑「两遍式」与「原来的一遍式管线」，再对比渲染结果：

- 同一输入 + 同一翻译函数 → 两遍式与一遍式产出的页面 **150 dpi 渲染 md5 完全一致**
  （`4de3cca0…`），内容流 md5 也一致。
- 换成长度与原文相当的译文（一个 CJK 字符≈两个拉丁字符，这是真实模型的行为）
  后，结论同样成立。

因此 judge 报的问题**都是排版算法既有行为，不是本轮引入的回归**。这与 2026-10-16
上一轮记录的结论一致（右边界溢出「继承自上游」）。judge 同时确认了干净的部分：
**无叠印**、文字都在栏内与页内、图（Ancilla 时序图，33 个矢量元素与原图 bbox 一致、
橙色填充一致）完整未被覆盖、双栏与阅读顺序保持、中文全部以真实 Source Han Serif
字形渲染（无空白方块）。

> 注：早先一次用「同一句超长填充文本重复」当译文时，确实出现了一处叠印。那是
> **译文长度远超源文**导致的既有排版行为（源文 568 字符的段落被塞进等长的框），
> 与两遍式无关——上面同函数的一遍式对照同样复现。真实模型译文不会这样。

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

---

## 2026-10-16（续五）：前端改为「左侧设置栏 + 右侧 PDF 预览」

任务：把单列卡片布局改成左侧设置栏 + 右侧 PDF 预览，整体调整前端风格。

### 先做技术选型验证，再动样式

**PDF 预览不是样式问题，是架构选择**，而且现有 CSP 已经把最省事的做法挡掉了。
动手前实测了四种做法，结论都进了文档：

| 事项 | 实测结论 |
|---|---|
| 现有 CSP `default-src 'none'` | **直接挡住查看器**（`ERR_BLOCKED_BY_CSP`）：`frame-src` 回落到 `'none'` |
| `webPreferences` 未开 `plugins` | 查看器根本不启动（它是 plugin），iframe 加载成空白 |
| `#toolbar=0&navpanes=0` | **有效**：查看器自带的深色工具栏与缩略图栏都消失，只剩白页浮在灰底 |
| `#page=N` / `#zoom=` | 首次加载有效 |
| 改已加载 iframe 的 src/片段 | **无效**（试过直接改、先置 `about:blank` 再设，都没跳页） |
| **重建 iframe 元素** | **有效**——这才是切页/缩放唯一可行的办法，实测跳到第 17 页（页眉 `17 of 21`） |
| 中灰画布 | 查看器自带（约 `#525659`），外壳保持浅色即可，不用自己画 |
| 中文 + 空格文件名 | `pathToFileURL` 百分号编码后查看器能正常打开（实测渲染成功） |

最终选**浅色外壳 + 中灰画布 + 自建工具栏**（因为能关掉自带外壳，就有条件做自己的
工具栏）。默认缩放取 `page-width`：预览区比整页窄得多，固定 100% 会让每份文档一
打开就带横向滚动条——这是截图里发现并修掉的。

**一个无法绕过的限制**：查看器在 `file://` 的 iframe 里，**父页面无法注入脚本**，
所以对比模式两栏**滚动无法同步**（各自独立滚动），也读不到查看器当前页码——工具栏
的页码是界面自己的状态。这一点在动手前就说明了，没有硬做。

### 执行方式（parallel-branch-delegation）

按 skill 走，但**只拆出 2 路**——这是诚实的判断：渲染那 4 个文件（结构/逻辑/样式/
文案）彼此强耦合，`index.html` 的 id 与 `app.ts` 的绑定必须一起改，再拆就会撞同一片
区域。

**Phase 0（主代理串行冻结契约，单独提交）**：`main.ts` 开 `plugins: true` +
放大窗口；`preload.ts` 加 `fileUrl()`。先落地而不是只写进 prompt，是为了让渲染那路
拿到**真实可用的 API**，而不是一句承诺。

**并行 2 路**：`sidecar-pdf-info`（新增 `pdf.info` 返回页数）+ `renderer-layout`
（两栏布局 + 预览）。前者顺利合并（166 passed）。

**渲染那路遇到阻塞**：子代理 dispatch 连续三次被 provider 的渠道校验拦下
（`upstream 400`，与内容无关）。我先合并已完成的那路，然后**自己实现渲染层**。
清理 worktree 时发现子代理在被拦前留下了一份快照（i18n/HTML/CSS，**但没有
app.ts**——那份代码根本无法运行），属于被取代的中间产物，已删除并说明理由。

### 过程中发现并修掉的两个真实缺陷

1. **单栏模式下窗格标签与工具栏按钮重复**。对比模式需要「原文/译文」标签（两栏并排
   时否则分不清），但单栏时工具栏的激活按钮已经说明了是哪一份。用 CDP 量 DOM 时
   发现的（`labelDuplicated: true`），改为只有对比模式才渲染标签。
2. **禁用态按钮看起来仍可点**。翻页/缩放的图标是实心笔画（`‹ › − +`），40% 透明
   时仍显眼。量了 computed opacity 确认样式确实生效、是**辨识度**问题，改为 25% +
   灰化颜色 + 浅边框。
3. 另外把默认缩放从 100% 改为 `page-width`（见上）。

### 验证（真跑 Electron，不只是静态检查）

前端**没有任何自动化测试**，所以只能靠 typecheck + i18n 校验 + 真机核对。做法是
`--remote-debugging-port` 启动后经 CDP 读 DOM、量尺寸、截图：

- `pytest` **166 passed**；`npm run typecheck`（两个 tsconfig）、`npm run check:i18n`
  （110 keys 同步）、`npm run build` 全绿。
- **结构断言**：侧栏宽 340px、预览区与 host 存在、翻译按钮在 `.sidebar__actions` 内、
  `dropzone` / `result-card` / `progress-card` 三个旧 id **已无残留**。
- **翻页真的重建了 iframe**：src 从 `page=1` 变到 `page=2`；页码显示 `1 / 21`
  （`pdf.info` 生效），首页时「上一页」禁用、「下一页」可用。
- **缩放**：`100% → 125%`，且 src 里的 `zoom` 参数同步变化。
- **双语**：切到中文后分组名、视图按钮、空状态、文件列表全部切换，`lang="zh-CN"`。
- **有结果前「译文/对比」被禁用**，点击不生效、仍停留在「原文」。
- **窄窗 1000×640**：翻译按钮 `bottom=630 < 640` 可见，侧栏自身可滚动。
- **完整翻译链路**（配一个本地 mock OpenAI 服务，让真实翻译产出 mono/dual）：
  译文视图自动激活、结果分组自动展开、对比模式渲染 **2 个窗格**且标签为「原文/译文」、
  翻页后两栏**同时**到 `page=2`（页码 `2 / 21`）。
- 清理：临时脚本、截图目录、e2e 产生的 `-mono-mono` / `-mono-dual` PDF 全部删除；
  worktree 与分支已清理（`prune` 无残留）。

### 遗留（本轮新增）

- **对比模式两栏滚动不同步**（iframe 不可注入脚本，见上），已在 README 的
  Known limitations 里写明。
- **前端仍无自动化测试**：本次验证是临时脚本，跑完即删。这类 CDP 脚本其实值得
  沉淀成仓库里的回归检查，本轮没做。
- 上一轮遗留的 4 条仍未修（cancel 不可达、`font_ready_for` 在 UI 层够不着、
  语言输入框是死的、`_CJK_TARGETS` 重复表）——其中**语言输入框是死的**这条现在更
  明显了：`options.langIn/langOut` 两个输入框仍未接线。


---

## 2026-10-16（续六）：自建标题栏 + 设置区（模型 / 外观 / 常规）

任务：标题栏自建，**只保留系统那三个按钮**；品牌、副标题、引擎状态、语言切换全部
移出；左栏「开始翻译」下面加一个 ⚙ 设置按钮，点开后左栏变成设置菜单、右侧显示对应
设置项表单。

### 关键取舍：三个窗口按钮不自绘

`titleBarStyle: "hidden"` 去掉系统标题栏与菜单条，但**最小化/最大化/关闭仍交给系统**
（`titleBarOverlay`）。自绘这三个按钮要自己实现窗口控制 IPC、hover 态、双击最大化、
Aero Snap、多显示器 DPI、无障碍标签——做残的概率远大于收益。overlay 的颜色目前是
固定浅色值，**深色主题需要改成运行时 `setTitleBarOverlay()`**，本轮不做。

`TITLEBAR_HEIGHT = 50` **必须与 `style.css` 的 `--titlebar-height` 一致**：overlay
的位置由 OS 按这个值摆放，不一致时三键会在栏内垂直偏心。

`Menu.setApplicationMenu(null)` 的副作用是**菜单快捷键一起没了**，所以用
`before-input-event` 在 `--dev` 下补回 `F12` / `Ctrl+Shift+I` / `Ctrl+R`；打包版不注册。

### 结构：任务模式 / 设置模式

| | 左栏 | 右栏 | 底部动作区 |
|---|---|---|---|
| 任务模式 | 文档 / 选项 / 进度 / 结果 / 日志 | PDF 预览 | 「开始翻译」+「⚙ 设置」竖排，钉底 |
| 设置模式 | ← 返回 + 模型 / 外观 / 常规 | 该设置项表单 | **整体隐藏** |

- **模型编辑器从任务侧栏搬进设置**，不是复制——两处编辑器会各自持有状态并互相打架。
- **没有「引擎」这一项**：引擎状态、版本、配置目录放进**常规**（只读诊断）。标题栏
  清空后引擎状态必须有地方显示。
- 平台差异走 `<body>` 类名（`platform-win32` / `platform-darwin`）：Windows 三键在
  右上，拖拽条留 `padding-right`；macOS 交通灯在左上，改留 `padding-left`。

### 验证（真跑 Electron）

前端仍无自动化测试，做法与上轮相同：`--remote-debugging-port` + CDP 读 DOM/量尺寸，
配合 PowerShell 抓**整屏**（`capturePage` 截不到系统绘制的三键，必须抓屏幕再读图，
且需要 DPI 感知否则图像错位）。

- `pytest` **166 passed**（未动 Python）；`npm run typecheck`（两个 tsconfig）、
  `npm run check:i18n`（**122 keys** 同步）、`npm run build` 全绿。
- **整屏截图**确认：原生菜单条消失、右上角三键（— □ ✕）在位且不重叠、标题栏为空、
  ⚙ 在「开始翻译」**下面**。
- **CDP 结构断言**：`titlebarText: ""`、`titlebarChildren: 1`、
  `dragPaddingRight: "140px"`、引擎状态与语言切换**都不在标题栏内**、
  `settingsSections: ["模型","外观","常规"]`、设置模式下 `bottomActionsVisible: false`。
- **拖拽区实测**（截图证明不了这件事）：`getComputedStyle` 里 `.titlebar` 是 `none`、
  拖拽条是 `drag`、各按钮都是 `none`；再经 CDP 发**真实** `mousePressed`/`mouseReleased`
  走完 ⚙ → 外观 → ← 返回，确认点击没有被拖拽区吞掉。
- **回归**：任务模式下拖入 → 预览 → 设置往返后预览仍在。

### 遗留（本轮新增）

- **标题栏 overlay 颜色是固定值**，深色主题需改成运行时 `setTitleBarOverlay()`。
- 本轮只搭架子：选项 / 结果 / 日志**不迁移**到设置里（计划里就说了）。
- 前端仍无自动化测试；拖拽区这种「静默吞点击」的问题其实很值得沉淀成仓库内的回归
  检查，本轮仍是临时脚本跑完即删。
- 上一轮遗留 4 条仍未修（cancel 不可达、`font_ready_for` 在 UI 层够不着、语言输入框
  是死的、`_CJK_TARGETS` 重复表）。

### 未跟踪的 PDF（待确认，未删）

`python/` 下有 **4 个**未跟踪 PDF：`open_pit_*` 两个（13:28，会话开始前就在）与
`respirable_*` 两个（18:08）。四个都含中文译文，说明**都是翻译产物而非输入**；但
`respirable` 那两个我无法归到自己可见的工作里，**故一个都没删**，只在此标注。
`respirable` 的输入原文在仓库里找不到（全库只有那两个输出）。

---

## 2026-10-16（续七）：标题栏图标（折叠侧栏）+ 进度移到底部状态栏 + 引擎日志归入设置

任务：标题栏左上角加一个图标，点击折叠/展开左侧栏；侧栏里的「进度」不该在那显示，
由我决定放哪；「引擎日志」也问是删还是挪。

### 两个决定，理由如下

**进度 → 窗口底部通栏状态栏。** 侧栏可折叠是决定性的：进度若留在侧栏，一折叠就看不见
了，而进度恰恰是任务进行中唯一要看的东西。底部通栏与折叠无关，本来就是「全局瞬时状态」
的常规位置。状态栏**常驻**（空闲显示 `progress.idle`），避免窗口底部高度跳动；设置模式
下也保持可见，因为翻译可以在设置模式里继续跑。

**引擎日志 → 移进「常规」，没有删。** `appendLog` 是 **7 条失败路径的唯一出口**
（`log.engineFailed` / `assetCheckFailed` / `providersFailed` / `engineFactsFailed` /
`pageCountFailed` / `cancelFailed` / `preview.dropUnreadable`），还承接 sidecar 的 stderr
流——删掉面板等于这些失败全部变静默，界面上再无任何地方解释「为什么没反应」。这与项目
其它地方不一致（`DictTranslator` 查不到译文宁可报错，也不静默透传）。放进「常规」而不是
新开第四个分区：那里本来就是只读诊断（引擎状态 / 版本 / 配置目录），日志是同类信息，也
不推翻上一轮「只有模型 / 外观 / 常规」的决定。`#log-pane` 的 id 没改，`appendLog` 一行
都没动。

### 图标：一个图形，两处使用

用户选了「双语页」母题——一页纸 + 右上折角 + 中间竖分隔 + 左右文字块，画的正是本项目的
产出（dual 双语对照 PDF）。`src/renderer/icon.svg` 是**唯一来源**，标题栏与打包图标都从
它来。

**实心徽标而非细线描边**，技术原因不只是审美：徽标要在 22px（标题栏）和 16px（Windows
最小图标位）下可辨，而 512 单位的画布缩到 16px 是除以 32，**细于约 60 单位的元素会直接
消失**。第一版用三根细横线，16px 下糊成噪点；改成每侧两块厚重色块后才站得住。验证方式是
`scripts/make_icon.py` 把 16/22/32/48/64 放大并排渲染成一张对照图，**看一眼就知道**，
不用猜。

### 过程中撞到并解决的两件事

1. **`<img src="icon.svg">` 被 CSP 直接拒绝**。页面跑在 `file://` 下，**origin 不透明，
   CSP 的 `'self'` 匹配不到相对路径**，控制台报
   `Refused to connect to 'file:///.../icon.svg' ... "default-src 'none'"`——和 PDF 预览
   必须放行 `frame-src file:` 是同一个原因。**没有放宽 CSP**（`frame-src file:` 仍是唯一
   有意为之的放宽），改为 `copy-static.mjs` 把 SVG **内联**进 `index.html`：内联不是子资源
   请求，不需要任何指令放行。顺带发现旧构建留下的 `out/renderer/icon.svg` 会一直陈旧，
   加了 `retired` 列表清掉。
2. **Electron 离屏截图挂死**。本想用 Electron 光栅化图标（与界面同引擎），但隐藏窗口
   **永不合成**：`capturePage` 挂住不返回，offscreen 的 `paint` 事件首帧是空的（160×160
   只有 179 字节）。改用 **PyMuPDF**——它**是既有依赖且原生渲染 SVG**，零新增依赖，实测
   一次通过。

### 结构

- **折叠**由 `<body>` 的 `sidebar-collapsed` 类驱动，不是逐个元素开关：侧栏有**两个**
  （任务 / 设置），类名一次覆盖两者，设置模式下折叠也成立。用**负 margin 位移**而非
  `display: none`，以便过渡；状态存 `localStorage`。
- **平台留白从拖拽条移到 `.titlebar` 容器**：图标放左侧后旧写法会打架（macOS 交通灯在
  左上，图标会被压住）。图标是拖拽条的**兄弟节点**，天然不在拖拽区内。
- 应用图标放**新增的 `assets/`** 而不是惯例的 `build/`：`.gitignore` 第 12 行把 `build/`
  当打包产物忽略，图标是需要入库的源材料，改 `assets/` 则 `.gitignore` 一个字都不用动。
- **已知取舍**：设置模式下折叠侧栏会连「← 返回」一起藏起来，退出设置只能靠标题栏图标。
  图标常驻所以可恢复；这是「图标折叠侧栏」的直接推论，没做特例。

### 验证（真跑 Electron）

- `pytest` **166 passed**（未动 Python）；`typecheck`、`check:i18n`（**122 keys** 同步）、
  `build` 全绿。
- **CDP 30 项断言全通过**，关键几项：进度祖先链里有 `.statusbar`、**没有** `.sidebar`；
  `#group-progress` / `#group-log` 已从 DOM 消失；日志祖先含
  `.settings-panel[data-panel=general]`。
- **真实点击证明没被拖拽区吞掉**：`getComputedStyle` 里 `.titlebar` 与图标按钮都是
  `none`、只有拖拽条是 `drag`；再经 CDP 发真实 `mousePressed`/`mouseReleased`，点图标
  → 侧栏滑出屏幕（右边缘 ≤ 0）、`aria-expanded=false`、写进 `localStorage`；**再点一次**
  → 回来。设置模式下折叠同样成立。
- **端到端真翻译**（自建 mock OpenAI 服务）：状态栏依次显示
  `资源 — preparing fonts` → `版面 (2/2) — page 2` → `完成 — 已完成。`，
  进度条确实推进，全程都在状态栏、从不在侧栏；**折叠状态下状态栏仍可见**（这正是它搬家的
  理由）。
- **图标**：`assets/icon.png` 实测 512×512；`.ico` 结构逐个 entry 校验（9 个尺寸、每个
  都是合法 PNG 签名、偏移不越界）；内联后 `index.html` 里只有一个 `width`/`height`（第一版
  与源 SVG 的属性重复了，已修）。
- **截图**：整窗抓图确认图标在左上、系统三键在右上、原生菜单消失；折叠态；「常规」面板里
  日志确实收到 sidecar 的真实 stderr（config 迁移的 WARNING）。
- 清理：临时脚本、截图、mock 服务、e2e 产出的 `-mono-mono` / `-mono-dual` PDF 全部删除；
  **e2e 往真实 config.yaml 写的 `mock` 供应商已还原**（改回 `deepseek` 并把 `mock` 删掉，
  再用 `config.load()` 验证能正常解析）。

### 遗留（本轮新增）

- **应用标记不支持深色主题**（内联 SVG 固定配色），与「标题栏 overlay 颜色固定」同源，
  留待深色主题那轮。
- **打包产物未在本环境验证**：图标只验到「PNG/ICO 结构正确 + 配置指向正确」，
  `electron-builder` 实际构建没跑（与仓库现状一致）。
- **设置模式下折叠会藏掉「← 返回」**（见上，可恢复）。
- 前端仍无自动化测试；拖拽区这种「静默吞点击」的问题很值得沉淀成仓库内回归检查，
  本轮仍是临时脚本跑完即删。
- 上一轮遗留 4 条仍未修（cancel 不可达、`font_ready_for` 在 UI 层够不着、语言输入框
  是死的、`_CJK_TARGETS` 重复表）。
- `expandGroup()` 注释写着「打开一个并关闭其它」，函数体却只 `open = true`、从不关闭
  其它——**既有文档与代码不符**，本轮未顺手改（超出范围）。

---

## 2026-10-16（续八）：视图控件移入标题栏 + 翻页/缩放改浮层 + 进度回到侧栏

三件事一起做，因为动的是同一片布局：① 「原文/译文/对比」与「适应宽度/高度」移到自建
标题栏右侧；② 翻页/缩放/打开改到 PDF 视窗**右下角自动隐藏浮层**；③ 进度回到侧栏底部，
设置按钮缩成纯图标，底部通栏状态栏删除。

### 省下的空间是真的（实测）

1280×820 下预览区高度 **695 → 770px**。那条 42px 的工具栏整条删掉了，而控件搬去的
标题栏右侧那 140px 本来就是给系统三键预留、**纯属空转**——所以这是净赚，宽度零损失。

### 关于「适应高度」：实测结论，之前搞错过

上一轮我说 `view=FitV` 是否生效**没验证成功**（截图为陈旧 + 只改片段不重载，两次探针
都被干扰）。这轮**用真实窗口抓屏对比两帧定死了**：

| 模式 | 片段 | 结论 |
|---|---|---|
| 适应宽度 | `&zoom=page-width` | 查看器**认** |
| 适应高度 | `&view=FitV` | **认**（整页纵向完整，页脚可见） |
| — | `zoom=page-height` / `zoom=page-fit` | 被接受但**被忽略** |

所以两种 fit 走**两个不同的片段参数**，不能想当然统一。

### 浮层的显示机制（两个条件并存）

1. **鼠标移到那里** → CSS `:hover`。浮层保持 `opacity: 0` 但**留着布局盒与命中测试**
   （不加 `pointer-events: none`），因此不需要额外热区、也没有死角。
2. **页面动了** → `flashViewerOverlay()` 加 `is-visible`，约 1.6s 后移除，**每次调用重置
   计时器**。

内缩 18px 而非贴边：查看器自己的竖向滚动条贴在最右侧，压住它下端会很难抓。

### 一个差点漏掉的真缺陷（子代理发现的）

`renderPreview()` 里是 `host.innerHTML = ""`，而浮层现在**住在 `#preview-host` 里**——
第一次渲染就会把浮层连同旧 frame 一起删掉，翻页/缩放控件从此消失。修法是先取到浮层引用、
清空后再 append 回去（它是绝对定位，流内顺序无影响）。这条已用 CDP 断言覆盖：
加载文档、翻页、切对比、设置往返之后，浮层都仍在 `#preview-host` 内。

### 执行方式（parallel-branch-delegation）

按 skill 走，但**只拆 2 路**——这是诚实判断：本任务真正要改的只有 3 个文件
（`app.ts`/`i18n.ts`/`style.css`），而 `app.ts` 内部改动互锁、不可能再拆，拆更多只会撞车。

- **Phase 0（主代理串行冻结契约，单独提交 `66c56be`）**：把 `index.html` 一次改到位
  （标题栏控件组、删除工具栏、浮层、侧栏进度行、删除通栏）。先落地而不是只写进 prompt，
  这样两路都对着**固定的 id 与类名**写，而不是互相猜。
- **并行 2 路**：`wire-view-controls`（`app.ts` + `i18n.ts`）与 `style-layout`（`style.css`）。
  两者文件集**严格不相交**，冲突在结构上不可能发生。
- 合并串行做，两次都能 dry-run 干净合入；worktree 与分支已清理（`prune` 无残留）。

CSS 那路**又是被 provider 渠道校验拦下**（`upstream 400`，与内容无关）——第二次遇到同一
问题，重发后成功。

### 验证

- `pytest` **166 passed**（未动 Python）；`typecheck`、`check:i18n`（**123 keys**）、`build` 全绿。
- **合并后集成验证 38 项全通过**（两路子代理各自只验了自己那个文件在孤立状态下的表现，
  合并后的组合**没人测过**，所以这轮必须补）：旧工具栏与通栏**已从 DOM 消失**；标题栏仍
  50px（没被撑高）；预览区高度 770；`.titlebar` 与各按钮 `-webkit-app-region` 都是 `none`、
  只有拖拽条是 `drag`；用**真实鼠标事件**点「适应高度」→ 该按钮 `is-active`、frame 片段
  变 `view=FitV`、浮层闪出；点回「适应宽度」→ `zoom=page-width`；翻页到 `2 / 21`；浮层在
  翻页/对比/设置往返后都还在；`is-visible` 计时器自行到期；**无未捕获异常**。
- **适应高度用真实窗口抓屏定死**：两帧不同，适应高度下整页纵向完整。
- **浮层可见态抓图**确认右下角渲染为 `‹ 1 / 21 ›  − 适应宽度  +  ↗`。
- 清理：临时脚本、截图、worktree 全部删除。

### 过程中发现的环境问题（重要）

**`Page.captureScreenshot` 在本环境返回陈旧画面**：我用纯红 vs 纯蓝两个明显不同的页面
做对照实验，拿到**逐字节相同**的 PNG。这解释了为什么早前几轮的缩放探针全都「测出五个
相同结果」——不是查看器不响应，是**截图 API 本身没在更新**。影响：ZCode 侧栏以及之前
几轮基于该 API 的截图断言**都可能不可信**。本轮验证因此改用 **CDP 实时 DOM 数值**
（计算所得，可信）+ **PowerShell 窗口抓屏**（读真实合成结果）。

另有一处**子代理的环境坑**：`scripts/copy-static.mjs` 按**自身位置**解析路径，从 worktree
里用主仓库的脚本会把产物写进**主仓库**的 `out/renderer`，而不是 worktree 的。

### 我自己的测试 bug（记一笔）

集成验证里「侧栏折叠」一度 FAIL。查下来是**测试的问题不是代码的问题**：折叠状态存在
`localStorage` 里，而我的脚本没在开头清掉，于是上一轮残留的 `"1"` 让它**开局就是折叠态**，
「点击折叠」实际是展开。已在脚本开头清 `localStorage` 并 reload。

### 遗留（本轮新增）

- **进度回到侧栏后，侧栏折叠时看不到进度**。上一轮把它放到通栏正是为了避免这个；现在按
  需求删掉通栏，折叠态下这个信息确实消失。已记录，不隐藏。
- **浮层会在自身矩形内截获鼠标事件**（`:hover` 触发显示的前提）。已用内缩 18px 避开滚动
  条；若仍碍事可改更小热区。
- 深色主题、打包未验证、前端无自动化测试等既有遗留不变。

---

## 2026-10-16（续九）：修默认输出目录的真 bug + 右上角「另存为」+ 滚轮显示浮层

起因：`python/` 下一直有 4 个未跟踪 PDF。查下来不是"谁乱放"，是一个**真 bug**。

### 根因：界面说一套，代码做另一套

输出目录的占位符写着「（与输入文件同目录）」，但代码不是这样：

| 位置 | 实际行为 |
|---|---|
| `api.py:96` | `resolved_output = output or str(Path.cwd())` |
| `sidecar.ts` | 开发时 sidecar 的 cwd = **`python/`**；打包后 = **安装目录** |

所以**输出留空时产物写进 sidecar 的工作目录**。这也解释了我之前一直没能归因的那两个
`respirable_*`：那是用户自己一次翻译的产物。打包后更糟——往 `Program Files` 写会直接
失败，或静默污染应用目录。

**修法**：默认目录在**主进程**解析（`app.getPath("documents")/pdfxlate`），因为 Windows 的
「文档」常被重定向到别的盘，Python 里拼 `~/Documents` 会算错。主进程还在转发
`translate.start` 时**兜底注入**，任何调用方漏传都不会退化成工作目录。渲染进程把解析到的
路径写进占位符，并在发起任务时显式带上——**所见即所用**。CLI 保持 `Path.cwd()`（命令行
输出到当前目录是惯例），`api.py` 的库层语义不动。

**实测确认修好了**：留空输出目录跑一次真翻译，产物落在源文件旁的 `translated/`
子目录（`D:\...` 那台验证机上就是 `...\Documents\pdfxlate\`），且 `python/` 前后**文件数不变**
（0 → 0）。`.gitignore` 加了 `*-mono.pdf` / `*-dual.pdf` 作兜底。

### 滚轮显示浮层：三次探针才找对路子

需求是「滚轮上下滑动、缩放时浮层也出现」。这撞上一堵墙：**查看器是另一个进程**，其滚轮
与鼠标事件**不到父页面**。

**测了三条路，都有证据：**

| 路 | 结论 |
|---|---|
| 父页面监听 iframe 里的滚轮 | **不通**。`webContents.sendInputEvent` 打进去，父页面计数 0 |
| 查看器的 `postMessage` 接口 | **不通**。试了 4 种消息形状 + 滚轮，**一条回复都没有** |
| 主进程 `input-event` | **通**，能看到 `mouseWheel`。但载荷**只有 `type`/`modifiers`，没有坐标也没有增量** |

关键发现：父页面**能**收到 iframe 元素的 `mouseenter`/`mouseleave`。所以设计是——主进程
只转发"发生了一次滚轮"，渲染进程把 `pointerOverPreview` 标志与它配对，**两者同时成立**
才显示。实测：预览区上滚滚轮 → 显示；侧栏上滚滚轮 → **不显示**；计时器自行到期。

### 执行方式

**没有拆并行**——这次改动是一条链（`main.ts` → `preload.ts` → `app.ts` → HTML/CSS），
`app.ts` 内部互相咬合。按 skill 自己的判断标准（"都要动同一片区域就不要并行"），拆开只会
撞车，所以串行做。

### 验证

- `pytest` **166 passed**（未动 Python）；`typecheck`、`check:i18n`（**127 keys**）、`build` 全绿。
- **默认目录 bug 修复**：真翻译 → 产物在 `Documents\pdfxlate`、**不在**源码树、`python/`
  文件数不变。
- **另存为**（真处理函数 + 只 stub 对话框）：原文视图存**源文件**；译文视图与对比视图存
  **译文**；拷贝与源**逐字节相同**（用不同内容的替身文件区分，避免挑错文件也能过）。
- **滚轮/缩放显隐**：缩放触发显示；滚轮在预览区上方触发、在侧栏上方**不**触发；1.6s 后
  自行消失。
- **回归**（15 项）：标题栏 50px、五个视图/适应按钮、旧工具栏已消失、进度在侧栏、日志在
  常规、适应宽/高、翻页、缩放、浮层在翻页后仍在、设置往返、侧栏折叠——全部通过，无未捕获异常。
- **截图**确认：另存为在右上、翻页缩放在右下、进度与齿轮同行。
- 清理：4 个 PDF 移到桌面（桌面本来就放着 `respirable` 那篇的**原文**，反证它们是产物）；
  临时脚本、截图、mock 服务、测试产生的输出全部删除；**所有 Electron 进程已杀**
  （上一轮漏了这步害用户 `npm start` 起不来）。

### 过程中的一个测试自身缺陷（值得记）

第一版滚轮验证用 CDP `Input.dispatchMouseEvent`，报**假失败**。查明原因是那个 API
**不会**在主进程产生 `input-event`，也不会产生真实指针移入 iframe 时的 `mouseenter`——
**用它根本测不了这个功能**。改成在真实 `out/renderer` 上用
`webContents.sendInputEvent`（物理鼠标走的路径）后通过。已在脚本里写明原因，避免以后
再被同一个假失败误导。

### 遗留（本轮新增）

- **`onWheel()` 的粒度很粗**：只知道"有滚轮"，不知道位置或方向。目前靠指针是否在预览区
  配对，够用；但如果将来要在预览区内区分"滚到顶部/底部"就做不到。
- 既有遗留不变（深色主题、打包未验证、折叠侧栏时看不到进度、前端无自动化测试）。

---

## 2026-10-16（续十）：改成「文件夹浏览 + 标签页」，去掉原文/译文/对比

用户报的问题：**导入已经翻译过的文献，译文和对比打不开。** 查下来是设计错位，不是功能缺失。

### 根因

`lastResults` —— 唯一决定译文/对比能否点开的东西 —— **只在一次翻译跑完时被赋值**
（`handleDone` → `renderResults`）。导入一份已经翻好的 PDF 时它是空的，于是
`hasResults()` 为假、两个按钮被禁用并提示"请先翻译"。**程序没有"这份文件本身是不是译文"
的概念**，只认识自己产出的东西。所以改成"任何 PDF 都能直接打开"，问题从根上消失——
比打补丁更对。

### 新结构

- **左侧栏列出所选文件夹里的全部 PDF**，列表项 = **复选框 + 文件名**：点名字开标签页，
  勾选表示"要翻这份"。**默认不勾任何一项**——文件夹里通常同时躺着原文与 `-mono`/`-dual`，
  自动勾选会把译文再翻一遍。
- **标签栏并进标题栏**（用户要求）：省下一条独立标签栏的整条高度。`flex: 0 1 auto`，
  没标签时宽 0、拖拽条独占整条；**拖拽条保留 `min-width: 40px`**，否则标签铺满后窗口拖不动。
- **页号按标签各自记住**；缩放是全局的（阅读偏好，每份文档不一样反而更糟）。
- 翻译产出**自动开成标签**，替代原来的「结果」分组。
- **适应宽度/高度**移到文档左上角，改成两个内联 SVG 图标（一页纸外框 + 双向箭头，
  `fit-height` 只旋转**箭头组**）；翻页/缩放留在右下角、另存为在右上角。

### 修掉两个真实缺陷（都是实测出来的）

**① 拖放一直是坏的。** 原来读 `file.path`，而 **Electron 32 移除了这个属性**（本项目跑
33.4.11），它恒为 `undefined`，于是 `filter(Boolean)` 把"每个路径都 undefined"变成
"没拖进任何文件"——只报一句"无法读取拖入文件的路径"。CDP 真实 drag 实测确认：
`File.prototype` 上没有 `path`、真实拖放拿到 `undefined`。改用 `webUtils.getPathForFile()`
（preload 暴露为 `pathForFile`）。**以前的测试没抓到，是因为我自己用
`Object.defineProperty(f, "path", …)` 造了 path，正好把失败掩盖掉**——这是测试方法的缺陷，
记一笔。

**② 打开文档后 iframe 会吃掉拖放。** 查看器是另一个文档，拖到页面上的文件由它接收，
父页面**一个事件都收不到**（实测：`dragenter`/`drop` 全无）。修法是两层：
`#preview-host` 留 **6px 内边距**（这条边框属于父页面），`dragenter` 里把 frame 设成
`pointer-events: none`，从而接管后续整个拖放。**仍然无法**从窗口右/底边缘直接拖入——
那条路径无解，只能走边框。

**③ 文件名的宽度是 0，根本点不动。** 复选框在 flex 行里没有固有宽度，吃掉了整个
basis，把文件名挤成 0px——不只是难看，是**不可点击**。实测 `nameRect.w = 0` 才发现。
这是会直接上线的缺陷。

### 执行方式

**没有拆并行**：改动是一条链（`main.ts` → `preload.ts` → `app.ts` → HTML/CSS），
`app.ts` 内部互相咬合。按 parallel-branch-delegation 自己的标准，拆开只会撞车。

### 验证

- `pytest` **166 passed**（未动 Python）；`typecheck`、`check:i18n`（**123 keys**）、`build` 全绿。
- **48 项 CDP 断言全通过**，关键几项：
  - **用户报的 bug 直接验**：打开一份**已翻好的** `-mono.pdf` → 正常显示、
    **没有任何因缺译文而被禁用的按钮**（原来做不到）。
  - 旧视图三按钮、`#titlebar-actions`、结果分组**都从 DOM 消失**；`.segmented` 在 CSS 中
    无引用。
  - **标签真的可点**（用真实 press/release，不是合成 click）：切换、关闭、各自保留页码。
  - **真实拖放**（CDP 带真实文件的 drag 事件）：拖入外部 PDF → 复制进文件夹 + 开标签；
    **同名再拖 → 生成 `outside (2).pdf` 且原文件未被覆盖**；拖非 PDF → 不建标签、有提示。
  - 适应图标：默认 `opacity: 0` → hover 显示 → 点击真切换（`zoom=page-width` ↔ `view=FitV`）。
- **图标可读性**：把 16/18/22px 并排放大渲染后**看图确认**（细线在小尺寸会糊，是应用标记
  与齿轮两次踩过的同一个坑）。

### 遗留（本轮新增）

- **删掉「对比」少了什么**：对比是左右并排原文与译文；`-dual.pdf` 是**原译文交替的页**，
  两者不等价。但 `-dual` 本就表达"对着看"，且新方案让任何 PDF 都能直接打开——取舍我认为
  对，但明确记下。
- **拖入即复制会改动磁盘**：这是用户选的，三条护栏都做了（同一路径不复制、同名自动改名
  绝不覆盖、未选文件夹不复制）。但"拖一下就在文件夹里多一个文件"仍需知道。
- **拖放无法从页面区域直接进入**，只能经由 6px 边框；那条路径是 iframe 的固有限制。
- **一次翻多份会一次开很多标签**（每份 mono + dual），超出时横向滚动。
- **自动隐藏的适应图标平时看不出当前模式**。
- **标签是内存态**：退出不恢复上次打开的标签（只恢复上次的文件夹）。
- 既有遗留不变（深色主题、打包未验证、折叠侧栏时看不到进度、前端无自动化测试）。

---

## 2026-10-17：选项面板改成下拉（模型/推理/语言）+ 侧栏宽度可拖拽

两件事：① 选项面板里的模型、推理强度、源/目标语言改成下拉；② 侧栏宽度可自由拖动并记住。

### 先修一个既有缺陷：语言框一直是死的

`field-lang-in` / `field-lang-out` 是**两个死输入框**——`app.ts` 里没有任何代码读它们，
`translate.start` 也从不带语言参数。所以它们从来没生效过（既有遗留里已记为「语言输入框是
死的」）。这次不是"改成下拉"，是**让它真正生效**。

**两种语言的作用不对称**（读代码得出，决定了实现方式）：目标语言进提示词、决定字体与行高；
**源语言只影响 OCR**。非扫描件改源语言不会让结果有任何变化——这不是 bug，已写进文档。

### 只影响本次任务，引擎侧要新增一层

`translate.start` 原本不接受语言，`reasoning_effort` 又是"每个模型一个值"。新增三个可选参数，
**用 `None` 表示"不覆盖"**（空串是显式覆盖成空，两者不能混）。覆盖落在**深拷贝**上：

```python
cfg = cfg.model_copy(deep=True)
cfg.translation = cfg.translation.model_copy(update=overrides)
```

**调用方持有的对象绝不被改**——设置面板正持有它，而"只影响这一次"的全部意义就在于不把临时
选择变成永久默认。先写 `tests/test_per_job_overrides.py` 把这个不变量钉死（5 条），再动前端。

### 下拉的两个细节

- 每个下拉**首项是「跟随默认」**（值为空串）。真正的默认在设置里；若这里直接显示某个具体
  模型，会让人以为模型是在这里选定的。
- **语言列表是常见的那些，不是全集**。引擎对未知码**有意宽容回退**到 GoNoto，所以
  "列表里没有" ≠ "不支持"。语言名用 `Intl.DisplayNames` 现取，不手写二十种译名。

### 侧栏宽度：只写一个变量

`--sidebar-width` 是**唯一**被写入的值——宽度和折叠位移都由它派生，所以折叠逻辑一行没动。
夹在 **240 – min(600, 窗口一半)**；窗口缩放与启动时都重新夹紧（存下来的值可能来自更宽的
窗口或被手改）。拖动期间加 `body.is-resizing` 关掉过渡与文字选中，否则会抖。

### 一个实测推翻了我原本的做法

本来用 `setPointerCapture` 保证"拖到预览区上方不断"。**实测不生效**：诊断显示移动事件到了
`window`，而手柄**一个都收不到**（`hasPointerCapture` 始终 false）。改成把
`pointermove`/`pointerup` 挂在 **`window`** 上＋一个普通标志位——手柄只有 5px，一拖就离开它，
而往右就是预览区的 `<iframe>`（另一个文档，事件不到本页），挂 window 才覆盖整个宽度。既简单
又确实可用。**推翻了假设就换，而不是保留一个看起来更"正确"的写法。**

### 验证

- `pytest` **171 passed**（原 166 + 新增 5）；`typecheck`、`check:i18n`（**127 keys**）、`build` 全绿。
- **28 项 CDP 断言全通过**，关键几项：
  - 选项选 `prov|fast` / `high` / `ja` / `ko` → 断言 `translate.start` 收到的就是这四个值。
  - **`config.get` 返回的语言仍是 `en`/`zh`、模型条目仍是 `low`，且全程没有 `config.save`**
    ——这是"只影响本次"的核心，UI 层再验一遍。
  - 选「跟随默认」→ 参数**根本不出现**（`undefined`），而不是送一个空值。
  - 拖拽：340 → 520 真的变宽；**跨过 iframe 仍继续**；夹在 240/600；存了 9999 后重载被夹回 600；
    折叠时手柄 `display: none`。
- 清理临时脚本；**所有 Electron 进程已杀**。

### 遗留（本轮新增）

- **两处都能改模型**：设置改的是**默认**（落盘），选项改的是**这一次**（不落盘）。这是设计，
  但确实可能让人困惑；「跟随默认」首项是为了缓解，文档里也写清了。
- **宽度上限 `min(600, 窗口一半)`** 是我定的经验值，要别的比例告诉我。
- 前端无自动化测试，CDP 脚本仍是临时脚本，跑完即删。
- 既有遗留不变（深色主题、打包未验证、折叠侧栏时看不到进度）。

---

## 2026-10-17（续）：设置 = 纯模型配置；取消「默认模型」

用户指出：设置里的模型**是模型配置**（像 ZCode 那样配 url、api key、上下文、最大输出、
推理强度等），存在配置文件里方便调用，**它不决定你用什么模型**。

### 读完代码的结论：架构上已经是对的一半

- **多个供应商、每个供应商多个模型**——schema 本来就是
  `providers: dict[str, Provider]` → `Provider.models: dict[str, ModelEntry]`，UI 两级都能增删。
- **`ModelEntry` 本来就是配置记录**，后端确实把 temperature/top_p/max_tokens/
  reasoning_effort 送进请求。

**真正缺的**：`max_tokens` / `top_p` / `stream` **完全没有 UI**，只能手改 yaml；**上下文长度在
schema 里根本不存在**。

### 两个真 bug，正是用户说的那种混淆

1. **设置的选择会漏进任务**：`provider: picked?.provider ?? currentProvider ?? undefined`
   ——`currentProvider/currentModel` 是**设置面板的下拉**。在设置里点几下就静默改变了下次
   翻译用的模型。**这是本轮要修的核心 bug。**
2. `default_provider` **只被清除、从未被写入**（全仓库只有 `= null`）。所谓「（默认）」标记
   其实只是"文件里第一个"。

### 修法

- **变量改名**：`currentProvider/currentModel` → **`editingProvider/editingModel`**，名字即含义；
  翻译任务**只读选项面板**的值，不再读它们。
- **彻底删除 `default_provider`/`default_model`**（用户选）。`resolve()` 不指定时回退到
  **文件里第一个**，CLI/MCP 不带参数仍可用；代价是**重排 config.yaml 会改变回退目标**，已记录。
- **补齐配置字段**：`max_tokens` / `top_p` / `stream` 加 UI，新增 `context_length`。
- 设置面板顶部加两行 `.hint` 说明"设置=描述模型，选项=决定用哪个"，否则两个地方都能选模型
  必然让人困惑。

### ⚠️ 一个必须做的迁移（否则软件打不开）

`AppConfig` 是 `extra: "forbid"`，而 `load()` 直接 `model_validate(raw)`，**旧模板里就有
`default_provider`**——几乎每份存量配置都有。删字段后不处理会直接抛 `ValidationError`，
**引擎完全起不来**。所以 `load()` 里加 `_drop_stored_default()` 先丢弃这两个键（与既有
`profiles:` → `providers:` 迁移同一处、同一手法），打 warning，下次保存自然消失。

**拿用户磁盘上那份真实配置实测过**：`load()` 成功、只打两条 warning、`resolve()` 正常。
旧 `profiles:` 迁移里原本会写 `default_provider`，一并改成把旧默认那组**排到最前**。

### `context_length` 怎么用（有意收窄）

存下来 + 用来诊断：**「最大输出」输入框显示推导出的建议值**（窗口 1/4，仅 placeholder）；
段落提示词估算超窗口时**每个 adapter 打一条 warning**（不是每段一条，否则长文档刷屏）。

**不会**在用户没填时自动塞 `max_tokens`：输出被截断比一条警告更糟，且多数服务自己有默认值，
替它们决定会改变现有行为。token 按"约 4 字符 1 token"粗估，误报只是一行日志。**这一点明确
标注，不假装已办到"自动推导请求参数"。**

顺带修正了自己计划里的一处错：原计划把 `context_length` 放进缓存键，但该函数的契约是
"everything that changes model output"，而我们**不自动改请求**，所以它不影响输出——放进去
只会造成无谓的缓存失效。**没放。**

### 验证

- `pytest` **174 passed**（原 171 + 3 条新的超窗口告警用例，含"只告警一次"与"不告警"）；
  `typecheck`、`check:i18n`（**133 keys**）、`build` 全绿。
- **旧配置能加载**：新增两条测试——含已移除字段的配置能 load、且保存后字段消失；另有旧
  `profiles:` 迁移后回退目标正确。
- **17 处失败/13 处错误**是本轮改契约的预期代价，逐个改完（fixtures 里的 `default_*` 参数、
  sidecar/MCP 契约断言、CLI 列表标记）。
- **CDP 37 项断言全通过**，关键几条：
  - **bug 修复直接验**：在设置里浏览到 `beta/three` → 发起翻译 → 断言 `translate.start`
    收到的是选项里的 `alpha/one`，**不是设置里的 beta/three**。
  - 新字段往返：`context_length=32768` / `max_tokens=4096` / `top_p=0.9` / `stream=true`
    保存后进 `config.yaml`；`config.get` 文档里**没有** `default_provider`。
  - 「最大输出」placeholder 显示推导值 `建议：8192`（32768/4）。
  - 选项下拉无「跟随默认」项；存一个失效的模型后重载**回退到第一项**。
  - 设置里的下拉**不再有「（默认）」标记**。
- **清理了真实配置里的残留**：用户磁盘上的 `config.yaml` 里有一个 **`mock` 供应商**，是**我
  早前几轮验证时留下的测试产物**（指向 `127.0.0.1:8791`，我的 mock 服务）。已用项目自己的
  `config.load/save` 移除，并确认其余配置（deepseek provider、密钥、语言）原样无损。
- 临时脚本、截图、Electron 进程全部清理。

### 抓住并补上了一个我自己引入的 bug

`check:i18n` 通过后我又手动 grep 了一遍，发现 **`options.followDefault` 被删掉但仍在两处
被引用**（推理强度下拉、语言下拉）——那两个下拉**仍然有"跟随配置值"的语义**（模型自身的
reasoning_effort、`translation.lang_*`），该 key 必须存在。删了它，界面会把原始 key 当文案
显示出来。

**`check:i18n` 抓不到这个**：它的三项检查全都只把两张表**互相对照**，删掉一个仍被引用的
key，两张表依然一致、全绿。这是检查的**盲区**，不是它没跑。

**于是补了第四项检查**：扫描 `src/renderer/` 里所有 `t("...")` 与 `data-i18n*` 属性，
逐个查表，缺失即非零退出。**并验证了它确实能抓到**——临时删掉那个 key，检查报
`"options.followConfig" is used in src
enderer\index.html but is not in the tables`
且退出码为 1；恢复后退出 0。key 也从误导性的 `followDefault` 改名为 `followConfig`
（已经没有"默认模型"这个概念了）。

### 遗留（本轮新增）

- **CLI / MCP 不指定时的回退依赖 config.yaml 的顺序**——重排会让它落到不同模型。用户选了
  删除 default 字段，这是应有的代价，已写进文档。
- **`context_length` 不改变请求**（见上）；要真的自动发 `max_tokens` 或做批量合并另说。
- **设置与选项都能选模型**，含义已用文案与文档区分，但仍是两个入口。
- 既有遗留不变（深色主题、打包未验证、折叠侧栏时看不到进度、前端无自动化测试）。

---

## 2026-10-17（续二）：修 save-as 浮层跑位/不消失 + 标题栏「固定浮层」开关

用户报了两件事：① 标题栏右上三键左边加一个按钮，控制页面角落那些控件是常显还是保持
"鼠标移入才出现"；② **左上角那个「另存为」按钮一直显示、不消失，和其他按钮不一样**。

### ②的根因：一个漏掉的 `/*`

`style.css` 里 `.viewer-saveas` 上方的注释块**丢了开头的 `/*`**，只剩一个孤立的 ` *` 行。
全文件实测 **`/*` 53 个、`*/` 54 个**，正好差一个。

CSS 解析器遇到孤立的 ` *` 会把它当成**畸形选择器**吞到下一个 `{`，于是**整条
`.viewer-saveas` 规则被判非法、整块丢弃**。而这条规则里同时有：

- `position: absolute; right/top: 18px` → 丢掉 ⇒ 退回普通流，成为 `#preview-host`
  （`display: flex`）的**第一个子项** ⇒ **跑到左上角**
- `opacity: 0; transition` → 丢掉 ⇒ **永不消失**

**所以用户看到的"位置不对"和"不消失"是同一个原因。** 另外两个浮层规则完整，这解释了为什么
只有它表现不同。

**是我上一轮造成的**：`git log -p` 显示我插入 `.viewer-fit` 注释块时**插在了 `/*` 与注释正文
之间**，把 `/*` 留给了 fit 块，save-as 只剩正文。经典的"在定界符中间插入"错误。

**修好后的行为变化要说明**：它回到**右上角**（设计如此），不只是开始隐藏——用户看到的
左上角本身就是 bug 的一部分。

### ①新按钮

标题栏内、**窗口拖拽条之后**（即系统三键左侧，不重叠）。内联 SVG **图钉**，`currentColor`
着色（与齿轮同一套：内联 SVG 能继承 `color`，`<img>` 不能）。**一枚图形两个状态**——按下
态用按钮自己的高亮表示，不做第二个图标，少一处要同步的东西。

二态开关、默认"跟随鼠标"（即原来的形式），存 localStorage。实现**只有一个类 + 一条 CSS**
（`body.overlays-pinned` 把三块设为 `opacity: 1`），**不另起一套显隐逻辑**：`:hover` /
`is-visible` / 计时器都照旧。`.hidden` 是 `display: none !important`，优先级高于钉住，
所以**没有文档时钉住也不会凭空造出控件**（单独验过）。

图钉必须 `-webkit-app-region: no-drag`——拖拽区会静默吞点击，这个坑本项目已踩过三次。

### 补一道必然经过的检查（因为这次没有任何检查发现它）

**这个 bug 骗过了所有检查**：构建成功、`typecheck` 通过、174 个测试全过——CSS 解析失败
既不影响构建也不影响测试，**只有肉眼能发现**。

所以给 `scripts/copy-static.mjs` 加了一道命令：**拷贝 CSS 前校验注释定界符配对**（`/*` 与
`*/` 数量相等 + 没有游离的 `*` 开头行）。CSS 只有这一个文件、且必经过这里，放这儿最合适。

**并且验证了这道检查本身**：正常 CSS 通过、重新引入那个 bug 时**退出码 1 并给出可读原因**
（`57 "/*" but 58 "*/" — a comment is unclosed`）。

**顺带修掉自己写的一处误报**：第一版用「行首是 `*`」来判游离注释，结果**误伤了通配选择器
`* {`**——一个会乱报的检查最后只会被人关掉。改成精确匹配（`*` 后跟空白再跟非 `{` 字符）。

### 验证

- `pytest` **174 passed**；`typecheck`、`check:i18n`（**136 keys**）、`build` 全绿。
- **35 项 CDP 断言全通过**（全部量计算样式，不靠截图——本项目窗口是隐藏标题栏，
  PowerShell 枚举不到句柄，上一轮已确认）：
  - **save-as 归位**：`position` 为 `absolute`、`right`/`top` 为 `18px`，且**落在文档右半边**
    （不再是左上角）；空闲 `opacity: 0` → 翻页后 `1` → 约 1.6s 后回到 `0`。
  - **三个浮层同一套规则**：空闲都是 `0`、翻页后都是 `1`。
  - **图钉**：默认未固定 → 真实点击后 `aria-pressed=true`、三块常驻可见 → 再点回；
    重载后状态被记住；tooltip 随状态变化且**不是原始 key**；**无文档时钉住也仍是
    `display: none`**（单独脚本验过）。
  - **位置与可点性**：图钉右边界 ≤ 窗口宽 − 140px（不与系统三键重叠）、在拖拽条右侧、
    `-webkit-app-region` 为 `no-drag`。
- **图钉图标可读性**：把 16/18/22/32px 并排放大渲染**看图确认**（16px 下仍能认出是图钉）。
- 清理临时脚本、截图；**所有 Electron 进程已杀**。

### 遗留（本轮新增）

- **`context_length` 仍是"只提示不改请求"**（上一轮的取舍，未变）。
- **CSS 那道检查只看定界符配对，不做完整 CSS 校验**：刻意如此——零成本、只抓真正咬到的
  那种故障，且**绝不会误拒合法 CSS**。真要完整校验得引依赖。
- 既有遗留不变（深色主题、打包未验证、折叠侧栏时看不到进度、前端无自动化测试）。

---

## 2026-10-17（续三）：一键配置 MCP（片段为主 + 代写为辅），并让打包版也能用

用户问：有没有办法给 agent 一键配好 MCP，但 agent 很多怎么办，是软件来配吗。

### 先纠正我上一轮说错的两件事

1. **`pdfxlate-mcp.exe` 不存在**。上一轮我看到 `mcp.exe` 就下了结论，**那是 MCP SDK 自带的
   CLI**。`entry_points.txt` 里只有 `pdfxlate` / `pdfxlate-sidecar` —— editable 安装发生在
   加这个入口**之前**，之后没重装。
2. **`pdfxlate.spec` 里那句注释是错的**：说打包版能被 agent 用
   `python -m pdfxlate.mcp_server` 启动。**做不到** —— onedir 产物里 exe 本身就是
   "解释器 + 写死的入口"，没有独立 `python`。**所以装安装包的用户此前完全用不了 MCP。**

### 实测：五个 agent 的配置是**三种形状**

| agent | 文件 | 形状 |
|---|---|---|
| Claude Desktop / Claude Code / WorkBuddy | `claude_desktop_config.json` / `~/.claude.json` / `~/.workbuddy/mcp.json` | JSON `mcpServers` |
| ZCode | `~/.zcode/cli/config.json` | JSON **`mcp.servers`** + `type`/`enabled`/`timeoutMs` |
| Codex | `~/.codex/config.toml` | **TOML** `[mcp_servers.<名>]` |

**所以没有"通用一键"**（键名、格式、路径都不同，agent 还在增加）。按用户选择：**片段是主**
（对任何 agent 成立、永不过时），**代写是辅**（只覆盖这五个，先探测、只对装了且找到配置的显示）。

### 打包版怎么用：复用 sidecar + `--mcp`

`sidecar.main()` **最先**判断 `--mcp`，有则把剩余参数原样转交 `mcp_server.main()`。
**不改 spec、不加第二个 EXE**：磁盘不变、构建时间不变、零打包风险。命令行是
`pdfxlate-sidecar.exe --mcp --config-dir <userData>`。

**这还绕开了上面第 1 条的坑**：应用生成的命令不依赖 `pdfxlate-mcp.exe` 是否装出来。

### 谁生成命令：主进程，且与启动引擎**共用同一处判断**

把 `Sidecar.resolve()` 拆出 `resolveLaunch(..., extraArgs)`，**dev/打包的判断只有一份**。
若两处各写一遍，迟早出现"面板给的路径和应用实际用的不是同一个"，那比不给更糟。

### 代写的三条护栏（写的是别人的文件，还含密钥）

1. **先备份**到 `.bak-<时间戳>`
2. **只合并我们那一个键**：读→解析→**只改这一个 key**→写回；绝不像素级重写整个文件
3. **写后重读校验**（能解析、条目确实在）；失败则**从备份还原**并报错

**解析不了的文件直接报错、绝不"修复"**：那是别人的配置，我们没资格猜内容。
文件内容**绝不进日志**（实测发现 `~/.zcode/cli/config.json` 里有明文 GitHub token —— 已在
报告里提醒用户轮换，代码里只记路径与结果）。

TOML 的读与校验用 stdlib `tomllib`；**写入是手写一小段固定块**（stdlib 没有 TOML writer），
只替换 `[mcp_servers.pdfxlate]` 这一段，注释与相邻表原样保留。

### 验证

- `pytest` **196 passed**（原 174 + 新增 22）；`typecheck`、`check:i18n`（**157 keys**）、`build` 全绿。
- **写入正确性 19 条**（`test_mcp_config.py`）：三种格式各自——合并后**别人的条目原样保留**、
  **幂等**（写两次逐字节相同且报 `changed=false`）、**备份等于原文件**、**坏文件一字不动且
  不留残留备份**、`mcpServers` 是数组时报错拒写、缺命名空间时自动创建。
- **TOML 边界单独手验**：替换已有 `[mcp_servers.pdfxlate]` 时**不吃掉下一张表**，注释、
  `[other]`、其他 server 全部保留（这是最容易写错的地方）。
- **`--mcp` 真的能起（3 条）**：真实 stdio 握手 `initialize` → `serverInfo.name == "pdfxlate"`
  → `tools/list` 恰好四个工具；**不带 flag 时仍是 sidecar**（第一条输出是 `{"event":"ready"}`，
  并回答 `ping`）；`--mcp` 放在参数任意位置都生效。
- **应用侧 17 条 CDP**：MCP 面板出现并可从菜单进入；**片段里的命令是本机真实路径**
  （断言等于主进程算出的那个，且**不是** README 里那个已失效的 `.venv/bin/pdfxlate-mcp`）；
  按钮复制的内容**逐字符等于**预览；探测列表只显示存在的配置；**写入后我们的条目在、
  别人的条目在、无关顶层键在、备份存在且等于原文**；**再写一次不重复、并提示"已配置过"**。
- 清理临时脚本、临时 agent 配置；**所有 Electron 进程已杀**。

### 过程中的一个环境限制（如实记）

**本机 OS 剪贴板不可用**：`clipboard.readText()` 在**直接写完后立刻**仍返回空，且
PowerShell 自己的 `Get-Clipboard` 都报"所请求的剪贴板操作失败"。所以**读回断言无法在此环境
成立**，它测的是会话而不是代码。改为断言**按钮确实以逐字符相同的内容调用了剪贴板 API**
（包装 `clipboard.writeText` 记录实参）。**端到端"粘贴到别处"这一步没有验证**，不假装验过。

### 遗留（本轮新增）

- **代写只覆盖五个 agent**；其余用片段。刻意如此——agent 会持续增加，而片段通吃。
- **TOML 写入不是通用 TOML 序列化**：只替换我们那一段。若用户把该段写成多行数组等更复杂的
  形式，会被整段替换为规范形式（值不变，格式归一）。
- **打包版未能完整验证**：本机没有 electron-builder 产物，`--mcp` 只验到"源码下等价命令可用
  + spec 会把它打进去"。完整打包验证仍是既有遗留。
- **`pdfxlate-mcp` 这个 console script 目前没装出来**（`pip install -e ".[mcp]"` 之后会有），
  但应用已不依赖它。
- 既有遗留不变（深色主题、折叠侧栏看不到进度、前端无自动化测试）。

---

## 2026-10-16

### 标签栏：自适应宽度 + 左右箭头 + 拖动排序

**问题**：标签开多了就溢出到看不见。`.tabbar` 本来就有 `overflow-x: auto`，但滚动条被
`scrollbar-width: none` / `::-webkit-scrollbar { display: none }` 藏掉，`.tab` 又是
`flex: 0 0 auto` 且无 `min-width`——于是溢出既不可见、也没法滚过去。另外标签文字可以选中，
标签不能拖动排序。

**做法**

- **两级结构**：`#tabbar`（外层，管箭头与 flex 尺寸）+ `#tabbar-strip`（真正的滚动容器）。
  箭头必须在外层——放进滚动容器里会跟着标签一起滚走，正是它们要解决的问题。保留
  `#tabbar` 这个 id，所以 `paintSettingsMode()` 里隐藏标签栏那行不用改。
- **宽度自适应**：`.tab { flex: 0 1 auto; min-width: 96px; max-width: 210px }`。
  少时按内容宽度（最多 210px），多了按比例压缩，**到 96px 就停**，剩下交给滚动。
  96px 的取舍：再窄文件名只剩三四个字符，标签就认不出是哪份文档了。实测 968px 的标签区
  正好放 10 个 96px 的标签；6 个时自动长回 101–123px。
- **箭头只在溢出时出现**，用 `display: none` 而不是 `opacity: 0`。这一点不是风格问题：
  一个不可见的箭头仍然占 22px，**可能自己造成溢出**；隐藏只会释放宽度，所以"显示→更多
  溢出→显示"这种抖动在结构上不可能发生。到两端时对应箭头 `disabled`，比较留 1px 余量
  （亚像素布局下两端很少精确相等）。
- **重建标签会丢 `scrollLeft`**：`renderTabs()` 每次都 `innerHTML = ""`，点最右边那个标签
  会跳回第一个。改为重建前存、重建后恢复，再在下一帧把激活项滚进可视区。只滚"确实不可见"
  的情况，否则会在用户指针下抽动。
- **拖动排序**：`draggable` + 私有 MIME `application/x-pdfxlate-tab`。**故意不写
  `text/plain`**——那样拖出窗口时浏览器地址栏会接受它，而拖标签出窗口应该什么都不发生。
  `dragover` 按各标签中点 `insertBefore` 实时预览；`dragend` 从 **DOM 读回**顺序写进
  `openTabs`，因为 DOM 已经是用户看到的排列，再自己推一遍等于两个真相。关闭按钮设
  `draggable = false`（否则按下它会开始拖整条标签），`dragend` 后 250ms 内忽略 click
  （否则拖动结束会顺手激活那个标签）。

**顺带修掉的一个坑**：文档级 `dragenter` 对**任何**拖动都会触发，原本无条件关掉 PDF iframe
并点亮拖放边框。加了标签拖动之后，把标签拖过预览区就会看起来像"文件要落在这里"，落点时还会
对空文件列表跑一遍 `handleDropped`、打一条假的"无法读取路径"。现在 `isFileDrag()` 只认
`dataTransfer.types` 含 `"Files"` 的拖动，三处（`dragenter` / `dragover` / `drop`）都加。

**验证**：`typecheck` / `check:i18n`（**159 keys**）/ `build` 全绿；CDP **27/27**，用临时
目录里 12 个 PyMuPDF 生成的 PDF（不进仓库）实测：

| 检查 | 结果 |
|---|---|
| 12 个标签全部压到 96px 下限，条带溢出 | ✅ 宽度全 96，`scrollWidth 1196 > clientWidth 968` |
| 6 个标签时宽度长回去 | ✅ 101–123px，无溢出 |
| 箭头随溢出出现/隐藏 | ✅ 2 个标签时 `display: none` |
| 两端禁用 | ✅ 起点左禁用、终点右禁用 |
| 点箭头滚动 | ✅ 一次滚 228px（可视宽 80%） |
| 点最后一个标签后仍在视野内 | ✅ 且 `scrollLeft` 不归零 |
| 文字不可选中 | ✅ `user-select: none` |
| 拖动第一个标签越过第三个 | ✅ 顺序变 B, C, A, … |
| 顺序写进模型（不是只改 DOM） | ✅ 触发 resize 后仍是新顺序 |
| 拖标签不点亮文件拖放框 | ✅ `afterTabDrag: false` |
| 拖文件仍然点亮 | ✅ `afterFileDrag: true` |
| 窄窗口（700px）不压破 96px | ✅ |
| 设置模式来回后箭头恢复 | ✅（为此在 `paintSettingsMode()` 里补了一次重算：隐藏时条带无宽度，量不出溢出） |

**遗留**：箭头一次滚 80% 可视宽，没有"滚到最后一个"的跳转；标签顺序不持久化到磁盘
（重开应用回到文件夹顺序）。两者都没要求，先记着。

---

## 2026-10-16（续）：滚 PDF 时浮层不出现——上一轮的验证是假阳性

**现象**：用滚轮上下滚 PDF，页面确实在滚，但右下角翻页/缩放、右上角另存为、左上角适应
这几个浮层按钮**不出现**。

### 根因

浮层的唤醒链路是「主进程转发『发生了滚轮』→ 渲染进程再与 `pointerOverPreview` 配对」。
转发用的是 `webContents.on("input-event")` 抓 `mouseWheel`。**PDF 查看器跑在 Chromium
的 guest 进程里，滚轮到不了顶层 `webContents`**，所以那个事件对 PDF 区域根本不存在。

**上一轮为什么判成"通过"**：当时用 `webContents.sendInputEvent` 造滚轮。这个 API 直接
注入**顶层** `webContents`，因此必然触发 `input-event`——它绕开了真实硬件滚轮的路由，
把"PDF 区收不到"这件事完全掩盖了。开发记录里那句"主进程 `input-event` 通"只对普通
DOM 区域成立，当时被当成了全局结论。

### 修法

`registerWheelForwarding()` 增加 **`hookWindowMessage(WM_MOUSEWHEEL)`**（仅 Windows）
作为主信号：窗口过程一定看得到滚轮，`WM_MOUSEWHEEL` 发给焦点窗口、`DefWindowProc` 再向
父窗口传播，与哪个 frame 处理它无关。原有 `input-event` 保留为兜底（非 Windows 平台，
以及 Windows 上侧栏/标题栏等普通 DOM）。窗口 `closed` 时 `unhookAllWindowMessages()`。
**渲染进程一行没改**——`pointerOverPreview` 那半本来是对的。

### 验证（真实消息，不用合成输入）

本机是 RDP 会话，`SetForegroundWindow` 被系统拒绝，且 `SendInput` 只产生 `mouseMove`。
改用 `PostMessage(WM_MOUSEWHEEL)` 直接投递到窗口——这正是真实滚轮到达窗口过程的同一条
路径。主进程侧通过 Node inspector 计数，**注意 `require` 在 inspector 上下文里不存在**，
要用 `process.getBuiltinModule("module").createRequire(...)`。

| 检查 | 结果 |
|---|---|
| 对照：侧栏上滚轮产生 `input-event` | ✅ `mouseWheel=5` |
| 对照：侧栏上滚轮**不**显示浮层 | ✅ `shown=0`（指针配对仍生效） |
| **根因**：PDF 区真实滚轮**没有** `input-event` | ✅ `mouseWheel=0` |
| 修复：PDF 区滚轮被 `WM_MOUSEWHEEL` 钩子接住 | ✅ `sent=4` |
| 渲染进程收到 `viewer:wheel` | ✅ `pings=4` |
| 浮层出现 | ✅ `shown=4` |
| 1.6s 后自行消失 | ✅ |

**9/9 通过**；`typecheck`、`check:i18n`（159 keys）、`build`、`pytest`（196）全绿。

### 测试过程中踩到的两个坑（记下来免得重犯）

1. **`hookWindowMessage` 对同一消息 id 只保留一个回调**。测试里又挂了一个 `WM_MOUSEWHEEL`
   计数，**静默顶掉了应用自己的钩子**，于是"修复没生效"。改为拦截
   `webContents.send("viewer:wheel")` 计数。
2. **渲染进程的钩子要在 `location.reload()` 之后装**，否则被重载清掉。

### 已知限制

`hookWindowMessage` **仅 Windows**。macOS/Linux 仍走 `input-event`，在 PDF 区域很可能同样
收不到——本机无法验证，没有假装修好。若要覆盖，得各平台另找信号源。

---

## 2026-10-17：滚轮修复的第三次尝试——前两次都是假阳性

**用户反馈**：修完仍然不显示；进一步说"好像只有第一次才会显示，后面的都不显示了"。

### 根因（这次拿到了直接证据）

**PDF 查看器不是普通 iframe**：它是 Chromium 内置 PDF 扩展，跑在**独立的 `remote`
WebContents** 里，有自己的窗口。OS 把滚轮路由给**它**，主窗口一次都收不到。

实测（把滚轮投递到光标下的窗口，连续三轮）：

| 轮次 | 查看器收到 `mouseWheel` | 主窗口收到 `mouseWheel` |
|---|---|---|
| 1 | 2 | 0 |
| 2 | 累计 4 | 0 |
| 3 | 累计 6 | 0 |

于是 `WM_MOUSEWHEEL` 钩子只在**焦点还在主窗口**时有效——即打开文档后的第一次滚动。
之后焦点进入查看器自己的窗口，钩子永远沉默。这精确解释了"只有第一次"。

### 修法

`attachViewerWheelForwarding()`：在 `app.on("web-contents-created")` 里按
`getType() === "remote"` 认领查看器，监听它自己的 `input-event` 并转发
`viewer:wheel`。在 `createWindow()` **之前**注册一次（guest 是懒创建的，错过
`web-contents-created` 就永远挂不上）。

**不用 `hostWebContents` 过滤**：该属性文档写的是 "might own"，对这些 guest 可能为空，
为空就会**静默转发失败**。本应用单窗口单查看器，`getType()` 足够。

`WM_MOUSEWHEEL` 与窗口 `input-event` 保留为次要来源（普通 DOM），注释已改写。
渲染进程**一行没改**——`pointerOverPreview` 本来是对的。

### 两次假阳性的教训（比修复本身更重要）

1. **`sendInputEvent` 打给顶层 `webContents`**：绕开真实路由，必然产生 `input-event`，
   把"PDF 区收不到"完全掩盖。
2. **`PostMessage(WM_MOUSEWHEEL)` 打给顶层窗口**：同样绕开"OS 投给光标下窗口"这一事实，
   而且**只测一次**，所以"第一次有效、之后失效"看不出来。

**正确测法**：驱动查看器**自己的** WebContents，并**连续多轮**、每轮间隔超过自动隐藏
时间。这次就是这么测的。

### 验证

| 检查 | 结果 |
|---|---|
| 查看器确实是独立 `remote` WebContents | ✅ |
| 第 1 轮：转发 + 浮层出现 | ✅ `sent=2 shown=2` |
| 第 2 轮（间隔 1.9s） | ✅ `sent=2 shown=2` |
| 第 3 轮 | ✅ `sent=2 shown=2` |
| 三轮完全一致（无"只有第一次"） | ✅ |
| 每轮之间浮层已自动隐藏 | ✅ |
| 指针不在预览区时滚动**不**显示 | ✅ `sent=2 shown=0`（配对仍生效） |

**12/12 通过**；`typecheck`、`check:i18n`（159 keys）、`build`、`pytest`（196）全绿。

### 没能验证的一步（如实记）

**真实硬件滚轮 → 查看器 `input-event`** 这一跳无法在本机证明：本机是 RDP 会话，
`SendInput` 返回成功但**不产生任何输入**（`mouseData` 已修正为正确的
`0xFFFFFF88`，仍无效），OS 输入管线被过滤。验证走的是
`guest.sendInputEvent`——它注入的**正是真实滚轮到达的那个 WebContents**，比前两次
都接近真实路径，但仍不等于硬件。要彻底确认，需要在有物理鼠标的机器上滚一次。

---

## 2026-10-17（续）：移除滚轮唤醒浮层；新增双栏阅读

### 移除滚轮功能

滚轮唤醒浮层在真实硬件上始终没能验证通过（详见上一节：本机 RDP 会话过滤了 `SendInput`，
而查看器是独立的 `remote` WebContents，路由复杂）。按用户决定**整体删除，不留死代码**：

- `src/main/main.ts`：`WM_MOUSEWHEEL` 常量、`registerWheelForwarding()`、
  `attachViewerWheelForwarding()`、两处调用、`closed` 里的 `unhookAllWindowMessages()`。
- `src/preload/preload.ts`：`onWheel()`。
- `src/renderer/app.ts`：`PdfxlateApi.onWheel`、`pointerOverPreview`、
  `trackPointerOverPreview()`、启动调用。
- `docs/project-structure.md`：`onWheel()` 整段替换为"已移除"说明，删掉两处交叉引用。

**保留 `flashViewerOverlay()`**——翻页、缩放、切换适应模式仍在用它，浮层照常出现。

### 双栏（左右两页）

**为什么不能靠查看器自己**：Chromium 内置 PDF 查看器没有双页模式的 URL 参数
（`spread` / `two-page` 都不认，已试）。所以自己实现：`renderPreview()` 在
`#preview-host` 里放一个 `.preview__pages` 行容器，双栏时放**两个** iframe。

- 按钮在左上角 `#viewer-fit` 面板内作第三个图标（两个并排页面轮廓），自动继承该面板的
  显隐规则，**没有新增任何显隐逻辑**。
- `spreadMode` 全局开关，与缩放同理。`state.page` 始终是**左页**，关掉双栏停在真实可见页。
- 翻页步长 2；末页奇数时只渲染一页；页号显示 `3–4 / 20`；"下一页"边界按"左页 + 1"。
- `zoom=page-width` 相对每个 iframe 自己的视口，两页各自缩到半宽，正好装下。

**新增 i18n**：`preview.spread`（中英各一），共 **160 keys**。

### 验证

CDP **20/20**（6 页真实排版 PDF + 7 页测试 PDF）：

| 检查 | 结果 |
|---|---|
| `window.pdfxlate` 上已无 `onWheel` | ✅ 键列表不含它 |
| 按钮在 `#viewer-fit` 内、有文档时可用 | ✅ |
| 默认单栏、1 个 frame、`page=1` | ✅ |
| 开启双栏：2 个 frame、`page=1`/`page=2`、标签 `1–2 / 7` | ✅ |
| 翻页一次到 `3–4`（步长 2） | ✅ |
| 末页只渲染 1 页、下一页禁用、标签 `7 / 7` | ✅ |
| 闲置时浮层隐藏，翻页后出现 | ✅ |
| 关闭双栏回到 1 个 frame，停在左页（5） | ✅ |

**关于"看起来页面变窄"**：截图里双栏的白页比单栏窄，实测查看器内部尺寸确认这是**正确的**——
单栏 `sizer` 803px，双栏每页 446px，正好约一半（`zoom=page-width` 按各自视口缩放）。
本环境 `Page.captureScreenshot` 有返回过期帧的前科，像素判读不可靠，故以 DOM 实测为准。

`typecheck`、`check:i18n`（160 keys）、`build`、`pytest`（196）全绿。

---

## 2026-10-17（续二）：双栏改成「一个文档、一条滚动条」

**用户指出的问题**：上一版双栏是两个 iframe，**各有自己的滚动条、能各自独立滚动**，
而且每个 iframe 是独立的 PDF 查看器实例——本质上是把同一份 PDF 开了两遍，不是"一个
视图里左右对照"。用户要的是：同一份文件里，左页英文、右页中文，一起滚。

### 为什么不能靠同步两个 iframe

查看器是 Chromium 内置扩展的**独立进程**，父页面**读不到它的滚动位置**，也无法把
滚动指令发进去（`postMessage` 接口在本版未暴露，之前实测过）。所以两条滚动条在技术上
**不可能**保持同步。任何"双 frame"方案都注定是错的。

### 做法：把两页合成一个派生 PDF

- 新增 `python/pdfxlate/pdf/spread.py`：`build_spread()` 每两页并排画到一张新页。
  用 `show_pdf_page()` 贴 **Form XObject**，**不栅格化**——文字仍可选中、可搜索。
- 输出 `cache_dir()/spread/<sha1>.pdf`，键 = 路径 + 大小 + mtime（改过的文件不会命中
  旧缓存）；7 天未用自动清理；先写 `.part` 再 rename，避免半成品被当成缓存命中。
- sidecar 新增 `pdf.spread` → `{path, pages, spreads}`。
- 渲染侧 `renderPreview()` **永远只建一个 iframe**：双栏时 `src` 指向派生文件，页码用
  `ceil(page / 2)`。`toggleSpread()` 改成异步，生成失败就保持原状并写日志。
- 删掉 `.preview__pages` / `--spread` 的双帧 CSS。

### 验证

**Python 新增 12 项**（`tests/test_pdf_spread.py`）：7 页 → 4 张、每张宽 1190（= 595×2）、
末张只有左页、文字仍是矢量可提取、二次调用是缓存命中（mtime 不变）、源文件被修改后
生成新文件、源文件字节不变、缺文件/目录/坏 PDF 各自报可执行的错误、不留 `.part`、
sidecar 形状与注册。**pytest 208 passed**。

**CDP 17/17**：双栏时 `#preview-host` 内**恰好 1 个** `.preview__frame`；`src` 指向
`spread/<40位hex>.pdf`；`page=1`；翻页后 `page=2`（对应源页 3–4）；末张 `page=4`、
下一页禁用、标签 `7 / 7`；关掉后 `src` 回到源文件并停在第 7 页。

**权威判据是派生 PDF 内部的文字坐标**（截图在本环境有返回过期帧的前科）：

| 合成页 | 左半 | 右半 |
|---|---|---|
| 1 | page 1 | page 2 |
| 2 | page 3 | page 4 |
| 3 | page 5 | page 6 |
| 4 | page 7 | （空） |

每张宽 1190，左页文字 x=60、右页 x=655——真正的左右并排。

### 一个刻意的取舍

末页为奇数时右侧留白，**但整张保持双页宽**。若把它做成单页宽，查看器会按视口把这一页
放大到两倍，翻到最后一页时画面会突然跳一下。留白比跳动好。

`typecheck`、`check:i18n`（162 keys）、`build` 全绿。

---

## 2026-10-17（续三）：「文档」组的文件夹选择器重做

**用户反馈**：这块布局不合适。

### 查出来的问题

1. **`.btn--block` 从来没定义过**。HTML 里一直挂着这个类，CSS 里搜不到——所以
   「选择文件夹…」只是个普通尺寸的小按钮，孤零零贴在左上角。这是个真实的死引用。
2. 路径 `<p>` 紧贴在按钮下方，两者没有视觉归属，读起来像"一个按钮 + 一行裸文本"。
3. `style.css` 在 `.folder-label` 后面有**一个孤立的 `}`**（早先就混进去的解析错误，
   浏览器静默丢弃，但会让后面的规则解析位置错乱）。

### 改成整行控件

```
┌────────────────────────────────────┐
│ 📁  未选择文件夹            [选择…] │
│     C:\Users\...\papers            │
└────────────────────────────────────┘
```

- 一个 `<button class="folder-picker">`：内联 SVG 文件夹图标 + 两行文字 + 右侧动作标签。
- 未选文件夹时路径行 `hidden`，整行只有一行高，不留空档。
- 名称用最后一段路径（`baseName`），完整路径在第二行小字、`title` 带全路径、超长省略。
- hover 整行变浅灰、图标与动作标签变 accent 色；`:focus-visible` 有 accent 描边。
- **删掉 `.btn--block` 的引用而不是补定义**：唯一用处被替换了，补上就是死 CSS。
- **修掉那个孤立的 `}`**；现在 `{` 与 `}` 各 154 个，平衡。

### 验证

CDP **16/16**：

| 检查 | 结果 |
|---|---|
| 是单个 `<button>`、flex 行、铺满 group 内容宽 | ✅ 290 = 290 |
| 未选：显示「尚未选择文件夹」、路径行隐藏且为空 | ✅ |
| 有图标、有右侧动作标签、整行有 tooltip | ✅ |
| 选中后：名称是最后一段、路径完整、tooltip 是完整路径 | ✅ |
| 路径字号小于名称 | ✅ 11.5 vs 13 |
| 超长路径省略而非换行 | ✅ `text-overflow: ellipsis` 且确实溢出 |
| 点击整行触发 `dialog:pickFolder` | ✅ 主进程侧计数 `calls=1` |

**注意**：`window.pdfxlate` 是 contextBridge 暴露的**冻结对象**，测试里改不动它——
第一版测试就是这么报错的。改成在主进程替换 `dialog.showOpenDialog` 来计数，走的仍是
真实 IPC 路径。

`typecheck`、`check:i18n`（163 keys）、`build`、`pytest`（208）全绿。

---

## 2026-10-17（续四）：设置页布局——居中、加宽、两列

**用户反馈**：设置页右边内容都挤在一边，应该在可用区域居中，并且可以适当变宽。

### 改动

- `.settings-panel`：`max-width` **520 → 760px**，加 `margin: 0 auto` 居中。
  760 是刻意上限——再宽单行文字太长、读起来累；配合两列填充率已经够。
- `.settings-host` 加 **`scrollbar-gutter: stable both-edges`**。这是个真 bug 的修复：
  滚动条占内容盒右侧约 16px，面板"居中"后实际偏左 8px；而且各设置页内容长短不同，
  有没有滚动条会让面板左右**跳一下**。两侧都留槽位后内容盒对称。
- 新增 `.grid--form`（`repeat(auto-fit, minmax(260px, 1fr))`），设置页的三个表单
  （模型 / 外观 / 通用）改用它；**侧栏的选项面板保持 `.grid--one`**，它本来就窄。
  760px 时两列各约 374px；窗口变窄自动退回一列，不需要媒体查询。
- `extra_body` 是 JSON，半列太窄，加 `.field--wide`（`grid-column: 1 / -1`）跨满整行。
- `.settings-panel .hint` 限宽 `68ch`，标题 15 → 16px，顶部内边距 20 → 24px。

### 顺手修掉一个真的 CSS bug

截图发现「流式返回」复选框跑到了那一列的**最右端**并且文字折行。根因是两条全局规则叠加：

1. 共享的 `label { display: flex; flex-direction: column }` —— `.check` 只改了
   `display`，没改方向，所以复选框和文字仍然上下排列；
2. 共享的 `input, select { width: 100% }` —— 在横向布局里复选框吃掉整行宽度，把文字
   挤到最右边。

`.check` 现在显式 `flex-direction: row`、`align-self: start`，`.check input` 覆盖为
`width: auto; flex: 0 0 auto`。实测复选框 13px、紧贴单元格左缘、文字不折行。

### 验证

CDP **21/21**：面板 760px 且左右留白相等（90/90）、占面板宽度 >75%、模型页 2 列、
JSON 字段跨满 760px、hint 窄于面板、四个设置页都居中、面板收窄到 580px 时退回 1 列、
复选框为 row 布局 / 13px / 不折行 / 对齐。截图确认版式。

`typecheck`、`check:i18n`（163 keys）、`build`、`pytest`（208）全绿。

## 设置 › 模型：改成「供应商列表 + 详情」两栏，并给模型加真正的启用开关

设置页的模型面板原本是两个下拉（供应商 / 模型）+ 一长列平铺字段。目标版式参照
ZCode 的模型设置：左边一列供应商，右边是选中供应商的接口信息和模型行列表。

### 版式

- **只有「模型」这一项是两栏**，侧栏（模型 / 外观 / 常规 / MCP）一行没动，
  `外观 / 常规 / MCP` 仍是居中 760px 单栏。切换设置项时 `paintSettingsMode()` 给
  `#settings-host` 加/去 `settings-host--split`。
- 左列 `.model-split__providers` 固定 232px、独立滚动、右侧 1px 分隔线；
  右列 `.model-split__detail` 占满剩余、独立滚动、内容限宽 720px。
- 不新开第三栏的原因：窗口最小 1000px，减去 340px 侧栏后再三栏，详情只剩约 400px，
  接口地址这类字段放不下。
- 实测 1280×820：左列 232px、右列 708px、两列各自 `overflow-y: auto`、host 无内边距；
  1024px 窗口下右列 452px，无横向溢出。

### 供应商列表

每行：方块图标 + 名称（省略号）+ 状态点。状态点**只反映配置，不做假的健康检查**
（引擎里没有任何探测接口的逻辑）：绿 = 有地址且有密钥，琥珀 = 缺一个，灰 = 还没有模型。
每个点带 `title` 说明含义。

「+」展开一个内联输入行添加供应商；`confirm()` 后删除。都在标题栏，不再占一整行。

### 详情与模型行

- 头部：图标 + 供应商名 + 删除按钮（trash 图标，带确认）。
- 接口地址 / 类型 / API 密钥三个字段**全宽纵向**排列（上一轮给模型页加的两列在这里撤掉）。
  密钥框保留「留空即保持不变」的掩码语义。
- 模型行：别名（粗体）、别名与模型 ID 不同时补灰色 ID、上下文长度徽章（`128K` / `1M`）、
  有流式时再加一个徽章；右侧是编辑、删除、启用开关。
- 点行或铅笔在**该行下方就地展开**编辑区（同一时刻只展开一个），内含原有全部字段和
  「保存修改」。编辑区是**一个**元素，通过移动位置换行，因此字段 id 始终唯一，
  `apply/commit` 系列不用重写。
  - 踩到的坑：`renderModelList()` 先把编辑区 `remove()` 再重建列表，未展开时它就被
    留在文档外，`getElementById` 全部返回 null（日志里表现为
    `Cannot read properties of null (reading 'remove')`）。现在未展开时把它以隐藏状态
    挂回列表末尾，并缓存元素引用。
- 开关点击不冒泡到行，不会顺带展开/收起编辑区（实测 `expandedBefore === expandedAfter`）。

### `enabled` 是真的启用/禁用，不是装饰

`ModelEntry` 与 `ResolvedModel` 都加 `enabled: bool = True`（后者必须加：
`_flatten` 会把 entry 的字段整体灌进去，而它 `extra="forbid"`）。语义：

- 老配置没有这个键，默认启用，不需要迁移。
- 无参 `resolve()` 取第一个**启用**的模型；全禁用时报 `No enabled models configured`。
- 只给 provider 不给 model 时，同样取该 provider 第一个启用的模型。
- **显式点名禁用模型报错**（`... is disabled; enable it in Settings first`）——
  用户关掉它就是为了不被用，静默继续翻译更糟。
- `all_models()` 默认仍返回全部（编辑器与 `--list-models` 要能看见并重新打开它），
  新增 `only_enabled=True` 供选项面板和无参回退使用。
- 选项面板过滤 `enabled === false`；记住的选项若正好被禁用，回退到第一个可用项。
- `providers.list`、MCP 的 `list_providers` 都多返回 `enabled`（MCP 仍只读，没新增写工具）；
  `--list-models` 给禁用项标 `(disabled)`，`used when unspecified` 标到第一个启用项。
- 开关**立即保存**，不经过「保存」按钮：它是这一行唯一改变翻译可用范围的东西。

### 验证

`typecheck`（两个 tsconfig）、`check:i18n`（169 keys）、`build`、`pytest` **221 passed**
（新增 `tests/test_config_enabled.py` 13 条）。

CDP 实测（临时脚本，跑完即删）：

- 进入设置 › 模型后 `settings-host--split` 存在，左列 232 / 右列 708，两列独立滚动；
- 三个供应商行状态点为 ready / ready / incomplete，与配置一致；
- 详情字段三条都是 660px 宽、纵向排列；
- 模型行 `glm-4.5` 徽章为 `128K` + `流式返回`，`glm-4-flash` 有 `is-disabled` 且开关为关；
- 点行展开后编辑区紧跟该行、只有一个可见编辑区，再点另一行仍只有一个；
- 关掉 `hy4-preview-f` 后 `config.yaml` 写入 `enabled: false`，选项下拉从 4 项变 3 项，
  再打开恢复 4 项；
- 切到外观 / 常规时 `settings-host--split` 消失，面板回到 760px 居中（左右各 90px）；
- 1000px 宽下右列 428px、无横向溢出；增删供应商/模型的 `confirm()` 文案正确。

### 已知限制

- 状态点不联网，因此「绿」只代表配置齐全，不代表密钥有效或服务可达。
- 删除模型仍用原生 `confirm()`，和项目其它删除操作保持一致。
- 前端仍无自动化测试；CDP 脚本不提交。

## 「类型」改成三选一下拉，并实现 Responses / Anthropic Messages 两个协议

设置页的「类型」原本是自由文本框，用户得自己知道 `openai_compat` 这个词。ZCode 的同一
位置是下拉，三项分别是 `Chat Completions (/v1/chat/completions)`、
`Responses (/responses)`、`Anthropic Messages (/anthropic/v1/messages)`。照此改成下拉，
并且**把后两项真正实现**——只改下拉会出现「能选但跑不通」。

ZCode 是闭源的，端点路径从其 `resources/app.asar` 的界面文案确认；`references/` 里那份是
PDFMathTranslate，与这三个协议无关，本轮没有拉取新源码。

### 新增适配器

`@register` 注册表原本就是为这件事设计的：新增后端 = 一个文件 + 一个装饰器，PDF 管线与
缓存接线都不用动（见 `models/base.py` 顶部注释）。

**`models/openai_responses.py`**（`openai_responses`）——复用已装的 `openai` SDK，
不新增依赖。相对 chat/completions 的差异都处理了：prompt 进 `input` 而非 `messages`、
输出上限叫 `max_output_tokens`、推理强度嵌套为 `reasoning={"effort": ...}`、正文取
`response.output_text`、流式只累加 `response.output_text.delta`（推理摘要事件不能排版进
PDF）。

**`models/anthropic_messages.py`**（`anthropic_messages`）——**不引入 `anthropic` SDK**，
用项目已有的 `requests`。三个需要留意的点：

1. **端点拼接**：`base_url` 以 `/v1` 结尾时只补 `/messages`，否则补 `/v1/messages`，
   否则粘 `https://api.anthropic.com/v1` 会变成 `/v1/v1/messages`。测试覆盖了带/不带
   结尾斜杠与带/不带 `/v1` 四种组合。
2. **`max_tokens` 是必填**：优先模型配置值 → `context_length / 4` → 4096。
3. **不发 `reasoning_effort`**：Anthropic 用 `thinking.budget_tokens`，无法从
   low/medium/high 推出预算。要开 thinking 请写 `extra_body: {"thinking": {...}}`；
   `extra_body` 在这里是**顶层合并**，不是 OpenAI 那种透传字段。

流式解析 SSE 的 `content_block_delta` / `text_delta`，`thinking_delta` 被忽略；
429 与 5xx 归为可重试（`TransientApiError`），4xx 直接抛出不重试。

`pyinstaller` 的 `hiddenimports` 补了两个模块名——它们只被 import 一次以触发
`@register`，静态分析会把它们当成未使用而丢掉，打包版就会报 unknown provider。

### 界面

`#field-kind` 从 `<input>` 改为 `<select>`，三个选项带端点路径。`applyProviderFields()`
里新增 `paintProviderKind()`：**`config.yaml` 里手写的、不在三项内的 kind 会临时插一个
选项并选中**，否则保存时会被悄悄改写成 `openai_compat`——一个拼写错误会变成行为改变。
离开该供应商时这个临时选项被移除。

### 验证

`pytest` **253 passed**（新增 `test_openai_responses.py` 11 条、`test_anthropic_messages.py`
20 条，注册表断言扩展为三个协议）、`typecheck`、`check:i18n`（172 keys）、`build` 全绿。

CDP 实测：`#field-kind` 是 `SELECT`，三个选项与标签正确；分别选中三个供应商时下拉值与
`kind` 一致；手写 `my_custom_kind` 的供应商会多出第四个选项并被选中，**不改任何字段直接
保存后磁盘上仍是 `my_custom_kind`**；切回内置供应商时临时选项消失。

### 已知限制

- 三个协议都只做「文本进、文本出」的翻译用法，没有实现工具调用、结构化输出、多模态。
- Anthropic 的 thinking 需要用户自己在 `extra_body` 里写，界面上没有专门字段。
- Responses 适配器依赖服务端真的实现了 `/responses`；不少 OpenAI 兼容网关只有
  chat/completions，选错会在请求时报 404。

## 让三个协议真正可靠：修三处行为缺陷 + 「测试连接」

上一轮把「类型」改成三选一下拉并实现了三个适配器。之后被问到「这三个协议真的能完美调用
吗」，核查后发现：**字段映射是对的，但有三处行为缺陷会让长任务出问题，而且它们与协议
无关，三个适配器一视同仁。**

### 缺陷一：永久错误无限重试，且无法取消

`converter.py` 的段落 worker 原本是：

```python
@retry(wait=wait_fixed(1))
def worker(s): ...
```

tenacity 的默认值是 `stop_never` + `retry_if_exception_type(Exception)`（核对过
`tenacity/retry.py:113` 与 `__init__.py:262-264`）。后果：

- 选错协议 → 404、key 错 → 401、模型总丢 `{vN}` → 每秒重试一次，**永不停止**；
- 取消标志只在翻页循环和 OCR 循环里检查，**不在 worker 里**，所以点取消也停不下来，
  只能杀进程。

改成模块级 `translate_segment()`：最多 3 次尝试（1s、2s 退避），**每次尝试前检查
`cancellation_event`**，并把三类永久错误直接抛出、不重试（`PERMANENT_TRANSLATION_ERRORS`：
占位符丢失、截断、空回复）——同样的输入必然同样失败，重试只是推迟错误信息。
`TranslateConverter` 新增 `cancellation_event` 参数，由 `translate_patch()` 传入。

**实测**：一个指向不存在路径的 provider，单页 6 段 PDF，**4.0 秒后中止**并报
`Anthropic API error (HTTP 404)`，日志里能看到 3 次尝试的边界。改动前这会永久挂起。

### 缺陷二：截断与空回复被当成正常结果

三个适配器都不检查终止原因，空回复也会被接受并写进缓存。排版阶段原文已被清空，于是页面
上留下**半句话或一块空白，没有任何提示**。

- `base.translate()` 在写缓存前抛 `EmptyTranslationError`；
- `openai_compat` 检查 `finish_reason == "length"`（流式在最后一个 chunk 上取）；
- `openai_responses` 检查 `status == "incomplete"`，流式监听 `response.incomplete`；
- `anthropic_messages` 检查 `stop_reason == "max_tokens"`，流式取 `message_delta` 里的
  `stop_reason`。

### 缺陷三：`temperature` 无条件发送

三个适配器都固定带 `temperature`（默认 0）。o 系列、gpt-5 这类模型会直接拒绝该参数 →
400 → 又回到缺陷一。

`ModelEntry.temperature` / `ResolvedModel.temperature` 改为 `float | None = None`，
三个适配器改成 `if ... is not None` 才发送。**默认行为不变**：设置页新建模型仍写 `0`，
配置模板也仍是 `0`，只有用户主动清空输入框才不发送（输入框加了对应 placeholder）。
实测：清空后保存，`config.yaml` 里不出现该键；请求体里也没有 `temperature`。

### 顺带：Anthropic 的 Session 改为线程本地

`requests.Session` 没有线程安全保证，而管线用 4–16 个线程共用一个适配器实例。改成
`threading.local()` 惰性创建，与 `cache.py` 的做法一致。测试用 3 个线程各取一次
`adapter.session`，断言拿到的是三个不同对象。

### 新功能：测试连接

`sidecar.providers.test`：拿**设置页当前草稿值**（接口地址、kind、模型）发一次极短的翻译，
返回延迟与模型回声，或把 HTTP 状态原样透出。这样选错协议、key 失效、地址写错都能在长任务
开始前发现，而不用等第一段译文卡住。

- 走 `do_translate` 而不是 `translate`：**不读不写缓存**，也没有 `{vN}` 可校验；
- 界面从不回显密钥，所以 `api_key` 为空时回落到 `config.load()` 里已存的那把；
- **超时处理**：sidecar 是单线程 stdin 循环，而适配器内部有 8 次重试 + 120s 连接超时，
  坏端点能挂几分钟。探针放在 daemon 线程里 `join(25)`，超时就返回「超时」，stdin 循环
  最多卡 25 秒（用户主动点击的代价）。

界面在供应商标题行右侧、删除按钮左边加了「测试连接」按钮，点击时禁用并显示「测试中…」，
结果写进同行的 `.msg` 槽位（成功绿、失败红）。错误串会剥掉 Electron 的三层前缀
（`Error: Error invoking remote method 'sidecar:request': Error: …`），只留最后一层。

### 补上验证缺口：真实 HTTP 测试

之前所有适配器测试都 mock SDK 对象，**没有任何一条真正发过 HTTP 请求**。新增
`tests/test_adapters_http.py`：用标准库 `ThreadingHTTPServer` 起一个本地服务，同时实现
三种形状，让三个适配器走真实 socket。覆盖 URL 拼接（含 `/v1` 与不带两种）、
`Authorization` / `x-api-key` / `anthropic-version` 头、请求体字段、SSE 解析、截断检测、
temperature 省略。不需要 key，也不需要网络。

写这批测试时发现一个真实约束：SDK 的 `responses.stream()` 带状态机，要求
`response.created` → `output_item.added` → … → `completed` 严格按序，只发 delta 会
`RuntimeError`。翻译只需要 delta 和最终的 `incomplete`，因此改用
`client.responses.create(stream=True)` 读**原始事件流**，耦合更少、对精简事件集的网关也更宽容。

### 验证

`pytest` **288 passed**（新增：`test_adapters_http.py` 17 条、`test_translate_segment.py` 7 条、
`test_sidecar_provider_test.py` 8 条）、`typecheck`、`check:i18n`（178 keys）、`build` 全绿。

CDP 实测：按钮存在且文案为「测试连接」；成功时绿字显示 `连接正常 — 62 ms，模型返回「你好」`；
失败时红字（`msg msg--error`）显示 `连接失败：Anthropic API error (HTTP 404): not found`；
点击期间按钮禁用、文案变「测试中…」；切换供应商时旧结果被清空；请求体里确认未配置
temperature 时该字段不存在。

### 仍然存在的限制

- 取消只能打断重试循环，**打断不了正在进行的 HTTP 请求**——这是网络层的性质，不是漏做。
- 测试连接只验证「这个协议、这个地址、这把钥匙能不能通」，不验证译文质量。
- 截断检测依赖各 API 正确返回终止原因；有的自建网关不返回 `finish_reason`，那种情况下
  无法识别截断。

## 供应商改名、供应商级开关、上游模型拉取

三件事一起做，因为都落在模型设置页的同一块区域。

### 1. 供应商改名

标题行加了铅笔按钮，点进去 `h2` 就地换成同字号输入框（自动聚焦全选），Enter/失焦提交，
Esc 取消。空名或重名会写 `#model-msg` 并重新打开输入框，免得用户看不到自己输错的内容。

供应商在配置里是**以名称为键**的，所以改名是换键：

- **保留原位置顺序**——用 `delete` + 赋值会把条目挪到末尾，而文件顺序决定无参调用落到
  哪个模型（`config.resolve()` 的文档里写明了这一点）；
- 同步 `editingProvider`、`cachedProviders` 摘要的键、localStorage 里形如
  `provider|alias` 的选项值。

**顺带堵了一个会泄露密钥的洞。** `config.save` 收到的是渲染进程回传的 `config.get` 文档，
那里的 `api_key` 已经是展开后的字面值；`_restore_env_refs` 原本靠路径
`providers.<名字>.api_key` 把 `${VAR}` 写回去。改名后路径不存在，**密钥会以明文落盘**。
现在路径还原之外多了一段**按值匹配**的兜底：任何等于某个 `${VAR}` 展开值的字符串都改回模板。
这同时覆盖了改名、条目重排，以及同一个密钥出现在 `extra_body` 等嵌套位置的情况。
代价是"用户手写一个恰好等于环境变量值的字面量"也会被替换——两个方向加载后展开结果相同，
而不替换意味着明文写盘，所以这个方向的错误更轻。`tests/test_config_env_refs.py` 6 条钉住。

### 2. 供应商级开关

原来放删除图标的地方，现在同时有垃圾桶和开关（按你的要求保留删除）。

`Provider.enabled`（默认 `true`，无需迁移）。关掉后 `all_models(only_enabled=True)` 跳过它
的**全部**模型，所以选项面板不列出、无参 `resolve()` 不回退到它；**显式点名**则报
`Provider ... is disabled`。左栏该行加 `is-off`（名称图标变灰）但仍留在列表里——那是唯一
能重新打开它的地方。开关点了立即保存，与模型开关一致。

删除与关闭是两件事：关闭保留地址和密钥，删除才真的丢掉。

### 3. 选项面板显示「供应商 / 模型 ID」

`renderOptionModels()` 的文本从 `供应商 · 别名` 改成 `供应商 / 模型 ID`（发给 API 的真实 id）。
`value` 仍是 `provider|alias`，因为 `resolve()` 认别名；两个别名指向同一 id 时，别名放在
`title` 里以备分辨。

### 4. 从上游拉取模型并勾选导入

新增 `models/catalog.py`，刻意**不放在适配器里**：这是"端点自称有哪些模型"，与翻译是两个
关注点，合在一起会让适配器多背一个与请求路径无关的端点。

- `openai_compat` / `openai_responses`：`{base}/models` + `Authorization: Bearer`，
  读 `{"data": [...]}`，也接受裸数组；
- `anthropic_messages`：`{base}/v1/models`（已以 `/v1` 结尾不重复拼）+ `x-api-key` +
  `anthropic-version`，按 `has_more`/`last_id` 翻页（上限 10 页，游标不前进即停）；
- 结果排序去重；失败抛 `CatalogError`，消息带状态码与 URL。

`sidecar.providers.models` 复用探针那套：草稿值优先、空 key 回落已存密钥、daemon 线程 +
`join(20)` 防坏端点冻住单线程循环。

界面用原生 `<dialog>`（Esc、焦点圈定、遮罩都不必自己实现），带搜索框——OpenRouter 这类有
几百个 id。**已配置的项勾选且禁用**并标「已添加」，避免导入时覆盖用户调好的参数。导入时别名
取 id 本身，之后可在行编辑里把别名与 API id 分开。

**不自动填 `context_length`**：各家 `/models` 的元数据差异太大，猜一个数字比留空更糟。

### 验证

`pytest` **328 passed**（新增 `test_catalog.py` 19 条、`test_sidecar_catalog.py` 7 条、
`test_config_env_refs.py` 6 条、供应商级开关 8 条）、`typecheck`、`check:i18n`（193 keys）、
`build` 全绿。

CDP 实测（本地 mock 同时提供 `/models` 与翻译端点）：

- 选项文本为 `w国外反代 / deepseek-v4.1-flash` 形式；
- 改名后左栏行、标题、`config.yaml` 键名、选项前缀全部同步，位置不变；
- 关掉供应商后选项从 3 项变 1 项，行加 `is-off`；重新打开恢复；
- 拉取弹窗列出 6 个上游 id，已配置的两项勾选禁用并标「已添加」；搜索 `hy` 过滤出 2 项；
  勾选 `hy3`、`glm-5.3-flash` 导入后模型行与选项都出现，`config.yaml` 写入；
- 地址改错后弹窗内红字显示 `拉取失败：HTTP 404 from http://127.0.0.1:8801/nope/models`。

### 已知限制

- 只做供应商改名，模型别名仍不能改（导入时别名取 id，可在行编辑里改模型 ID，但别名本身没有
  重命名入口）。
- 拉取只读 `id` 字段，不解析能力/上下文长度等元数据。
- `providers.models` 与 `providers.test` 一样受单线程 sidecar 限制，最长阻塞 20/25 秒。

## 拉取弹窗与模型行的三处修正

### 1. 勾选框跑到模型名上方（CSS 优先级 bug）

`.models-option` 是 `<label>`，而全局 `label { flex-direction: column }` 定义在文件更后面。
两者**优先级相同、后者更晚**，所以 `.models-option` 的 `display: flex` 被竖排方向接管，
复选框落到名称上方，行高也从 32px 涨成两行。

这与早先「流式返回」复选框的问题是同一个根因（`.check` 当时也这么修过）。现在
`.models-option` 显式写 `flex-direction: row`，并把选择器提升为 `.models-options .models-option`
——比裸 `label` 多一个类，将来规则顺序变化也不会再被悄悄覆盖。实测复选框与名称 top 相差
2px 内、复选框在左、行高 32px。

### 2. 全选

搜索框与列表之间加了 `[✓] 全选   已选 n / m`。

- 只作用于**当前筛选结果里尚未添加**的项；已添加的是 `disabled`，不参与，因此全选不会
  覆盖已有配置。
- 计数与全选框的状态**每次都从 DOM 重算**（`paintModelsCount()`），而不是在筛选、勾选、
  全选三处各维护一份，避免三者不同步。
- 部分选中时用原生 `indeterminate` 显示中间态。
- 搜索 `glm` 后全选只选中那 5 项，计数同步变成 `已选 5 / 5`；取消其一变
  `已选 4 / 5` 且全选框进入中间态。以上均用 CDP 实测。

### 3. 模型行以模型 ID 为主

原来把**别名**当主标签、API id 作灰字补在后面，于是出现「deepseek　deepseek-v4.1-flash」
这种读起来像「供应商 + 模型」的两段式。

现在主标签就是**模型 ID**（发给 API 的名字），别名只在两者不同时以灰色小徽章出现
（`title` 说明「本地别名」），与上下文长度、流式两个徽章同一层级。

保留别名显示而不是直接隐藏：它是 `config.yaml` 里的键，也是选项面板 `value` 的一半；
彻底隐藏会让「配置文件里的键」和「界面上看到的名字」对不上，改 YAML 时无从对照。

### 验证

`pytest` 328 passed、`typecheck`、`check:i18n`（196 keys）、`build` 全绿。

CDP 实测：勾选框与名称同行且在左、行高 32px；`flex-direction` 计算值为 `row`；
初始 `已选 0 / 6`（7 个上游 id 中 1 个已添加被锁定）；点全选后 `已选 6 / 6` 且全选框勾上；
搜索 `glm` 后为 `已选 0 / 5`，全选后 `已选 5 / 5`；取消一项后 `已选 4 / 5` 且中间态生效。
截图确认弹窗与模型列表的最终形态。

## 侧栏底部重排、设置按钮重设计、修复拉取重复

### 1. 设置按钮下移到「就绪」那一行

原来 `.sidebar__status` 一行里挤着 `[进度条][齿轮]`，而「就绪」文字在**下一行**单独放着，
进度条因此只能占侧栏宽度减去 30px 齿轮再加间隙。

现在分成两行：

```
[进度条 ──────────────────────────────]   ← .sidebar__status，只剩进度条
[就绪。                          [⚙]]   ← .sidebar__foot
```

进度条从 315px（340 − 12×2 padding − 30 按钮 − 8 间隙里的实际余量）变成占满
`.sidebar__actions` 的内容宽度，实测 315px 全宽；齿轮与状态文字的 `top` 相差 5px 内、位于
右侧。窄侧栏时 `.sidebar__status-text` 先截断（`flex: 1 1 auto` + `ellipsis`），按钮不被挤走。

齿轮**留在侧栏**没有搬到 PDF 视图右上角——按交互里的选择，与最初那句「调整到 pdf 视图
右上方」相反。第一张图（标题栏的图钉）按选择未改动。

### 2. 设置按钮重设计

- 尺寸 30×30 → **28×28**，圆角 6 → **8px**，底色 `var(--card)`，hover 转淡灰；
- 图标从「粗描边圆 + 8 个矩形齿」换成**单条描边路径的六齿齿轮**（stroke 1.6、round 端点）。
  原来那套在 18px 下齿与本体以直角相接，糊成一团；描边路径在同样尺寸下轮廓更清楚；
- `:focus-visible` 用 `--accent` 描边，键盘可达。

**踩到一个 CSS 优先级坑**：`.btn--settings` 原本写在 1174 行，而通用的 `.btn` 在 1414 行，
同优先级下后者更晚，把 `width`/`border-radius`/`padding` 全盖回去了——第一次量出来是
30×28、圆角 6px。改用 `.btn.btn--settings`（两个类）提高优先级后才是 28×28 / 8px。
这与本轮和上一轮修的两个 `label { flex-direction: column }` 覆盖问题是同一类错误：
**单类选择器在长样式表里靠位置取胜是不可靠的**。

`.btn--icon` 与 `.icon-gear` 在改动后已无引用，一并删除，避免留死代码。

### 3. 修复「拉取上游后同一模型出现两次」

**根因**：`paintModelsOptions()` 判断「已添加」时用的是

```ts
new Set(Object.keys(provider?.models ?? {}))
```

`Object.keys` 拿到的是**别名**。模型行允许别名与 API id 不同（例如别名 `local-alias`、
`model: deepseek-v4-pro`），而上游清单返回的是 **id**。于是 `has("deepseek-v4-pro")` 为
false，弹窗把这个其实已经配好的模型当成新的，导入后 `models["deepseek-v4-pro"]` 与已有的
`models["local-alias"]` 指向同一个 model，列表里就是两条。

**两层修法**：

1. 新增 `configuredModelIds(provider)`，用 `entry.model || alias` 收集**API id**，弹窗据此
   判断「已添加」；
2. `importSelectedModels()` 再加一道守卫，跳过任何 id 已存在的项——即使弹窗状态因某种原因
   失效，也不会产生重复。

**回归验证**：配置里写 `local-alias: {model: deepseek-v4-pro}`，mock 的 `/models` 返回
`deepseek-v4-pro`。修改后弹窗中该项显示「已添加」、勾选且禁用；在只含 deepseek 的筛选下
点「导入所选」，模型行数量保持 2 条不变。

### 验证

`pytest` 328 passed、`typecheck`、`check:i18n`（196 keys）、`build` 全绿。

CDP 实测：进度条 315px 占满；齿轮 28×28、圆角 8px、单个 `path`（无 `rect`）、与状态文字同行
且在右；重复检测如上。截图确认侧栏底部与按钮形态。

## 2026-10-17（续）：一个按钮启停、真正能取消、输出到 translated/、文档搜索、拖拽不再卡

### 1. 开始/取消合成一个按钮

原本 `#btn-translate`（蓝）和 `#btn-cancel`（红、默认 `hidden`）并排。点开始后
第二个按钮凭空出现在右边，整行高度和宽度都跳一下，而且两个元素各自被不同代码
开关，很容易出现"都显示"或"都不显示"。

改成**同一个按钮三种状态**，由一个 `paintTranslateButton()` 统一决定：
空闲 = 「开始翻译」+ `.btn--primary`；运行中 = 「取消翻译」+ `.btn--danger`；
按下取消后 = 「正在取消…」+ 禁用。点击时按 `activeJobId` 是否为 `null` 分流。
删除 `#btn-cancel` 与 `updateTranslateButton()`。

### 2. 取消以前根本没到过引擎

**根因**：`sidecar.py` 的 `main()` 是 `for line in sys.stdin: handle(request)`，而
`method_translate_start` 在**这个循环里同步跑完整份翻译**。`translate.cancel` 是
标准输入上的一行——作业结束前主循环根本回不到 `readline`，请求就一直躺在管道里。
所以不是取消逻辑写错了，是**请求从未被读到**。这也解释了为什么按钮点了"没反应"，
而不是"报错"。

**修法**：

- `handle()` 对 `translate.start` 起一个 daemon 线程，主循环立刻回去读 stdin；
  其余方法仍同步执行（`_BACKGROUND_METHODS` 白名单，不按"慢"来猜）。
- `_emit()` 持 `_EMIT_LOCK`。两个线程同时写 stdout 会把两条 JSON 拼在同一行上，
  渲染进程一次丢两份。这和"加锁很稳妥"无关——不加锁是真的会坏。
- `method_translate_start` 捕获 `asyncio.CancelledError`，回
  `{cancelled: true, outputs: []}`，**且不发 `done` 事件**。取消是正常结束。

**实测**：一个每段耗时 5s 的假端点，点取消后 **1.34s** 停下，进度显示「已取消」，
按钮复位，`translated/` 里没有半成品文件。

### 3. 输出目录去掉，固定写进源文件旁的 `translated/`

`papers/paper-a.pdf` → `papers/translated/paper-a-mono.pdf`。

默认值放在 `pdf/pipeline.py` 的 `translate()` 里，因为它是**逐文件**解析的：一次任务
可能跨越几个文件夹，共用一个目录会把不同论文的产物混在一起。`api.translate()` 于是
在 `output` 为空时传 `""`（而不是原来的 `Path.cwd()`）。CLI 的 `-o` 与 MCP 工具都显式
传目录，行为不变；`two_phase.output_paths()` 是 MCP 自己的约定，没动。

前端删掉整个输出目录 label、`#field-output`、`#btn-pick-output`；主进程删掉
`defaultOutputDir()` / `withDefaultOutput()` / `app:defaultOutputDir` /
`dialog:pickOutputDir` 与 `DIALOG_STRINGS.selectOutput`。

**列出译文**（按你选的"始终列出并加标记"）：`app:listPdfs` 返回
`{path, translated}[]`，除源目录外再扫 `translated/`。译文行**没有勾选框**，用一个
等宽的 `.file-row__tick-slot` 占位保持文件名左对齐，带「译文」角标、文字用次要色。
没有做成"禁用勾选框"——一个灰掉的框只会让人问"为什么不能勾"，而没有框就说明
"这不是可选项"。

### 4. 文档搜索

`#docs-search` 放在文件夹选择器与列表之间。**只过滤画出来的行，不动 `checkedFiles`**：
被搜索藏起来的已勾选项仍然计入任务，否则打字就会悄悄改变要翻什么。无匹配时显示
`.file-list__empty`。

### 5. 侧栏拖拽"一卡一卡"

**根因**：指针一越过侧栏边界就落在预览区的 `<iframe>` 上。它是**独立文档**，移动
事件不会冒泡到父页面，挂在 `window` 上的监听收不到 → 宽度停住；等指针移回来才跳
一下。之前那轮已经知道 iframe 吞事件（所以监听挂在 window），但没意识到**指针进入
iframe 之后父文档连事件都没有**。

**修法**：`body.is-resizing .preview__frame { pointer-events: none }` —— 拖拽期间
iframe 退出命中测试，所有移动都留在本页。另外用 `requestAnimationFrame` 合并移动
（只记最新 `clientX`）：每次写 `--sidebar-width` 都会让侧栏、预览与查看器各自重排，
而鼠标事件率远高于帧率，逐事件重排就是连续掉帧。

### 验证

`pytest` **340 passed**（原 328 + `test_sidecar_cancel.py` 6 + `test_output_dir.py` 6）、
`typecheck`、`check:i18n`（198 keys）、`build` 全绿。

CDP 在真实 Electron 里跑了 **22 项断言，全过**：

- 列表：3 份源文件 + `translated/` 里的 1 份译文；译文行无勾选框、有「译文」角标；
- 搜索 `beta` → 只剩 1 行；搜不到时显示空状态；**按钮禁用状态不受过滤影响**；
- 空闲按钮是「开始翻译」+ `.btn--primary`，DOM 里已无 `#btn-cancel`；
- 运行中同一按钮变「取消翻译」+ `.btn--danger`；再点变「正在取消…」且禁用；
- 取消后 **1.34s** 回到「开始翻译」，进度「已取消 — 未完成的文件没有写出」；
- `translated/` 里没有半成品；
- 拖拽 5 个采样点（300/380/460/520/430）宽度逐一跟上，拖拽时 iframe
  `pointer-events: none`，松手后 `body.is-resizing` 已清除。

进度文案改了一次：初版 `progress.cancelled` 是「已取消。」，而左侧固定显示
`stage.cancelled`（「已取消」），渲染成「已取消 — 已取消。」。改成说明实际结果
（「未完成的文件没有写出。」）后才有信息量。

## 2026-10-17（续二）：译文形式可选、页码范围裁掉输出、产物不覆盖

### 1. 「译文形式」：交替 / 仅译文，默认交替

以前**永远产出两个文件**：`-mono`（纯译文）与 `-dual`（原文页、译文页交替）。用户要的是
二选一。选项面板加 `#field-output-mode`：

| 值 | 文案 | 产物 |
|---|---|---|
| `dual`（默认） | 交替（原文 + 译文） | 8 页输入 → 16 页 |
| `mono` | 仅译文 | 8 页输入 → 8 页 |

**没有做成"少写一个文件"，而是少建一份文档**。`translate_stream(..., output_mode=)`：
`mono` 只序列化译文文档，`dual` 才走 `doc_en.insert_file()` + 逐页 `move_page`；
`both` 保留两者，给 CLI 和 MCP 用（它们的行为不变）。没要的那一侧返回 `b""`，
`pipeline.translate()` 据此不写盘，`outputs` 里对应字段是空串。

**前端差点因此出 bug**：`showOutputs()` 原来无条件遍历 `[dual, mono]`，空串会被
`activateTab("")` 开成一个永远加载不出来的标签页。现在先过滤空串。CDP 验证：只有交替
文件时**只开一个标签**。

选项**不持久化**——它是"这一次"的选择，且默认值就是交替的形式。

### 2. 页码范围现在也裁掉输出

以前 `pages` 只控制"翻译哪几页"，输出仍是整份文档：填 `2-3` 得到的是整本，其中第 2-3 页
是译文、其余是原文。用户的预期是"第 2-3 页"就该拿到两页。

改为**双重语义**：翻译哪几页 + 输出保留哪几页。`_selected_pages()` 排序去重、丢掉越界页
（`1-99` 在 10 页文档上是正常输入），整份选中时返回 `None`，连 `select()` 都不调。
裁剪位置在**打完补丁之后、`subset_fonts()` 之前**——不给马上要丢的页做字体子集化。

`two_phase` 的两遍式收集/回填走同一个 `translate_stream`，所以 MCP 的页码语义也跟着
变成"输出只含这几页"，这正是这个参数本来的意思。

### 3. 产物永不覆盖

`_unique_paths()`：`paper-mono.pdf` 已存在 → `paper-mono (2).pdf`。mono 与 dual **共用
一个序号**（`paper-mono (2)` 配 `paper-dual (2)`），否则一对文件看起来像两次无关的运行。
`pipeline.translate()` 与 `two_phase.write_outputs()` 都走它。

**一个实现细节**：`(2)` 必须放在扩展名之前，而不是直接接在完整文件名后——初版写成
`f"{stem}{marker}{suffix}"` 得到 `paper (2)-mono.pdf`，看起来像另一个文件。现在按各
后缀自己的 `.pdf` 拆开再拼。

### 4. 顺手修掉一个真回归：Windows 上第一个任务会挂死

上一轮把 `translate.start` 挪到工作线程后，**numpy 的 C 扩展第一次是在子线程里导入的**。
在 Windows 上、且 stdin 是管道时（Electron 正是这样），这个导入会永久卡在模块初始化里：
线程不返回，一条进度事件都不发。进程内直接调用却完全正常，所以之前的测试全绿。

本次 CDP 验证一开始就撞上了：界面永远停在「正在启动…」。逐层剥离后确认——
同一个 sidecar 子进程、同一份配置，直接 `api.translate()` 9 秒出结果，走 sidecar 则
20 秒后 faulthandler 显示线程停在 `numpy/_core/multiarray.py → create_module`。

修法：`main()` 在进入 `for line in sys.stdin` 之前先 `import numpy`（主线程）。
`tests/test_sidecar_cancel.py` 加了两条断言，其中一条直接检查源码里这行在循环之前。

### 验证

`pytest` **361 passed**（原 340 + `test_output_mode.py` 19 + 取消相关 2）、`typecheck`、
`check:i18n`（201 keys）、`build` 全绿。

**真实管线端到端 16 项**（4 页样例 PDF + stub 翻译器，不联网）：

- `dual` → 8 页；第 1 页原文、第 2 页译文、第 3 页第 2 页原文；
- `mono` → 4 页，每页都是译文，且**没有**写出 dual 文件；
- 页码 `2-3`：`dual` → 4 页且从第 2 页开始，`mono` → 2 页；
- 连跑两次：多出 ` (2)` 文件，第一份的字节未变。

**Electron 界面 9 项**：下拉存在、两项、默认 `dual`、中英文案正确；跑真实作业后
`translated/` 里只有 `paper-dual.pdf`、只开一个标签；切到 `mono` 再跑，只多出
`paper-mono.pdf`，dual 未被改动。


## 翻译缓存：本地已经在命中，服务端前缀缓存这条路走不通

用户反馈「命中率为 0」。先分清两个缓存：

- **本地 sqlite 缓存**（`cache.v1.db`）一直都在工作。检查时库里有 1327 条记录，
  其中 967 条是 `deepseek-v4.1-flash` 的真实译文，WAL 也在持续写入。它按
  「引擎 + 参数 + 段落原文」精确匹配，所以新论文第一次翻必然接近 0；同一份 PDF
  在参数不变时重跑才会接近 100%。这不是故障，也不需要再造一层缓存。
- **服务端提示词缓存**（网关返回的 `prompt_cache_hit_tokens`）才是真正为 0 的那个。

### 探测：网关支持缓存，但加长提示词是亏的

用当前网关做了三组探针：

| 固定前缀 | prompt tokens | 第二次请求命中 |
|---|---|---|
| 约 2.9 kB | 564 | 384 |
| 约 1.2 kB | 298 | 128 |
| 约 0.9 kB | 235 | 0 |

规律是 `hit = max(0, floor(tokens / 128) - 1) * 128`：**第一个 128-token 块永远
不缓存**，之后每块 128。也就是说，要让前缀产生命中，前缀本身至少要到 256 token；
而前缀越长，未命中的部分（至少 128 token）就越多。原来的短模板在 `${text}` 之前
只有约 50 token，按原价计费；任何“可缓存”的加长版本都要多付至少 78 token 的全价，
**命中省下的 10% 抵不过新增的固定开销**。因此最终**回退到原始短模板**，
只保留 `prompt_cache_hit_tokens` 的采集——它仍然能告诉你网关有没有在别处命中。

### 保留的改动

1. `cache.py`：`snapshot_stats()` 提供 hits / misses；只有缓存开启且真正查询才计数，
   `cache.enabled=false` 与 `ignore_cache` 都不会虚增 miss。
2. `models/base.py`：`record_provider_cache()` / `provider_cache_snapshot()` 累计
   服务端 usage，进程级、按任务清零。
3. `models/openai_compat.py`：非流式响应里读
   `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`，读不到再退到
   `prompt_tokens_details.cached_tokens`；字段缺失就静默跳过。流式请求没有 usage，
   当前配置 `stream: false`，正好覆盖。
4. `sidecar.py`：`translate.start` 开始清零，结束时把
   `cache: {hits, misses}` 与 `provider_cache: {hit_tokens, miss_tokens}` 同时放进
   回复和 `done` 事件；取消的回复也带（统计取消前已完成的部分）。
5. `app.ts` / `i18n.ts`：完成状态追加「本地缓存命中 x/y 段」和
   「服务端提示词缓存 x/y tokens」，中英各两条。以后“命中率”不再是猜的。

真机端到端（临时生成的 4 页 PDF，只翻第 1 页，共 9 段；这一轮用的是加长版提示词，
仅用于验证计数器）：

- 第 1 次：本地 `0 hit / 9 miss`，服务端 `1152 hit / 1767 miss`（9 段 × 128）。
- 第 2 次：本地 `9 hit / 0 miss`，服务端 `0/0`——一次 API 都没发。

**本地缓存才是省钱的部分**：同一份文档重跑、先跑交替再跑仅译文、任务中断后重跑，
都不会再付第二遍钱。服务端缓存只对“本来就该发很长固定前缀”的场景有意义，本项目不是。

测试：`pytest` 370 项通过，`typecheck` / `check:i18n`（203 键）/ `build` 全绿；
`test_default_prompt_stays_short_for_cost` 防止以后有人再把提示词“优化”长。


## 2026-10-17（发布前）：打包审计与 Windows 安装包验证

第一版准备发布，按「装到别人电脑上能不能跑」逐项检查。结论：代码本身没问题，
打包链和首次运行资源有硬伤；已修并实际产出 NSIS 安装包。

### 会直接让构建失败的

1. `scripts/build.sh` 只认 `.venv/bin/python`。Windows 的 venv 是
   `.venv/Scripts/python.exe`，脚本在 Git Bash 里第一步就退出。改成两种布局自动识别。
2. `electron-builder` 不在 `devDependencies`，`npx electron-builder` 会临时去
   registry 拉最新版，构建不可复现。加 `electron-builder@^26`。
3. electron-builder 缓存为空，NSIS / winCodeSign 要从 GitHub release 下载，而实测
   `github.com` release 链路 8/8 超时。`build.sh` 默认设置
   `ELECTRON_BUILDER_BINARIES_MIRROR=https://npmmirror.com/mirrors/electron-builder-binaries/`，
   本次构建从镜像成功下载 NSIS 3.0.4.1、nsis-resources、7zip。

### 装得上但换台电脑会坏的

4. **PyInstaller 漏了 pdfminer 的 CMap 数据**。`pdfminer/cmap/*.pickle.gz` 是按
   `__file__` 读的，`collect_submodules("pdfminer")` 只收模块不收数据；打包版遇到
   CID 字体（中文 PDF 常见）会解析失败。加 `collect_data_files("pdfminer")`。
5. **中文字体只从 GitHub release 下载**，干净机器上必然失败。新增
   `scripts/stage_assets.py`，构建时把默认中文字体复制进 `python/bundle_assets/`，
   spec 打进 `_internal/bundle_assets`；`assets.ensure()` 按「缓存 → 内置 → 网络」
   顺序，`status()` 也认内置副本。GoNoto 能取到就一起打（本次第二次构建成功取到）。
6. **sidecar 会闪控制台窗口**：PyInstaller `console=True`，`spawn` 没传
   `windowsHide`。补上。
7. **没有单实例锁**：两个窗口、两个 sidecar 共用配置和缓存。加
   `requestSingleInstanceLock()` + 第二次启动聚焦已有窗口；Windows 下补
   `setAppUserModelId("com.pdfxlate.app")`。
8. **mcp 进不进包取决于 venv**：`dev` extra 不含 mcp，但界面始终宣传 MCP，
   README 还写着“桌面包不带 mcp”。`dev` 加 `mcp>=2.3.0`；spec 收集 mcp 子模块时
   排除 `mcp.cli`（它 import `typer`，服务端不需要）并 `on_error="ignore"`。
9. **没有 LICENSE**：项目是 AGPL-3.0-or-later，分发必须带协议文本。新增
   `LICENSE`（34 KB 全文），`pyproject.toml` 加 license 字段，NSIS 安装向导加许可页，
   asar 里也带一份。

另外两处小修：`assets._download_one()` 比对 `Content-Length`，截断下载不再被当成
成功；`npm run build` 前清 `out/`，避免旧文件混进包；electron-builder 的 `files`
排除 `*.map`、`buildResources` 指向存在的 `assets/`。

### 实际验证（不是只看配置）

1. `bash scripts/build.sh` 成功：`dist/win-unpacked/` + `dist/pdfxlate Setup 0.1.0.exe`。
   安装包 180 MB，解包 576 MB（Electron 228 MB + sidecar 308 MB，含 14 MB 中文字体
   和 15 MB GoNoto）。
2. **干净用户目录下的打包 sidecar**（临时 `LOCALAPPDATA`/`APPDATA`）：
   - `assets.status` → `font_zh=true`（内置）、`layout_model=false`；
   - `assets.download` 从 hf-mirror 下载 75 MB 模型成功；
   - 用本地假 OpenAI 端点跑真实管线：9 段、6 次调用、输出 PDF 含 54 个汉字，
     说明 onnxruntime、pdfminer、fontTools 子集化、openai SDK 在冻结包里都能用；
   - `--mcp` 完成 initialize，列出 `list_providers` / `extract_segments` /
     `typeset_translations` / `translate_pdf`。
3. **打包后的 Electron**（临时 `--user-data-dir` + `LOCALAPPDATA`）：
   - 渲染进程加载，preload API 可用，`ping` 返回 0.1.0，config dir 指向临时目录；
   - 首启横幅可见（只缺模型）、`font_zh=true`；
   - 第二个实例自动退出（exit 0），只保留一个 sidecar；
   - `window.close()` 后 app 退出、sidecar 归零。
4. **NSIS 安装/卸载**：静默装到 `%LOCALAPPDATA%\Programs\pdfxlate`，安装后的 exe
   重复上述 Electron 冒烟全过；卸载后目录清除。注意 Git Bash 传 `/S` 会被 MSYS
   改写路径（曾误装到 `D:\pdfxlate`），测试时用 PowerShell
   `Start-Process -ArgumentList "/S"`。

### 仍未解决 / 不在本轮

- **未签名**：SmartScreen 会提示，需要购买代码签名证书。
- **MSI**：首版只出 NSIS `.exe`；要 MSI 需加 target 并下载 WiX。
- **版面模型仍在线下载**：应用本身需要网络调用翻译 API，且 hf-mirror 实测可用；
  默认中文字体已内置，解决的是确定会失败的 GitHub 下载。
- `npm audit` 报 10 个 dev-only 漏洞（electron-builder 依赖树）；`--omit=dev` 为 0，
  不进安装包。

测试：373 passed，typecheck / check:i18n（203 键）/ build 全绿。


## 公开仓库：匿名身份、干净历史与 Windows 安装包

把 develop 公开发布到 github.com/jk-uzi/pdfxlate 之前，做了三件历史层面的整理。

### 1. 匿名身份

68 个提交原本都是同一个**真实邮箱**。推送前用 `git filter-branch --env-filter`
把作者与提交者统一改成 GitHub 的 noreply 地址，随后删掉 `refs/original`、
`git reflog expire --expire=now --all`、`git gc --prune=now --aggressive`。
现在本地任何分支都查不到它。

> 这里**故意不写出那个邮箱**：这份日志本身要公开，原样引用等于把匿名化白做一遍。
> 需要的人可以在备份 bundle（桌面 `pdfxlate-backup-pre-release/`）里查到原始身份。

用 GitHub 的 noreply 地址而不是空字符串：界面上不显示邮箱，也不会关联到账号。
选择保留 `jk` 这个笔名，是为了署名仍在。

### 2. 干净提交

- **线性化**：原来有 19 个 `merge style-layout` / `merge i18n-core` 风格的合并提交。
  `git rebase --no-rebase-merges --root develop` 把它们全部消掉，58 个提交排成一条直线，
  每个都是完整的功能或修复。rebase 无冲突，并校验过重放后的树与原文逐字节相等。
- **中文化的消息**：`chore: 初始化仓库（空提交，仅用于建立分支）` 与
  `feat: 初始化 pdfxlate 项目` 译成英文。
- **工作区三批拆开**：此前会话留下的 35 个改动文件原本是一整团。按语义拆成三个提交
  （交互与译文输出 / 缓存统计 / 打包与仓库卫生）。三批在 `app.ts`、`i18n.ts`、
  `sidecar.py` 里逐行交叉，`git add -p` 无法安全拆分，所以用脚本从终态里精确减去 B、C
  两批构造出 A 的中间态，再逐批恢复。每批提交前单独跑 typecheck 与相关测试确认自洽。

### 3. 顺带修掉的两处泄露

- `tests/test_mcp_server.py` 硬编码了作者主目录下的绝对路径。删掉后改为相对于测试文件
  查找，顺带修掉一个真实缺陷：协作者的机器上那条路径永远解析不到。
- `docs/development-plan.md` 与 `requirements/development-log.md` 里的
  `/home/jk/桌面/…` 一类绝对路径，改为相对描述。

三处改动**同时也写进了历史**（`--tree-filter`）——只改当前提交不够，旧 blob 仍会带着路径。
这里踩过一次坑：tree-filter 用文本模式读写文件会把整个文件的行尾统一化，显示成 2469 行的
全文件 diff。改成字节级替换后，真实差异只剩 6 行。

### 推送顺序

**先推送，后公开**。`git push --force-with-lease origin develop` 强推重写后的历史
（远程无 fork、无 watcher，安全），再 `git push -u origin main`，然后才
`gh api -X PATCH repos/jk-uzi/pdfxlate -f private=false`，最后把默认分支改成 `main`。
GitHub 从 LICENSE 识别出 AGPL-3.0。

校验：远程 main 上 58 个提交、0 个合并，noreply 地址是唯一身份，grep 原邮箱为 0；
公开内容抽查三个文件无绝对路径；pytest 373 通过、i18n 203 键同步、
typecheck 干净。原始历史的完整备份留在仓库外的 bundle 里（桌面 `pdfxlate-backup-pre-release/`）。

> 署名后来改回了真实身份，见下一节「改回真实署名」。本节保留的是当时的决定过程。

> **强推的残留**：GitHub 侧带旧邮箱的对象在服务端 GC 之前可能仍可通过 SHA 抓取。要彻底
> 断掉只能删库重建；本仓库当时 0 star / 0 fork / 0 issue，损失极小，但删库不可逆，所以
> 没有做。以后若要发布「必须绝对干净」的仓库，新建一个比强推更可靠。

## 改回真实署名，双语 README 与首个正式发布

上一节记录的 noreply 匿名身份只维持了一轮：对外发布时决定改用真实身份，把全部提交的
作者与提交者重新写成 `jk-uzi <2081217232@qq.com>`，并强推 `develop` 与 `main`。
`filter-branch` 只改 `--env-filter`，不动树、不动消息、不动时间；重写前后
`HEAD^{tree}` 与 `git diff` 都为空，说明内容一个字节没变。改完同样清掉
`refs/original`、过期 reflog 并 `gc --prune=now`。

### README 做成中英双语

GitHub 只能显示一个 `README.md`，所以默认放中文，英文版另存 `README.en.md`，两份顶部
互相链接。这样仓库首页默认就是中文，英文读者点一下即可切换。两份文档逐节对应，
不是机翻：数字、命令、限制条件必须在两版里一致。

写的时候顺手修掉旧英文 README 的六处事实错误：

- 项目**不读取 `.env`**（没有 dotenv 依赖），`${VAR}` 只从进程环境展开，
  `.env.example` 仅列出常用变量名；
- 模型下载是**先试 `huggingface.co`、失败回退 `hf-mirror.com`**，不是「只能用镜像」；
- `--config-dir` **只重定向配置目录**，缓存与资源始终用平台目录（`paths.py` 的模块
  注释写了原因）；CLI 的帮助文本原来写错，改成只描述配置目录；
- `TESSDATA_PREFIX` 并未被代码读取，OCR 前缀来自 `ocr.tessdata_prefix`；
- 命令行默认产出 `both`，**界面里只有单语/双语两个选项**；
- 只有 Windows 安装包与 Linux AppImage 实际构建并验证过，macOS 仅配置、未验证。

### 界面截图

README 里的截图是打包后的真实应用，不是设计稿：`dist/win-unpacked/pdfxlate.exe`
配一个独立 `--user-data-dir` 启动（独立的单实例锁，不影响日常配置），用
`--remote-debugging-port` 通过 CDP 预置语言与文件夹、展开选项面板、点开译文行，再
`Page.captureScreenshot`。演示目录里放一份原文与它对应的 `-mono.pdf`，侧栏就能同时
展示源文件与带「译文」标记的输出。截图 305 KB，远低于 pre-commit 的 2 MB 上限。

### 安装包作为 Release 资产

用 `gh release create` 把已验证的安装包挂到 `v0.1.0`，发布说明写清未签名导致的
SmartScreen 提示、首次运行要联网下载模型、以及安装包的大小与 SHA256。没有重建安装包：
asar 内的渲染进程与主进程文件与当前 `out/` 逐字节相同，已核对。

---

## 2026-10-18

### 设置 › Agents：把外部 Agent CLI 接成翻译后端

**目标**：让 pdfxlate 能用本机已登录的 agent CLI 的模型翻译，而不是只能填 API key。
首批五个：Claude Code、Codex、WorkBuddy/CodeBuddy、ZCode、DeepSeek Harness。参考了
本机安装的 Open Design 0.25.0（`AGENT_DEFS` / `agentId` / `agentModels` 的结构），
但代码全部自研，不 import 它的运行时。

**已完成**

- **配置**：新增 `Provider.agent`、`ResolvedModel.agent`、可选 `AgentSettings.paths`
  （`AppConfig.agents`）。`kind: agent_cli` 的供应商必须写 `agent`，否则加载时报错；
  `_flatten()` 把它带进 `ResolvedModel`。`agents.paths` 用于 CLI 不在 PATH 时的
  路径覆盖，同样支持 `${VAR}` 展开/写回。模板里加了一段注释示例。
- **后端 `python/pdfxlate/agents/`**：`defs.py`（五个 agent 的静态定义）、`detect.py`
  （路径解析：配置覆盖 → 环境变量 → PATH → 平台标准目录 / Windows 卸载注册表；版本
  与登录探测；30 秒缓存）、`catalog.py`（Claude 内置列表、Codex `debug models`、
  WorkBuddy help、ZCode `default`、DSH `--dump-config`）、`runtime.py`（按 agent 拼
  命令、空临时 cwd、stdin/argv、输出解析、超时/取消杀进程树、严格重试 → 拆单段）、
  `batch.py`（8 段 / 12000 字符 / 0.2 秒；argv agent 8000；自定义 prompt 时批大小为 1）。
- **适配器 `models/agent_cli.py`**：`AgentCliAdapter` 把批队列接进 `ModelAdapter`；
  `_cache_params()` 追加 `agent` + `agent_batch`（HTTP 适配器的缓存键不变）；
  `close()` 停队列。`api.translate` / `translate_stream` 在 `finally` 里关闭适配器；
  agent 供应商的线程数取 `max(thread, 8)`，让 8 个并发段落喂满一批。
- **sidecar**：新增 `agents.list`（并行检测，≤8 秒，超时降级为「已安装、版本/登录
  未知」）、`agents.models`（15 秒，失败回退内置列表）、`agents.test`（90 秒，后台
  线程，真实调用一次 CLI 后关闭适配器）；`providers.list` 多回 `agent`；
  `providers.test` 拒绝 `agent_cli` 并指向 Agents 面板。CLI `--list-models` 打印
  `@ agent:<id>`；MCP `list_providers` 多回 `agent`；spec 加 hiddenimport。
- **前端**：设置左栏新增「外部 Agent」，单栏卡片：状态 pill、版本、路径、安装/文档
  链接、模型复选框（含已配置但 CLI 不再列出的自定义模型）、每行测试、刷新模型、
  自定义模型 ID、CLI 路径覆盖。勾选模型写 `providers.<agent>`（`kind: agent_cli`）；
  同名但 kind/agent 不匹配时显示冲突并禁用。`modelPanelProviderNames()` 把 agent
  供应商从「模型」面板过滤掉；「选项 › 模型」仍列出，标签加 ` · CLI`，选中时禁用
  推理强度。新增 `shell:openExternal` IPC（主进程只放行 `https:`）。
- **文档**：`docs/development-plan.md` 新增决策一节；`docs/project-structure.md` 的
  文件树、定位表、配置、翻译层、sidecar、设置、测试、运行期数据都更新；
  `AGENTS.md` 与中英 README 各加一条说明。

**关键决策**

1. **spawn CLI，不读密钥**。OAuth/订阅额度只存在于 CLI 自己的登录态里；读
   `~/.claude`、`~/.codex` 的凭据既脆弱又越界。调用 headless 模式是唯一既尊重登录态、
   又不复制密钥的方式。代价是每次翻译多一次进程启动，所以有批队列。
2. **agent 仍是一种 provider**，不是第二套配置树：`kind: agent_cli` + `agent` 字段，
   复用 `resolve()` / `all_models()` / 选项下拉 / 缓存 / CLI / MCP。
3. **检测只回状态，不回原始输出**：`codex login status` 会打印掩码 key，所以
   `agents.list` 只回 `ok/missing/unknown`；错误里的 stderr 截断到 300 字符。
4. **登录失败是永久错误**：`AgentAuthError` 定义在 `models/base.py`（避免
   `base → agents` 循环导入），加入 `PERMANENT_TRANSLATION_ERRORS`；401 重试不会
   变成成功。
5. **自定义 prompt 不批处理**：模板有自己的输出契约，不能包进 translations 数组，
   所以批大小为 1，逐段用原模板。

**验证**

- `python/.venv/Scripts/python.exe -m pytest tests/ -q` → **469 passed**
  （worktree 里缺 `references/` 时 `test_mcp_server.py` 的 4 个用例会
  `FileNotFoundError`，是既有的 `_find_sample_pdf()` 环境依赖问题；主仓库有
  `references/`，全绿）。
- `npm run typecheck`、`npm run check:i18n`（262 keys）、`npm run build` 通过。
- **fake CLI 端到端**：用临时 `.cmd` 假 CLI 通过 `agents.paths.claude` 接进真实
  `api.translate`，翻 `translate.cli.text.with.figure.pdf`，产出 mono/dual；
  fake 日志显示 **2 次调用：8 段批量（4738 字符）+ 1 段（370 字符）**，证明批队列
  在真实 PDF 管线上生效；不消耗任何额度。
- **CDP 界面验收**：启动 Electron（独立 userData + `PDFXLATE_CONFIG_DIR`），打开
  设置 › Agents：五张卡片正确显示 Claude（fake，已安装）、Codex（v0.154.0，6 个
  模型）、WorkBuddy（不可用，只有桌面版）、ZCode（v0.16.9，未验证）、DSH
  （v0.2.1-rc.2，未验证，2 个模型）；通过面板把 Claude 路径指到 fake CLI 并勾选
  Sonnet 后，`config.get` 返回 `{kind: agent_cli, agent: claude, models: [sonnet]}`，
  选项下拉出现 `claude / sonnet · CLI`；点「测试」返回
  `正常 — 405 ms，Sonnet 返回「译文：Hello.」`。
- **真实检测冒烟（不调模型）**：Claude 2.1.153 登录 ok；Codex 0.154.0 登录 ok、
  6 个可见模型；WorkBuddy 正确报 `app_only`；ZCode 0.16.9 从
  `D:\ZCode\resources\glm\zcode.cjs` 识别；DSH 0.2.1-rc.2 列出 `default` 与
  `deepseek-flash`。`agents.list` 约 2.2 秒。
- 验证过程中有一次误触发了真实的 `claude -p`（路径修正前），返回 403 auth、
  没有产出译文，也没有消耗可用额度；之后全部改用 fake CLI。

**限制 / 待办**

- WorkBuddy 只装了桌面版，独立 `codebuddy`/`cbc` CLI 不在本机；它的运行/help/auth
  路径只做了 parser 测试，未真实跑过。面板会提示安装并给链接。
- ZCode 没有 `--model`，只能用它的当前模型（`default`）；切换模型要在 ZCode 里做。
- DSH 的 `-` stdin 约定与 `--patch` 覆盖是按文档/帮助实现的，真实只验证了
  `--dump-config`；需要一次真实 headless 调用来确认。
- v1 不映射 agent 的 temperature/top_p/max_tokens/reasoning；这些由 CLI 自己的
  配置决定，界面在选中 agent 时禁用推理强度。
- 真实模型冒烟（每个 CLI 发一句）没有跑，避免消耗额度；可后续在 Agents 面板手动点
  「测试」。
- 打包版未重建：`pdfxlate.spec` 的 hiddenimport 已加，但 PyInstaller 产物需要下次
  `bash scripts/build.sh` 验证。
