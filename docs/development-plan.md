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

独立仓库：`./`

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
    def __init__(self, profile, lang_in, lang_out, prompt=None): ...
    @abc.abstractmethod
    def translate(self, text: str) -> str: ...   # 必须原样保留 {vN}
    def build_messages(self, text) -> list[dict]: ...

_REGISTRY: dict[str, type[ModelAdapter]] = {}
def register(cls): ...      # 装饰器
def get_adapter(provider): ...
```

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

Electron 通过 `--config-dir` 把 `app.getPath('userData')` 传给 Python，配置落在该目录：

```yaml
default_profile: deepseek
profiles:
  deepseek:
    provider: openai_compat
    base_url: https://api.deepseek.com/v1
    api_key: ${DEEPSEEK_API_KEY}      # 支持环境变量展开
    model: deepseek-chat
    reasoning_effort: high
    temperature: 0                    # 默认 0：原项目注释「随机采样会打断公式标记」
    stream: false
    extra_body: {}
  local_vllm:
    provider: openai_compat
    base_url: http://127.0.0.1:8000/v1
    model: Qwen/Qwen3-32B
    extra_body: {chat_template_kwargs: {enable_thinking: true}}
translation: {lang_in: en, lang_out: zh, thread: 4, mode: fast}
layout: {backend: auto}               # auto | cpu | cuda | dml
cache: {enabled: true}
```

pydantic 校验，启动即报错而非跑到一半失败。

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
`ping` / `config.get` / `config.save` / `profiles.list` / `assets.status` /
`assets.download` / `translate.start` / `translate.cancel`

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

## 六、Electron 外壳

**主进程**：创建窗口（`contextIsolation: true`、`nodeIntegration: false`）、spawn sidecar、退出时 kill 子进程、原生文件对话框、`shell.showItemInFolder` 打开输出目录。

**界面**（原生 HTML/CSS/TS，无前端框架）：

1. 拖拽 / 选择 PDF
2. profile 下拉 + 可编辑 base_url / api_key / model / 推理强度 / extra_body
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

**注意**：新项目位于仓库根目录，**不修改**现有 PDFMathTranslate 仓库任何文件（仅作只读参考）。
