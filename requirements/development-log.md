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


