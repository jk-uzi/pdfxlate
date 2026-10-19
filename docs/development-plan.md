# 方案：跨平台桌面版 PDF 文献翻译工具

> 本文件是项目的长期开发方向：架构决策、移植对照、协议设计、阶段划分与风险。
> 具体代码结构见 [project-structure.md](project-structure.md)，
> 实现进度见 [../requirements/development-log.md](../requirements/development-log.md)。

## 一、架构决策

**Electron (TypeScript) + Python sidecar**

- **TS 层**：渲染进程界面、Electron 主进程、IPC、原生文件对话框、子进程管理、构建与打包 —— 你要的"全栈 JS/TS"体验在这里。
- **Python 层**：PDF 解析 / ONNX 版面检测 / 排版回填，用 PyInstaller 打成独立二进制，作为 sidecar 被 Electron 拉起。

**为什么不全栈 TS**：我核实过 `mupdf.js`（Artifex 官方 WASM 绑定），能力上**够用**——有 `addCJKFont`、`subsetFonts()`、`PDFObject.put()` 改写内容流、`graftPage` 合并。但两个硬伤：`PDFPage.toContents()` 未绑定（Python 侧"重渲染原始内容流再包裹"的核心技巧没有等价 API，得自己递归处理 XObject 重建），且 `asJSON()` 只给每行首个字符的字体（公式识别必须改用 `walk()` 回调）。这意味着要重写 `converter.py` 那 26KB 排版逻辑——项目里最难、最微妙的部分，写错的症状是公式错乱、文字溢出、双栏串行。**先用 Python 复用现成算法拿到高保真产品，把 PDF 核心的可替换边界设计干净，将来想迁移到 TS 也有接口。**

**为什么用 stdio 而非本地 HTTP**：不占端口、不触发 Windows 防火墙弹窗、父进程退出即回收、无鉴权面。

## 二、代码划分

独立仓库：`./`（与上游的完整克隆 `references/PDFMathTranslate/` 并列）

职责边界：

- **`src/`（TypeScript）** —— Electron 主进程、preload、渲染进程界面。
  负责窗口与生命周期、原生文件对话框、子进程管理、构建与打包。
- **`python/`（Python）** —— PDF 解析 / ONNX 版面检测 / 排版回填，
  打成独立二进制作为 sidecar 被 Electron 拉起。
- **`docs/`、`requirements/`、`references/`** —— 文档、开发记录、参考项目。

具体的文件树、每个模块的职责与关键符号、以及「改某个功能该动哪个文件」，
见 [project-structure.md](project-structure.md)。本文件不再重复维护结构信息。

## 三、翻译层（自研，满足"接入任何模型"）

### 基类与插件注册表

```python
# models/base.py
class ModelAdapter(abc.ABC):
    name: str                                   # 注册名 = 缓存引擎键（<20 字符）
    # ``profile`` 是拍平后的 ResolvedModel（供应商 + 模型），不是配置里的供应商本身
    def __init__(self, profile, lang_in, lang_out, prompt=None): ...
    @abc.abstractmethod
    def do_translate(self, text: str) -> str: ...  # 必须原样保留 {vN}
    def translate(self, text: str) -> str: ...     # 基类实现：缓存 + 占位符校验
    def build_messages(self, text) -> list[dict]: ...

_REGISTRY: dict[str, type[ModelAdapter]] = {}
def register(cls): ...      # 装饰器
def get_adapter(provider): ...
```

配置里供应商的 `kind` 字段就是这个注册表的键，因此「一个供应商挂多个模型」不影响
注册表——注册表按**适配器类型**索引，模型是数据。

新增任何后端 = 加一个文件 + `@register`，**PDF 管线一行不动**（原项目必须同时改 3 处分发表）。

### OpenAI 兼容适配器（覆盖绝大多数服务）

```python
kwargs = dict(model=..., messages=self.build_messages(text), stream=self.stream)
kwargs.update(self.options)            # temperature / max_tokens / top_p
if self.reasoning_effort:              # 推理强度（一等支持）
    kwargs["reasoning_effort"] = self.reasoning_effort
if self.extra_body:                    # 任意厂商私有参数透传
    kwargs["extra_body"] = self.extra_body
```

