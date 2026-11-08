# 内存占用诊断与技术栈选型建议（2026-11-08）

**问题**：pdfxlate 运行太占内存，考虑换技术栈，允许混合架构，不考虑工作量，要好用且功能不减。

**方法**：本机实测（Windows 11，Python 3.12.14，onnxruntime 1.31.1，Electron 33，pdfjs-dist 4.10.38）
+ 6 路并行外部调研 + 对抗式核查。所有数字都用 **USS（独占物理内存）** 或明确标注的 RSS 口径；
原始日志留在 [mem-probe/](mem-probe/)。

> 口径说明：Windows 上 `psutil` 的 `rss` 是「工作集」，包含与其他进程共享的 DLL 页，
> 把多进程相加会重复计数（实测同一份 Electron 空白窗口 RSS 合计 290 MB、USS 只有 69 MB）。
> 本文除特别标注外一律用 USS。任务管理器「内存」列显示的也是私有工作集一类口径。

---

## 一、结论先说

**换技术栈解决不了这个项目的内存问题。** 实测证据：

| 组件 | 实测 USS | 占比 |
|---|---|---|
| Electron 外壳（空白窗口，4 进程） | 69 MB | 小 |
| Electron + 15 页中文 PDF 预览（内置查看器） | 185 MB | 中 |
| Electron + 同一份 PDF 用 pdf.js（缓存 3 页） | 111 MB | 中 |
| Python 引擎 fast 模式跑 1.8 MB 论文（15 页） | **峰值 440 MB / 作业后驻留 337 MB** | **主因** |
| Python 引擎 ultrafast 模式跑同一份论文 | 峰值 173 MB | 对照 |

**内存大头在 Python 引擎的 ONNX 版面检测，不在 Electron，也不在 PyMuPDF。**
同一份文件、同一台机器，只把版面检测从 ONNX 换成免模型的启发式路径，
峰值从 440 MB 掉到 173 MB —— 差 267 MB。这是唯一一个能解释「为什么这么占内存」的量级。

而 ONNX 那 267 MB 里，最大的一块根本不是模型权重（71.8 MB），而是
**onnxruntime 的 CPU 内存 arena**：一行开关就能消掉约 227 MB。

三条最省事的改动（都不改语言、不砍功能）实测效果：

| | 作业后驻留 | 峰值 |
|---|---|---|
| 现状（arena 开、float64 掩膜） | 370 MB | 734 MB |
| 关 arena + 掩膜改 uint8 | **248 MB** | **613 MB** |
| 真实论文（1.8 MB / 15 页）现状 | 337 MB | 440 MB |
| 真实论文，同样两项改动 | **219 MB** | **329 MB** |

（46.5 MB / 30 页扫描件的数字；作业后驻留降 33%，峰值降 16%。）

---

## 二、内存到底花在哪：分阶段实测

46.5 MB / 30 页扫描件，fast 模式，逐阶段 USS：

```
imports（numpy/cv2/onnxruntime/pymupdf/openai…）      70 MB
+ ONNX 会话加载                                        170 MB   (+100)
+ 输入原始字节（46.5 MB）                              218 MB   (+48)
+ doc_en 序列化进 BytesIO                              264 MB   (+47)
+ doc_zh 序列化进 BytesIO（fp）                        311 MB   (+47)
+ translate_patch（arena + 掩膜 + 逐页推理）           545 MB   (+234)  ← 最大一块
+ dual 装配（insert_file + move_page）                 545 MB   (+0)
+ mono 序列化（46.5 MB）                               592 MB   (+47)
+ dual 序列化（93.1 MB）                               685 MB   (+93)
```

### 2.1 ONNX Runtime 的 CPU arena（最大单项，约 227 MB）

`python/pdfxlate/pdf/layout.py:104-106` 只设了 `graph_optimization_level`，
**没有设 `enable_cpu_mem_arena`**，而 onnxruntime 默认开启它。实测：

