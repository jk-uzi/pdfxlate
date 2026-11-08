# 内存测量脚本与基线

这份目录是 [../memory-diagnosis-2026-11-08.md](../memory-diagnosis-2026-11-08.md) 的
可复现证据：`.log` 是当时的原始输出，`.py` 是复跑用的脚本。

## 复跑

```bash
# 1) 造两份合成样本（15 页文本论文 / 30 页扫描件）
<venv>/python requirements/mem-probe/make_fixtures.py .

# 2) 跑一次完整的 translate_stream，打印分阶段 USS
<venv>/python requirements/mem-probe/probe.py memtest-paper.pdf fast
<venv>/python requirements/mem-probe/probe.py memtest-scan.pdf fast
<venv>/python requirements/mem-probe/probe.py memtest-paper.pdf ultrafast

# 3) 跑 sidecar 真正走的那条路（api.translate，成品写盘、任务结束释放模型）
<venv>/python requirements/mem-probe/probe.py memtest-paper.pdf fast app
<venv>/python requirements/mem-probe/probe.py memtest-scan.pdf fast app
```

`probe.py` 用假 translator（正则替换），不发网络请求，所以测的是「计算 + 内存」，
排除了等待时间的影响。缓存被短路，不写 sqlite。

**两种口径别混用**：不带 `app` 时走 `translate_stream`，成品以 `bytes` 返回、
探针自己持有；带 `app` 时走 `api.translate`，成品直接写盘，且作业结束会调用
`release_layout_model()`。**app 模式下探针绝不能自己持有模型引用**——否则模型
释放不掉，「作业后」这一列会虚高约 90 MB（踩过一次）。

## 口径

一律用 **USS**（独占物理内存），不用 RSS。Windows 上 `psutil` 的 `rss` 是工作集，
包含与其他进程共享的 DLL 页；把 Electron 的多进程相加会重复计数——
同一个空白窗口 RSS 合计 290 MB、USS 只有 69 MB。

## 基线（改动前，commit 14a9949）

| 场景 | 作业后 USS | 峰值 USS |
|---|---|---|
| 46.5 MB / 30 页扫描件，fast | 369.5 MB | 733.9 MB |
| 1.8 MB / 15 页论文，fast | 336.5 MB | 439.7 MB |
| 1.8 MB / 15 页论文，ultrafast | 114.9 MB | 173.1 MB |

分阶段（46.5 MB 扫描件，fast）：

```
imports                                  70 MB
+ ONNX 会话                             170 MB   (+100)
+ 输入原始字节                          218 MB   (+48)
+ doc_en 序列化进 BytesIO               264 MB   (+47)
+ doc_zh 序列化进 BytesIO（fp）         311 MB   (+47)
+ translate_patch（arena+掩膜+推理）    545 MB   (+234)
+ dual 装配                             545 MB   (+0)
+ mono 序列化                           592 MB   (+47)
+ dual 序列化                           685 MB   (+93)
```

ONNX 会话单独测（`_mem_arena.log`）：arena 开时推理 3 次 +235 MB、峰值 400 MB；
关掉后 +6 MB、峰值 174 MB。

## 改动后实测（A+B 已合并）

同一台机器、同一份样本、同一个脚本。先看 `translate_stream` 口径（可与上面的基线
逐行对比）：

| 场景 | 基线峰值 | 现在峰值 | 基线作业后 | 现在作业后 |
|---|---|---|---|---|
| 30 页扫描件 fast | 733.9 | **552.7** | 369.5 | 388.5 |
| 15 页论文 fast | 439.7 | **313.2** | 336.5 | 225.9 |
| 15 页论文 ultrafast | 173.1 | **151.3** | 114.9 | 121.6 |

再看 app 口径（`api.translate`，成品写盘、结束释放模型），这才是 sidecar 的真实占用：

| 场景 | 峰值 USS | 作业后 USS |
|---|---|---|
| 30 页扫描件 fast | **510.2** | **120.1** |
| 15 页论文 fast | **313.8** | **138.9** |

读法：

- 峰值主要来自关掉 ONNX arena（约 −100 MB 常驻）与掩膜 `float64 → uint8`
  （每页 3.8 MB → 0.5 MB，30 页约 −100 MB）。
- 「作业后」在 app 口径下才明显：模型在整批结束后真的被释放，剩 120–140 MB
  是 Python 运行时 + ORT allocator 的保留量。
- `translate_stream` 口径下扫描件的作业后反而 +19 MB，是脚本自己持有
  `mono + dual` 两份输出（共 139 MB）与分配器复用的结果，不是回退。
- 单页推理从约 0.6 s 变成约 1.1 s（arena 关闭的代价）。要换回速度就设
  `layout.cpu_mem_arena: true`。

## 还没做的

- 前端预览换 pdf.js 后的 renderer 内存没有独立复测（当前结论来自功能冒烟）。
- 未在打包产物（`electron-builder`）上复测；以上都是开发树上的数字。