原项目 grep `reasoning` **零命中**，`extra_body` 仅在一处硬编码。新设计一次覆盖：

- `reasoning_effort`（o 系列 / 兼容实现）
- `extra_body`（如 Qwen 的 `chat_template_kwargs.enable_thinking`、vLLM 私有开关）
- 保留 `<think>...</think>` 剥离正则（推理模型会输出思考过程，必须剥掉再回填）
- 429 限流自动重试（tenacity 指数退避）
- 流式/非流式可配

覆盖 DeepSeek、Qwen、Kimi、Grok、GLM、硅基流动、vLLM、Ollama、OpenRouter 等。

### 配置文件

Electron 通过 `--config-dir` 把 `app.getPath('userData')` 传给 Python，配置落在该目录。

**一个供应商挂多个模型**：凭据与地址在供应商层配一次，模型列在它下面。多个模型
共用一个 endpoint 时不必重复填 URL 与 key：

```yaml
default_provider: deepseek
default_model: chat

providers:
  deepseek:
    kind: openai_compat
    base_url: https://api.deepseek.com/v1
    api_key: ${DEEPSEEK_API_KEY}      # 支持环境变量展开
    models:
      chat:
        model: deepseek-chat
        temperature: 0                # 默认 0：原项目注释「随机采样会打断公式标记」
        stream: false
      reason:
        model: deepseek-reasoner
        reasoning_effort: high        # 推理强度（一等支持）
  local_vllm:
    base_url: http://127.0.0.1:8000/v1
    models:
      qwen:
        model: Qwen/Qwen3-32B
        extra_body: {chat_template_kwargs: {enable_thinking: true}}
translation: {lang_in: en, lang_out: zh, thread: 4, mode: fast}
layout: {backend: auto}               # auto | cpu | cuda | dml
cache: {enabled: true}
```

pydantic 校验，启动即报错而非跑到一半失败。`AppConfig.resolve(provider, model)`
把两层拍平成适配器直接消费的 `ResolvedModel`，因此新增模型后端仍然只需一个文件 +
`@register`，翻译层与 PDF 管线都不知道「供应商」这层存在。

> 历史上配置是扁平的 `profiles:`（一个 profile = 一个完整 endpoint+model）。
> 旧的 `profiles:` 文件会在加载时**自动迁移**成上面的结构（按 endpoint+key 分组，
> 旧 profile 名成为模型别名），所以老配置不会启动失败，`--profile deepseek`
> 这类老命令也仍然可用。

## 四、PDF 管线移植

| 新文件 | 来源 | 改动 |
|---|---|---|
| `pdf/interp.py` | `pdfinterp.py` | **照搬** |
| `pdf/text_spacing.py` | `text_spacing.py` | **照搬**（纯 stdlib） |
| `pdf/ultrafast.py` | `ultrafast.py` | 照搬，`pdf-inspector` 变可选 extra |
| `pdf/ocr.py` | `ocr.py` | pooch 下载 → requests |
| `pdf/layout.py` | `doclayout.py` | babeldoc 取模型路径 → `assets.get_layout_model_path()` |
| `pdf/converter.py` | `converter.py` | **删掉顶部 24 个 translator import 与 `__init__` 分发块**，改为注入 translator 对象；排版逻辑一字不动 |
| `pdf/pipeline.py` | `high_level.py` | 去掉 docx/pdfa 分支；字体走 `assets`；`service/envs/prompt` → `translator` 实例 |

**唯一实质重构是 converter.py 的分发块**。改完后 converter 不再 import 任何厂商 SDK，原 40KB 的 `translator.py` 整个不需要。

### 缓存重写

原 `cache.py` 用 peewee 且 `init_db()` 在 **import 时执行**（副作用）。新实现用 stdlib `sqlite3`，接口保持 `get/set/add_params` 兼容。缓存键 = `(adapter_name, params_json, original_text)`，`reasoning_effort`/`extra_body` 都进 params，避免换参数命中脏缓存。

### 资源下载（替代 babeldoc）

`assets.py` 直连下载到 userData，带校验和 + 原子写入（下到 `.tmp` 再 rename，避免半截文件被当缓存）：