| 配置 | 加载 | 推理 3 次后 | 峰值 |
|---|---|---|---|
| arena 开（现状） | +90 MB | **+235 MB** | 400 MB |
| arena 关 | +81 MB | **+6 MB** | **174 MB** |
| arena 开 + 单线程 | +80 MB | +233 MB | 399 MB |
| arena 关 + 单线程 | +80 MB | +7 MB | 174 MB |

线程数不影响结论，只有 arena 影响。代价是推理变慢：单次 1024² 推理
0.6 s → 1.1 s（偶发 3.7 s 长尾）。**这是纯取舍，不是缺陷。**

arena 的行为是「第一次/第二次推理时按高水位一次性预留，之后不还」，
所以它抬高的既是峰值也是**作业结束后的驻留**（370 → 248 MB）。

### 2.2 逐页掩膜的 float64（约 57–100 MB，随页数线性增长）

`pipeline.py:254` 的 `box = np.ones((pix.height, pix.width))` 默认是 **float64**，
而且存进 `layout` 字典**整篇不释放**，`converter.py:355/360` 逐字符去查它。

实测（默认 72 dpi 渲染，A4 → 595×842）：

| dtype | 每页 | 30 页 |
|---|---|---|
| float64（现状） | 3.82 MB | **115 MB** |
| uint8 | 0.48 MB | 14 MB |

掩膜取值只有 `0`（保留）和 `i+2`（第 i 个区域），实测一页最密集的排版
（130 个文本块、360 个 item）最大标签也只有 7，**uint8 上限 255 完全够用**。

实测累积曲线（30 页扫描件，持有全部 float64 掩膜）：213 → 296 → 325 → 385 → **444 MB**；
转成 uint8 立刻降到 **348 MB**。

### 2.3 全文档内存副本（约 4–6 份，随文件大小线性增长）

`pipeline.py:476-482` 和 `:530-532` 的流程：

```python
doc_en = Document(stream=stream)          # ① 原始字节
stream = io.BytesIO(); _save_document(doc_en, stream)   # ② doc_en 整份序列化
doc_zh = Document(stream=stream)          # ③
fp = io.BytesIO(); _save_document(doc_zh, fp)           # ④ doc_zh 整份序列化
...
mono_bytes = serialize(doc_zh)            # ⑤ 46.5 MB
dual_bytes = serialize(doc_en)            # ⑥ 93.1 MB（both 模式）
```

实测（46.5 MB 输入）：① 46.5 + ② 46.5 + ④ 46.5 + ⑤ 46.5 + ⑥ 93.1 ≈ **280 MB 纯字节副本**。
单测确认 `Document(stream=bytes)` 是惰性近零拷贝（+0 MB），
但每次 `save()` / `tobytes()` 都是一整份复制。

**改法**：`fp` 与两个输出改成直接写临时文件 / 目标路径，而不是 `BytesIO` + `bytes`。
`pipeline.translate()` 反正马上要 `Path.write_bytes()`，中间那次 `bytes` 是纯浪费。

### 2.4 OCR 路径的 300 dpi 位图（瞬时，不累积）

`pipeline.py:386` 对无文字层的页做 `get_pixmap(dpi=300)`：
单页 A4 @300dpi = 2480×3509 = **24.9 MB**，然后 `pdfocr_tobytes()` 还会再复制一份。
好消息是代码用 `with Document(...)` 逐页释放，所以是瞬时峰值而非累积。
但扫描件密集时会推高整体峰值，值得知道。

### 2.5 依赖 import 本身（约 70 MB）

| 依赖 | import 后增量 | 备注 |
|---|---|---|
| numpy | +12.8 MB | 必需 |
| cv2 | +3.1 MB | **全项目只用 2 个函数**（resize / copyMakeBorder） |
| pymupdf | +35.1 MB | 必需 |
| onnxruntime | +17.0 MB | 必需（fast 模式） |
| openai | **+22.2 MB** | 纯 Python，可延迟到真正调用时 |
| mcp | **+24.0 MB** | 纯 Python，只有 `--mcp` 才需要 |

