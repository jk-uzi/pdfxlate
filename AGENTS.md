# AGENTS.md

`pdfxlate` — 保持版式的 PDF 文献翻译工具。Electron 前端 + Python sidecar，
Python 侧负责 PDF 解析、ONNX 版面检测与排版回填。

## 目录定位

**`docs/`** — 项目的长期开发方向，与代码一一对应。
[docs/project-structure.md](docs/project-structure.md) 是代码索引：先读它，
里面有「改某个功能该动哪个文件」的对照表和完整文件树。
[docs/development-plan.md](docs/development-plan.md) 是架构方案与实施计划。

**`references/`** — 参考项目。`references/PDFMathTranslate/` 是上游 PDFMathTranslate
的完整克隆，用于对照移植逻辑、理解某个算法的原始意图。可以随便读，但本项目
的代码自己写，不要直接复制，也不要 import 或修改它。

**`requirements/`** — 短期开发记录。每次开发任务的进展、遇到的问题、待办事项
记在这里，随时间累积，是比 `docs/` 更细、更临时的记录。

## 代码位置

```
python/pdfxlate/          Python 核心
  paths.py                跨平台路径（config / cache / assets）
  config.py               pydantic 配置模型 + YAML 读写
  assets.py               模型 / 字体 / OCR 数据下载
  cache.py                sqlite3 翻译缓存
  api.py                  公开 Python API
  cli.py                  命令行入口
  sidecar.py              stdio JSON 行协议，供 Electron 调用
  mcp_server.py           MCP 服务（stdio），供外部 agent 调用
  models/                 翻译层：ModelAdapter 基类 + 注册表 + OpenAI 兼容适配器
  pdf/                    PDF 管线：pipeline / interp / layout / ocr / converter 等
                          （two_phase.py 是 MCP 用的两遍式收集/回填引擎）
src/{main,preload,renderer}/   Electron 前端（主进程 / preload / 渲染进程）
                              渲染进程为两栏：左侧设置栏 + 右侧 PDF 预览
tests/                    pytest 测试
scripts/                  copy-static.mjs（拷静态资源）、build.sh（一键构建）
```

配置是**供应商 → 模型**两层：一个供应商配一次 endpoint 与密钥，下面挂任意多个
模型。见 [docs/project-structure.md](docs/project-structure.md) 的 `config.py` 一节。

各模块职责、关键符号、以及「改某个功能该动哪个文件」见
[docs/project-structure.md](docs/project-structure.md)。

## 常用命令

```bash
git config core.hooksPath .githooks                     # 启用提交前密钥扫描（克隆后一次）
python/.venv/bin/python -m pytest tests/ -q             # Python 测试
npm run typecheck                                       # 前端类型检查
```

venv 在 `python/.venv`（Python 3.12），包以 editable 方式装好，改源码即时生效。

## 提交前防护

仓库带一个原生 git hook（`.githooks/pre-commit`），阻止密钥进入提交：
gitleaks 扫暂存区 + 正则兜底 + 大文件拦截。**克隆后必须启用一次**：

```bash
git config core.hooksPath .githooks
```

误报请改 `.gitleaks.toml` 的 allowlist，不要习惯性 `--no-verify`。
详见 [docs/project-structure.md](docs/project-structure.md) 第六节。