- **版面模型**：`https://huggingface.co/wybxc/DocLayout-YOLO-DocStructBench-onnx/resolve/main/doclayout_yolo_docstructbench_imgsz1024.onnx`（75.3 MB，已核实）。支持 `HF_ENDPOINT` 国内镜像。
- **字体**：`GoNotoKurrent-Regular.ttf`（satbyy/go-noto-universal v7.0）+ `SourceHanSerif{CN,TW,JP,KR}-Regular.ttf`（timelic/source-han-serif），按目标语言选一。
- **OCR 数据**：`tesseract-ocr/tessdata_fast/4.1.0/{code}.traineddata`

### 跨平台路径

`paths.py` 统一解析，Electron 传入覆盖：

- Windows `%APPDATA%\pdfxlate`
- macOS `~/Library/Application Support/pdfxlate`
- Linux `~/.config/pdfxlate` / `~/.cache/pdfxlate`

## 五、sidecar 协议（换行分隔 JSON）

**请求** `{id, method, params}`，实际实现的方法（见 `sidecar.py` 的 `METHODS`）：
`ping` / `config.get` / `config.save` / `providers.list` / `providers.test` /
`providers.models` / `agents.list` / `agents.models` / `agents.test` /
`assets.status` / `assets.download` / `pdf.info` / `pdf.spread` /
`translate.start` / `translate.cancel` / `mcp.snippet` / `mcp.installConfig`

> 打开输出目录、文件对话框这类操作由 Electron 主进程直接处理（`shell:reveal`、
> `dialog:pickPdf` 等 IPC 通道），不走 sidecar —— 它们本来就是 Electron 的职责，
> 让 Python 绕一圈没有意义。

**响应** `{id, ok, result | error}`

**事件**（无 id）：

- `{event:"progress", job_id, stage, current, total, message}`
  —— 目前实现实际发出的 stage 是 `assets`（下载模型/字体）与 `layout`（版面检测）
- `{event:"done", job_id, result:{outputs:[{mono, dual}]}}`
- `{event:"log", level, message}`
- `{event:"ready", version}` —— 进程启动后发一次，Electron 侧以此确认链路可用

取消：`translate.cancel` 置 `asyncio.Event`（管线已内建 `cancellation_event` 支持，直接用）。

### 外部 agent 接入（MCP）

`mcp_server.py` 把同一个 Python 核心以 **MCP 服务**暴露出去，让别的 agent 用自己的
模型、通过它来翻译。走 **stdio**（与 sidecar 同样的理由：不占端口、无防火墙弹窗、
无鉴权面、随父进程回收），因此 agent 是**启动一个子进程**，而不是连到运行中的应用。

三个决策值得记：

1. **为什么 stdio 而不是常驻 HTTP**。让运行中的应用监听 `127.0.0.1` 会带来「界面能实时
   显示进度、共用已加载模型、可复用取消」的好处，但与项目既有的「用 stdio 不用本地
   HTTP」取舍相反，且要额外处理端口与鉴权。本阶段先做 stdio：配置、缓存、下载资源
   三者本就在进程外共享，所以 MCP 服务看到的就是界面上配好的东西，独立进程的代价
   只有「不共享内存里的版面模型」与「界面看不到它的进度」。
2. **配置只读**。服务只提供 `list_providers` 这类读取工具，**故意没有**任何增删
   供应商/模型或写 API key 的工具——把 agent 指向这个服务就不可能改坏用户配置。
3. **两遍式翻译**。为了支持「用 agent 自己的模型」（MCP sampling），需要先拿到段落再
   排版，而 `converter.py` 的翻译是同步的线程池调用、改不动。利用管线的确定性做成
   收集→翻译→回填两遍，`converter.py` 一行未改，详见
   [project-structure.md](project-structure.md) 的 `pdf/two_phase.py` 一节。

**「不移植上游 MCP」这条决策已推翻**：上游的 `mcp_server.py` 用 v1 的 `FastMCP`
（v2 已删除该类，直接 import 会报 `ModuleNotFoundError`），且只做「用服务端配置的模型」
一条路。本项目自研，并额外支持 sampling——即**真正用 agent 自己的模型翻译**。

### 外部 agent CLI 作为翻译后端（Agents 设置）