`openai` 和 `mcp` 合计 46 MB 是**纯 Python 包**，延迟 import 就能省掉——
但注意 sidecar 是长驻进程，一旦用过一次就不会再降下来，收益主要是启动阶段。

`onnx`（19.6 MB 磁盘）只用来读两个 metadata 字段（`layout.py:98-101`），
可以用更轻的方式读，省的是安装包体积不是内存。

---

## 三、技术栈：逐项评估

### 3.1 Electron → Tauri：**不建议**（对内存没有帮助）

Windows 上 Tauri 用的是 **WebView2，也就是 Edge 的 Chromium 内核**。
第三方 CI 实测（[benchmarks.json](https://github.com/Elanis/web-to-desktop-framework-comparison/blob/main/runner/benchmarks.json)）：
空白窗口进程树 **Electron 279 MB vs Tauri 312 MB** —— Tauri 反而略高。

Tauri 团队成员在 [issue #5889](https://github.com/tauri-apps/tauri/issues/5889) 里直接说了：
不用原生功能时，「你其实是在测 WebKit 对 Chromium」——内存差异来自 Web 内核，不是 Rust 主进程。
Tauri 维护者本人也称官方 benchmark「只能当 smoke test」。

Tauri 真正的优势是**二进制体积**（3.19 MB vs 374 MB，约 117 倍），不是运行内存。

对本项目还有两个额外的硬伤：
- **file:// 加载 PDF 不可用**（Tauri 的源策略限制，须改 `convertFileSrc`/asset 协议），
  而现有预览正是靠 `iframe src="file:///…"` + Chromium 内置查看器。
- **sidecar 进程树清理在 Windows 上不可靠**（[issue #14360](https://github.com/tauri-apps/tauri/issues/14360) 仍 open），
  PyInstaller 子进程可能残留。

### 3.2 PDF 预览：Chromium 内置查看器 → pdf.js：**建议做，主要为了可控性**

同一份 **15 页中文 PDF**、同一个 Electron 33、USS 口径的公平对比：

| 方案 | USS 合计 | 进程数 |
|---|---|---|
| Chromium 内置查看器（`iframe file://`） | **184.8 MB** | 6 |
| pdf.js 4.10.38，缓存 3 页 | **111.3 MB** | 4 |
| pdf.js 4.10.38，缓存 6 页 | 129.2 MB | 4 |

省 55–73 MB，且**缓存页数直接决定内存**（3 页 → 6 页多 18 MB）。
按 A4@150dpi 每页约 8.7 MB 估算，这个斜率是合理的。

> 修正说明：我第一版报告用的是「pdf.js 极简测试页 vs 内置完整查看器」，
> 那不是同口径（内置查看器自带工具栏、缩略图、文本层）。上表已换成两边都是
> 真实 15 页中文文档。pdf.js 侧是 core API 自建视图（无工具栏），
> 所以这个差值里有「少了工具栏」的贡献，不能全算作渲染内核的差异。

**真正的收益是可控性，不是内存**：
1. **能拿到精确滚动位置与页码** → 可以原生实现左右双栏同步滚动，
   直接删掉 `pdf/spread.py` 这个「把两页合成派生 PDF」的 workaround。
   这是架构性的：内置查看器是独立的 remote WebContents，父页面根本拿不到它的滚动状态。
2. **文本层** → 支持选中、搜索、复制。
3. **可控渲染** → 缓存页数、`maxCanvasPixels`、`page.cleanup()` 都在手里，
   大文档不会失控。stock viewer 有 **10 页缓存下限**（`PDFPageViewBuffer`），
   自建视图才能压到 2–3 页。

**已验证的前提（都是我本机实测的）**：
- Electron 的 `file://` 页面里 **Web Worker 可用** —— pdf.js 的硬性要求；
- `file://` 下 **cmaps 能正常加载**，中文 PDF 文本提取正确
  （实测输出「注意力机制是全部所需本文提出一种新的转换序列模型…」）。
  但**路径结尾的斜杠不能少**：我第一版就因 `cmaps` 少个 `/` 导致
  `Unable to load binary CMap`、文本层为空——这个坑很隐蔽，且只影响 CJK。
- 仍需配置 `worker-src` 与 `script-src 'wasm-unsafe-eval'`（pdf.js 4+ 的 WASM 图像解码）。

**代价要认**：要做工具栏/缩放/懒加载，打包 worker + cmaps + standard_fonts；
pdf.js 本身有已知的内存泄漏边缘案例（[#13112](https://github.com/mozilla/pdf.js/issues/13112)、
[#20198](https://github.com/mozilla/pdf.js/issues/20198)）；
CJK 文本层有历史 bug（复制时可能缺空格/断行）。
**建议先做 spike**：两个 pdf.js 实例、缓存上限 3 页，按 USS 复测并确认双栏同步手感，达标再替换。

> 注意：pdf.js 是 Apache-2.0，与项目 AGPL-3.0 兼容。

### 3.3 Python → Rust（PDF 层）：**不建议为省内存而做；为「去 Python」才值得**

逐项核查（每条都有 crates.io / GitHub 源码出处）：

| 需求 | mupdf-rs | pdfium-render | lopdf |
|---|---|---|---|
| 渲染位图 | ✅ `to_pixmap` | ✅ | ❌ |
| 内容流改写 | ✅ `insert_contents`/`write_stream_buffer` | ❌ **无原始内容流 API**（只能对象模型重建） | ⚠️ 对象层 |
| 插入字体 | ✅ `insert_font` | ✅ `load_true_type_from_bytes` | ⚠️ 整份嵌入 |
| 字体子集化 | ⚠️ 只有 FFI `pdf_subset_fonts`（**标 EXPERIMENTAL**） | ❌ **完全没有** | ❌ |
| 页面 select/merge | ✅ | ✅ | ⚠️ |
| 逐字符 bbox + 字号 | ✅ structured text（但**未暴露 font**） | ✅ `GetCharBox`/`GetFontSize` | ❌ |
| 行/块结构 | ✅ block→line→char | ❌ 需自己聚类 | ❌ |
| Windows 预编译 | ❌ 需 MSVC + libclang 自编 | ✅ pdfium-binaries | ✅ |
| 许可 | AGPL-3.0（与项目一致） | BSD-3 | MIT |

**关键结论：没有任何 Rust 库等价于 pdfminer.six 的 `LTPage`/`LTChar` 对象树。**
`converter.py`（699 行）+ `interp.py`（366 行）那套基于 LTChar 的逐字符掩膜判定，
要么保留 Python + pdfminer，要么在 Rust 里**重写**（不是平移），
而且因为 bbox 口径不同（pdfminer 左下原点 vs MuPDF 左上原点）会产生可观察的行为漂移。

**换 PDF 引擎也不省内存**：PyMuPDF 的原生库约 33.6 MB（`_mupdf.pyd` 13.0 + `mupdfcpp64.dll` 20.6），
换成 Rust + MuPDF 静态链接是同一量级。

**混合架构的净效果是「增加」内存**：Python sidecar + Rust 进程共存时，
两套 ORT、两套 PDF 库、两套打包链路都要各占一份。只要还有页面渲染 / 文本抽取 / 写回在 Python，
Python 就删不掉。

### 3.4 ONNX 推理迁 Rust：**伪命题**

`ort` crate 只是**同一份 Microsoft ONNX Runtime C++ 引擎**的薄封装，
权重加载、BFC arena、memory-pattern 缓存都发生在同一个引擎里。
换成 Rust 不改变任何一项。

而且 `ort` 的高层 `SessionBuilder` **没有暴露** `enable_cpu_mem_arena`
（需要走 `ort-sys` 的 FFI 或 EP option），也就是说**在 Rust 里反而更难做那个省 227 MB 的开关**。

（唯一可能真正改变内存的是纯 Rust 后端如 `tract`，但它对 YOLO 检测头/后处理的算子覆盖、
速度与数值一致性都没有公开验证，属于待评估而非推荐。）

### 3.5 模型量化：**值得做，但需要自建评测**

模型 18,785,902 个参数：fp32 = 71.7 MB / fp16 = 35.8 MB / **int8 = 17.9 MB**。
量化能同时砍**会话常驻、磁盘、推理中间张量**三块，是与「关 arena」同级的杠杆。

但 ONNX Runtime 官方明确说量化不是无损变换，而 DocLayout-YOLO 的
int8/fp16 精度影响**没有任何公开实测**——必须自己建校准集和评测才能拍板上线。
另外 ONNX Runtime 会额外在模型旁生成一份 `.optimized` 缓存（再占 71.8 MB 磁盘）。

### 3.6 同类产品的做法（横向参考）

- **PDFMathTranslate（上游）**：Python + PyMuPDF，整份文档常驻、逐页渲染整页位图送 ONNX，
  和本项目同构。维护者估计需 4 GB、建议 8 GB 内存，有用户报告 200 页文件吃满内存。
  **按页分片是 2.0 才加的能力**——说明这是这类工具的共性瓶颈，不是本项目的实现问题。
- **BabelDOC**：已把解析/写出迁到 Rust（pyo3，8 个 crate），但仍是「整份解析 + 可选按页分片、
  串行、每片清理」的粗粒度批处理，不是流式；且内置峰值内存监控。
- **Zotero 7**：用 pdf.js 在 Web Worker 里解析，开发者实测 PDF 队列峰值约 4 GB、连开 20 份近 5 GB。
- **Open PDF Studio**（Tauri + PDFium 独立 worker）：极端页峰值从 5.16 GB 降 74%。

结论：**「流式低内存」在同类里也没有现成答案**，大家都在「按页分片 + 独立进程」这条路上。

---

## 四、建议的路线（按收益 / 风险排序）

### 第 0 步：先量，别猜

在 `layout.py` / `pipeline.py` 加几行 USS 采样（或复用 [mem-probe/](mem-probe/) 里的脚本），
在真实论文上跑一遍，确认你机器上的数字与本文量级一致。**没有基线就没法验证任何改动。**

### 第 1 步：三行改动，零功能损失（预期 -25% 峰值、-33% 驻留）

1. **`layout.py:104` 之后加一行**：`sess_options.enable_cpu_mem_arena = False`
   —— 省约 227 MB，代价是推理慢约 2 倍（0.6 s → 1.1 s/页）。
   建议做成配置项（如 `layout.mem_arena: auto|on|off`），大内存机器可以换速度。
2. **`pipeline.py:254` 改 dtype**：`np.ones((h, w), dtype=np.uint8)`
   —— 30 页省约 100 MB，实测标签最大值 7，uint8 绰绰有余。
3. **`pipeline.py:530-532` 与 `:594-602` 改直写盘**：`fp` 与 `mono/dual` 用临时文件 / 目标路径，
   不用 `BytesIO` + `bytes` —— 省约 3N（N = 文件大小），扫描件场景可省 100+ MB。

### 第 2 步：模型生命周期（预期 -100 ~ -170 MB 常驻）

`ModelInstance.value` 一旦加载**永不释放**（`api.py:264-274` 只赋值，没有置 None 的路径）。
改成「任务开始加载、任务结束释放」（实测 `del session` 后内存确实回到基线，
三轮无泄漏；重载约 0.7 s）。**这是「打开应用不占内存」的关键。**

### 第 3 步：预览换 pdf.js（-55 ~ -73 MB，并删掉 spread.py）

同时删掉 `pdf/spread.py` 的 workaround，用 pdf.js 的滚动位置做原生双栏同步。
这是唯一一项「省内存的同时功能变多」的改动。

**做法很重要**：不要直接把 stock viewer 塞进 iframe（它有 10 页缓存下限，
还多背一整套工具栏 UI），而是用 pdf.js core API 自建视图、只保留可视的 2–3 页。
**先做 spike 验证双栏同步手感再全量替换。**

### 第 4 步：进程隔离（兜底，非必需）

把每个文件的翻译放进独立 worker 子进程，任务结束即回收。
实测多文件顺序处理**本来就不累积**（369 → 373 → 380 MB），所以隔离的价值主要是
「让峰值真正归还给 OS」，而不是修泄漏。

**注意**：worker 的 stdout 必须重定向，否则会污染 sidecar 的 stdio JSON 行协议。
PyInstaller onedir 下还需要 `multiprocessing.freeze_support()`。

### 不建议做的

- **换 Tauri**：不省内存，还要重写预览与 sidecar 生命周期。
- **PDF 层迁 Rust**：不省内存，且必须保留 Python 做版面分析，净增一套运行时。
- **ONNX 迁 Rust**：伪命题，还更难关 arena。

### 可以单独评估的

- **模型量化 int8**（71.7 → 17.9 MB）：收益大，但**必须先自建精度评测**。
- **去掉 cv2**（只用了 2 个函数）：省 112 MB 磁盘 / 3 MB 内存，收益小，属打包优化。
- **延迟 import `openai` / `mcp`**：省约 46 MB，但只对启动阶段有效。

---

## 五、如果一定要「重构」，怎么做才对

你说允许混合架构、不考虑工作量。那么**唯一值得的混合架构**是：

```
Electron（壳 + pdf.js 预览）
    └── sidecar 进程（Python：pdfminer 版面分析 + PyMuPDF 写回）
            └── 可选：ONNX 推理放进短生命周期子进程（任务结束即退出）
```

理由：
- **pdf.js** 让预览从 185 MB 降到 111 MB（缓存 3 页），并且换来双栏同步/搜索/可控渲染；
- **ONNX 子进程化** 让 170 MB 的模型会话在不用时完全不占内存，
  且不会把 arena 的 227 MB 常驻带进主进程；
- **Python 必须保留**——除非你接受重写 1,065 行排版逻辑并承担行为漂移，
  而这恰好是项目里最难、最微妙的部分（`docs/project-structure.md` 自己也是这么写的）。

**不要做的事**：为了「技术栈现代化」而把 Python 换成 Rust。
那是把一个「已实测能修好」的内存问题，换成一个「未知量级 + 重写最难的代码」的问题。

---

## 六、本次实测的局限

- 只测了 **Windows 11**；macOS / Linux 未测（Tauri 在 macOS 用 WebKit，结论会不同）。
- 测试 PDF 是合成的（1.8 MB 真实论文形态 / 46.5 MB 扫描件形态 / 100 页长文），
  **不是真实的 arXiv 论文**——真实论文的图片密度、公式密度会改变绝对值，但不改变归因。
- 用假 translator（正则替换）跑管线，**不含真实网络请求**，所以测的是「计算 + 内存」而非「等待」。
- `assets.ensure_layout_model()` 下载的模型是 71.8 MB（文档里写的 75 MB 是旧记法）。
- 未测 PyInstaller 打包后的基线（本机没有 `dist/`），打包版会多一份 Python 运行时。
- 外部调研的数字均带 URL；本机实测数字均可在 [mem-probe/](mem-probe/) 的日志里复核。
- **pdf.js 侧是 core API 自建视图，没有工具栏/缩略图**；内置查看器是完整 UI。
  所以 185 → 111 MB 的差值里含「少了工具栏」的贡献，不全是渲染内核差异。
  若要严格对等，pdf.js 侧还得补上工具栏再测一次。
- pdf.js 的 worker 在 `file://` 下实测可用，但这是 Electron 特有的宽松行为；
  标准浏览器里 `file://` 是 opaque origin，pdf.js 会退回 fake worker（阻塞 UI）。
  若将来要迁自定义协议（Electron 官方推荐），这条路径会更稳。
- 未在真实 arXiv 论文上验证 pdf.js 的 CJK 文本层选中/复制行为
  （公开 issue 里有已知的断行/空格问题）。