MCP 是「别的 agent 用 pdfxlate 的模型」；Agents 是反方向——**pdfxlate 用别的 agent
的模型**。目标不是复制它们的密钥，而是借用用户已经登录的 CLI 与订阅额度。设置左栏
因此新增「Agents」，首批支持 Claude Code、Codex、WorkBuddy/CodeBuddy、ZCode、
DeepSeek Harness 五个 CLI。

四个决策值得记：

1. **spawn CLI，不读密钥**。OAuth/订阅额度只存在于 CLI 自己的登录态里，读
   `~/.claude`、`~/.codex` 的凭据既脆弱又越界；调用 CLI 的 headless 模式（`claude -p`、
   `codex exec`、`dsh --profile headless`、`codebuddy -p`、`zcode -p`）是唯一既尊重
   登录态、又不复制密钥的方式。代价是每次翻译多一次进程启动。
2. **仍然是 provider + model 两层**。agent 是一种 `kind: agent_cli` 的供应商，`agent`
   字段指向具体 CLI；模型别名就是 CLI 的模型 id。这样 `resolve()`、`all_models()`、
   选项下拉、缓存、CLI/MCP 全部复用，不需要第二套配置树。设置 › 模型只编辑 HTTP
   供应商，agent 供应商由 Agents 面板编辑，避免同一份配置有两个编辑器。
3. **批量翻译**。逐段起进程的代价不可接受（一篇论文 250+ 段），所以适配器内建一个
   批队列：最多 8 段 / 12000 字符（argv 传 prompt 的 ZCode 是 8000）/ 首段到达后
   0.2 秒触发一次，一次进程翻一批。CLI 被要求返回
   `{"translations": [...]}`；解析失败先严格重试，再拆单段，最后才报错。用户自定义
   prompt 时批大小固定为 1，保证自定义语义不变。
4. **检测不自动探测登录，也不自动测试模型**。`detect_agent()` 只做路径解析 +
   版本探测；DSH/ZCode 的登录态不再自动探测，安装即显示「已安装」。「测试」按钮
   是唯一的真实模型调用，完全由用户手点；成功后才显示「可用」，失败回到「已安装」。
   可执行文件按「`agents.paths` 覆盖 → 环境变量 → PATH → 平台标准目录 /
   Windows 卸载注册表」解析；`agents.paths` 让用户能指向非标准安装位置。
5. **快照优先，不每次启动重检**。`agents.list` 的成功结果写入配置目录的
   `agents-snapshot.json`（安装/模型元数据，不含凭据，带 `paths` 签名）；启动/打开
   面板优先读它，不 spawn CLI、不请求 WorkBuddy 网关。只有首次无快照、用户点
   「重新检测」或「刷新模型」时才 live 并更新快照。首次升级后的第一次仍会 live 一次。

v1 的限制（写进 UI 提示与文档）：

- **WorkBuddy** 独立 `codebuddy`/`cbc` 存在时走 CLI；只有桌面版时走桌面版
  auth/catalog/HTTP（`/v3/config` + `/v2/chat/completions` SSE）。HTTP 默认直连，
  绕开系统里可能失效的代理；需要代理时显式设 `WORKBUDDY_PROXY`。token 只在引擎
  内存，不写 WorkBuddy 的 auth 文件；卡片显示「桌面版 · 已登录/未登录」，登录态
  只在快照/手动刷新时读取，不再每次启动读 key。
- **ZCode** 的 CLI 没有 `--model`：目录读取 `~/.zcode/v2/provider_config.json`，
  当前模型读取 session DB；面板列出 enabled 模型，当前模型可勾选、可翻译。非当前
  模型可通过 ZCode 官方 `app-server --stdio` 的 `provider/testModelConnectivity`
  手动测试：临时 workspace、无 session、不改当前模型/config/credentials/DB；只显示
  连接结果，不提供复选框、不把整张卡标成「可用」。要用非当前模型翻译，仍需在 ZCode
  内切换（建议发一条消息让 session DB 记录）后回面板「重新检测」；不伪造切换，不写
  ZCode 的 config/DB。
- **DSH** 使用 `headless` profile；模型目录来自桌面 profile
  `~/.dsh/profiles/desktop/cordis.patch.yml`（复合 `provider/model`），非默认模型
  通过临时 `--patch` 覆盖 `llm-pi-ai` + `agent-default-model`，不修改用户的 profile；
  `apiKeyEnv` 的值从 `~/.dsh/.credentials.yaml` 只注入子进程 env。
- agent 的 temperature/top_p/max_tokens/reasoning 参数 v1 不映射，由 CLI 自己的配置
  决定；这些供应商的「推理强度」控件在界面上禁用。
- agent CLI 视为本地可信程序；翻译文本会发给它背后的模型，额度与条款由该 CLI 承担。

实现落在 `python/pdfxlate/agents/`（定义 / 检测 / 模型列表 / 快照 / 进程运行时 /
批队列）、`models/agent_cli.py`（适配器）与 `src/renderer/` 的 Agents 面板；sidecar
新增 `agents.list` / `agents.models` / `agents.test`。参考实现是本地安装的 Open Design
0.25.0（其 `AGENT_DEFS` / `agentId` / `agentModels` 结构），但代码全部自研，不 import
它的运行时。

2026-10-19 的模型目录扩展：DSH 已从桌面 profile 列出 25 个模型，并可通过临时
`--patch` 选择任意 `provider/model`；ZCode 已列出 `provider_config.json` 的 30 个
enabled 模型并标记当前模型（当前模型可翻译；非当前模型可通过官方 app-server 手动
测试，切换翻译仍需在 ZCode 内做）。WorkBuddy
桌面版的 auth 解密、`/v3/config`/`/v2/enterprises/personal/models` 目录与
`/v2/chat/completions` SSE 翻译已接入（只在没有独立 `codebuddy`/`cbc` CLI 时启用），
卡片显示桌面版登录状态与 credits，不再只提示安装 CLI。HTTP 默认直连（避开失效的
系统代理，`WORKBUDDY_PROXY` 可显式覆盖）。DSH/ZCode 不再自动探测登录状态，安装即
显示「已安装」；「测试」成功后 pill 变「可用」，失败回到「已安装」，不再出现
「未验证」。`agents.list` 优先读本地快照，启动不再每次重新检测。

## 六、Electron 外壳

**主进程**：创建窗口（`contextIsolation: true`、`nodeIntegration: false`）、spawn sidecar、退出时 kill 子进程、原生文件对话框、`shell.showItemInFolder` 打开输出目录。

**界面**（原生 HTML/CSS/TS，无前端框架）：

1. 拖拽 / 选择 PDF
2. **供应商下拉 + 模型下拉**（两级），各自可增删；供应商级可编辑 kind / base_url /
   api_key（共享），模型级可编辑 model / 推理强度 / 温度 / extra_body
3. 模式开关：fast（ONNX 版面检测）/ ultrafast
4. 首次运行引导下载模型和字体（带进度条）
5. 实时进度：解析 → 版面检测 → 翻译（第 N/M 段）→ 排版 → 保存
6. 完成后打开输出目录，展示 mono / dual 两个结果

## 七、依赖对比

**Python 核心**（约 13 个）：`pymupdf<1.25.3`（钉死）、`pdfminer-six==20250416`（**必须钉死**，用了大量内部 API）、`numpy`、`onnx`、`onnxruntime`、`opencv-python-headless`、`openai>=1.0`、`requests`、`tqdm`、`tenacity`、`pyyaml`、`pydantic`、`rich`

**可选 extras**：`ultrafast`（pdf-inspector）、`cuda`（onnxruntime-gpu）

**砍掉**：babeldoc（连带 scipy/pandas/scikit-image/rapidocr/pyclipper 等一大批）、gradio、gradio_pdf、flask、celery、redis、ollama、xinference-client、deepl、azure-ai-translation-text、tencentcloud-sdk-python-tmt、peewee、pikepdf、pooch

> 注：原项目自带但**未使用**的 `fontTools` 曾计划一并砍掉，实际实现发现
> PyMuPDF 的 `Document.subset_fonts()` 运行期需要它，且源码里没有直接 import，
> 缺了字体子集化会直接失败，因此保留在依赖里。

Python 依赖从 **121 个降到约 14 个**。

**打包**：PyInstaller 用 **onedir 模式**（不用 onefile——onnxruntime/opencv 这类原生库在 onefile 下启动慢且临时解压易出错），产物作为 Electron 的 `extraResources` 打进安装包。

## 八、实施阶段

**阶段 1：Python 核心骨架 + 资源层**
建仓库、pyproject、paths.py、config.py、assets.py。验证：路径解析正确；模型与字体下载成功且重复调用不重下。
> **字体必须在此阶段单独验证**（生成含中文的测试 PDF 看渲染是否正常）——这是最大风险点，不要拖后。

**阶段 2：翻译层**
`models/base.py` + `openai_compat.py` + 注册表 + cache.py。验证：先用假 adapter（照抄原项目测试的 `AutoIncreaseTranslator`）跑通缓存；再用真实 API 翻译，确认 `{v0}` 占位符原样保留、`reasoning_effort` 生效、`<think>` 被剥离。

**阶段 3：PDF 管线移植**
照搬 interp/text_spacing/ultrafast/ocr，改造 layout/converter/pipeline。验证：用原仓库自带的 `test/file/translate.cli.plain.text.pdf` 跑通端到端，产出 mono + dual。

**阶段 4：sidecar 协议 + CLI**
`sidecar.py` 的 JSON 行协议、取消、进度事件；`cli.py`。验证：管道喂 JSON 请求，确认响应与事件格式正确。

**阶段 5：Electron 外壳**
主进程、preload、IPC、界面。构建用 `tsc` 编译 + 一个静态资源拷贝脚本，
不引入 electron-vite（本项目没有复杂的前端打包需求）。
验证：`npm run dev` 拉起完整链路，实测拖拽上传 → 翻译 → 下载。

**阶段 6：打包**
PyInstaller spec + electron-builder 三平台配置。验证：在 Linux 打出 AppImage 并实机运行；Windows/macOS 配置写好，需在对应平台或 CI 验证。

**阶段 7：端到端验收**
拿一篇真实双栏带公式的 arXiv 文献跑 fast 和 ultrafast，用 visual-judge 检查排版质量（公式是否错乱、双栏是否串行、图表是否被误翻），确认 mono/dual 都正确。

## 九、风险与对策

1. **字体回填是最大风险**。原项目靠 babeldoc 的 `get_font_and_metadata` 提供字体路径与元数据；自建下载后字体不对会导致中文渲染成空白方块。对策：阶段 1 就单独验证字体渲染。
2. **pdfminer 内部 API 极脆**。`interp.py` 用了大量非公开 API，换版本就崩。对策：`pdfminer-six==20250416` 硬钉并在 pyproject 注明原因。
3. **PyInstaller 打包 onnxruntime/opencv/PyMuPDF 需手工处理**（hidden imports + 二进制收集），三平台各调一次。对策：spec 显式声明，阶段 6 留足时间。
4. **代码签名**。macOS 未签名会被 Gatekeeper 拦，Windows 未签名触发 SmartScreen。对策：开发期先本地运行验证功能；签名/公证作为独立后续事项（需证书）。
5. **体积**。Electron + PyInstaller 后每平台约 300MB+。这是桌面跨平台 + 双运行时的固有成本，但用户侧只需装一次，不再需要配 Python 环境。
6. **ONNX 元数据字段**。模型必须带 `stride`/`names`，否则 `ast.literal_eval` KeyError。对策：下载后校验元数据。
7. **无 babeldoc 的排版退化**。原项目 `--babeldoc` 是另一条更优排版路径，不移植。对策：只提供 fast（ONNX）与 ultrafast 两条路径，文档写清差异。
8. **许可证**。原项目与 mupdf.js 均为 AGPL-3.0；本方案用 PyMuPDF（同为 AGPL）。若将来要闭源分发需重新评估或购商业授权。

## 十、交付物

可独立分发的跨平台桌面应用：拖入 PDF → 选模型 → 出双语对照 + 纯译文两个 PDF。支持任意 OpenAI 兼容端点（自定义 base_url / api_key / model / 推理强度 / 任意私有参数），另附 CLI 与 Python API 两个入口用于调试和批处理。

**注意**：新项目位于仓库根目录（与上游克隆 `references/PDFMathTranslate/` 并列），**不修改**现有 PDFMathTranslate 仓库任何文件（仅作只读参考）。
