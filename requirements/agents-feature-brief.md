# 实现简报：设置 › Agents（外部 Agent CLI 作为翻译后端）

> 这是本轮开发的实现简报，供并行开发的两个分支共用。长期结构说明见
> [../docs/project-structure.md](../docs/project-structure.md)，本文件只描述本轮要做什么、
> 接口长什么样、边界在哪里。完成后把结果写进
> [development-log.md](development-log.md)，并同步更新 project-structure.md。

## 0. 目标与边界

在设置左栏「模型」下面新增 **Agents / 外部 Agent**：检测本机 agent CLI，把它们的模型接成
`pdfxlate` 的翻译后端。首批五个 agent：

| 显示名 | agent id | 调用方式 |
|---|---|---|
| Claude Code | `claude` | `claude -p`（headless） |
| Codex CLI | `codex` | `codex exec`（headless） |
| WorkBuddy / CodeBuddy | `workbuddy` | `codebuddy -p`（独立 CLI） |
| ZCode | `zcode` | `node <ZCode>/resources/glm/zcode.cjs -p` |
| DeepSeek Harness | `dsh` | `dsh --profile headless`（one-shot） |

**明确不做**：不自动安装/登录 agent；不读其他 agent 的配置文件或密钥；不 GUI 自动化桌面版；
不做聊天界面或多 agent 编排；v1 不映射 temperature/top_p/max_tokens/reasoning。

**核心决策：spawn CLI，不读密钥。** OAuth/订阅额度只存在于 CLI 自己的登录态里，调用 CLI 的
headless 模式是唯一既尊重登录态、又不复制密钥的方式。

## 1. 配置数据模型

沿用现有 `providers → models`，新增一种 kind：

```yaml
providers:
  claude:
    kind: agent_cli
    agent: claude          # 新增字段
    enabled: true
    models:
      sonnet:
        model: sonnet
      opus:
        model: opus

agents:                    # 新增、可选；只存机器本地的路径覆盖
  paths:
    zcode: D:\ZCode\resources\glm\zcode.cjs
```

- `config.py`：`Provider.agent: str | None = None`；`model_validator` 要求
  `kind == "agent_cli"` 时 `agent` 必填；`ResolvedModel.agent: str | None = None`；
  `_flatten()` 增加 `agent=prov.agent`。
- 新增 `AgentSettings(paths: dict[str, str])`，`AppConfig.agents: AgentSettings | None = None`
  （`None` 时 `save()` 不写这一段）。
- `DEFAULT_CONFIG_TEMPLATE` 加一段注释示例（不写死任何 agent）。
- 缓存键：`AgentCliAdapter._cache_params()` 在基类基础上加 `"agent": profile.agent` 和
  `"agent_batch": "v1"`；**不改基类**，避免让所有 HTTP 适配器的缓存键失效。
- provider key 直接用 agent id；若同名供应商已存在且 `kind/agent` 不匹配，Agents 面板拒绝编辑。
- 「设置 › 模型」过滤 `kind == "agent_cli"`；「选项 › 模型」仍列出全部，标签加 ` · CLI`。
- v1 每个 agent 可勾选多个模型；模型别名 = CLI 的模型 id；别名禁止包含 `|`。

## 2. 后端结构

```
python/pdfxlate/agents/
  __init__.py        # AGENT_DEFS、get_agent_def、detect_all、list_models、run_batch
  defs.py            # AgentDef + 五个定义
  detect.py          # 可执行文件解析、版本/登录探测、注册表兜底
  catalog.py         # 模型列表
  runtime.py         # 拼 argv、spawn、输出解析、超时/取消/杀进程树
  batch.py           # 线程安全批队列
python/pdfxlate/models/agent_cli.py
```

### 2.1 每个 agent 的精确调用方式

| agent | 检测 | 登录探测 | 模型列表 | 运行命令 | prompt | 模型选择 |
|---|---|---|---|---|---|---|
| `claude` | PATH + `agents.paths.claude` | `claude auth status` → JSON `loggedIn` | 内置 `sonnet/opus/haiku` + 自定义 id | `claude -p --input-format text --output-format json --json-schema <schema> --tools "" --no-session-persistence --model <id>` | stdin | `--model` |
| `codex` | PATH + `agents.paths.codex` | `codex login status` | `codex debug models`：`models[].slug`，`display_name` 做 label，过滤 `visibility == "hide"`；失败用 `gpt-5-codex/gpt-5/o3/o4-mini` | `codex exec --skip-git-repo-check --sandbox read-only --ephemeral --output-last-message <tmp> -m <id>` | stdin（**不加 `-`**） | `-m` |
| `workbuddy` | `codebuddy` / `cbc` on PATH + 路径覆盖；桌面版只提示 | `codebuddy auth status`（不支持则 unknown） | 解析 `codebuddy -p --help` 的 `Currently supported: (...)`；失败用 `default` | `codebuddy -p --output-format text --model <id>` | stdin | `--model` |
| `zcode` | 路径覆盖 → 注册表/标准目录的 `resources/glm/zcode.cjs`；需要 `node` | `node zcode.cjs doctor`（登录 unknown） | 只有 `default`（ZCode 当前模型） | `node <zcode.cjs> -p <prompt> --no-color --mode plan --surface terminal --cwd <tmp>`；env 指向 `zcode-builtin.json` | argv（限 8k 字符） | 无（`default`） |
| `dsh` | PATH + 路径覆盖 + 注册表 `<install>\resources\runtime\cli\bin\dsh.cmd` | 无 → unknown | `dsh --profile headless --dump-config` YAML：`agent-default-model` + `llm-pi-ai.providers[].models[]`；失败用 `default` | `dsh --profile headless [--patch <tmp>] -` | stdin | `--patch` 覆盖 `agent-default-model` |

补充：

- Claude schema：`{"type":"object","properties":{"translations":{"type":"array","items":{"type":"string"}}},"required":["translations"],"additionalProperties":false}`。
- Codex 用 `--output-last-message` 读最终消息。
- ZCode `--mode plan` 避免 `-p` 默认 yolo；`--surface terminal`。
- DSH patch 文件内容：`- id: agent-default-model` + `config: {provider, model}`；选 `default` 时不加 `--patch`。
- WorkBuddy 只认独立 CLI；桌面版自带的那份缺模块，不硬跑，提示安装 CodeBuddy CLI 并给
  `https://www.codebuddy.cn/docs/workbuddy/Overview`。

### 2.2 detect.py

- 解析顺序：`agents.paths[id]` → 环境变量（`PDFXLATE_CLAUDE_BIN` 等）→
  `shutil.which(bin/fallback_bins)` → 平台标准目录 / Windows 卸载注册表。
- Windows 注册表：`HKCU/HKLM ...\Uninstall\*`，`DisplayName` 含 `ZCode`/`WorkBuddy`/
  `DeepSeek Harness`；`InstallLocation` 为空时从 `DisplayIcon` 推导目录；只读、失败静默。
- 版本/登录/模型探测各带超时（4s/5s/15s）；`agents.list` 用线程池并行，总预算 ≤ 8s，
  结果缓存 30s，`refresh: true` 绕过。
- **安全**：登录探测只回传 `ok/missing/unknown`，绝不回传原始输出（`codex login status`
  会打印掩码 key）；错误里的 stderr 截断到 300 字符，日志不写 prompt/env/原始输出。
- 未安装原因用机器可读码：`not_installed` / `app_only` / `node_missing` / `invalid_path`。

### 2.3 catalog.py + runtime.py + batch.py

- `catalog.list_models(agent, refresh=False)`：按上表执行并解析；失败返回 fallback + `error`，
  不抛给 UI 致命错误。
- `runtime.run_batch(defn, executable, model, texts, lang_out, custom_prompt, timeout, cancel_event)`：
  - 临时工作目录（cache 下 `agents/`），空目录，结束清理；CLI 的 cwd 一律指这里。
  - Windows `CREATE_NO_WINDOW`；`.cmd` 直接作为 argv[0]（已实测可执行）。
  - prompt 走 stdin 或 argv；argv 模式按 `max_prompt_chars` 限制。
  - 超时/取消：`Popen` + 轮询 `cancel_event`（0.2s）；Windows `taskkill /F /T /PID`，
    POSIX `terminate`→`kill`，杀进程树。
  - 输出解析：Claude 拆 JSON envelope 的 `result`；Codex 读 last-message 文件；其余取 stdout
    并 strip ANSI。
  - 错误分类：stderr 命中登录/鉴权关键词 → `AgentAuthError`（永久，带修复提示）；超时 →
    `AgentTimeoutError`（可重试）；非零退出 → `AgentProcessError`（可重试，带 stderr 尾部）；
    解析失败 → 一次严格重试，仍失败则拆成单段重试，最后 `AgentOutputError`。
- `batch.BatchQueue`：
  - 每个适配器一个后台 daemon 线程 + `Condition`；`submit(text)` 入队并等待自己的结果。
  - 触发条件：攒满 8 段 / 字符数超 12000（argv 模式 8000）/ 首段到达后 0.2s。
  - 批量 prompt：段落作为 JSON 数组，要求返回 `{"translations":[...]}`，保持顺序和 `{vN}`；
    **自定义 prompt 模板时 batch size 固定为 1**，用原模板逐段调用。
  - 解析优先级：`translations` 数组 → 索引 key 对象 → 长度匹配的裸数组（排除与输入完全相同的
    回显）→ 单段纯文本。
  - `close()` 停止线程并唤醒等待者；空闲 60s 自动退出；已关闭时 `submit` 抛清晰错误。

### 2.4 适配器与 API

- `models/agent_cli.py`：`@register class AgentCliAdapter(ModelAdapter)`，`name = "agent_cli"`；
  `do_translate()` 交给 `BatchQueue`；`_cache_params()` 加 `agent` + `agent_batch`；
  `close()` 停队列。
- `base.py`：`ModelAdapter` 加 `self.cancellation_event = None` 和 `close()` 空实现；
  `build_adapter(..., cancellation_event=None)` 构造后赋值（不改三个 HTTP 适配器的构造函数）。
- `api.py`：
  - `build_translator(..., cancellation_event=None)` 透传。
  - `translate()` / `translate_stream()` 用 `try/finally` 调 `translator.close()`。
  - agent 供应商时 `resolved_thread = max(resolved_thread, 8)`（最小值，用户设更大仍生效）。
- `pdfxlate.spec` hiddenimports 加 `pdfxlate.models.agent_cli`。

### 2.5 sidecar / CLI / MCP

- 新方法（`METHODS`）：
  - `agents.list` → `{agents: [{id,name,installed,path,version,auth,reason,models:[{id,label}],models_source,models_error,install_url,docs_url,supported}]}`
  - `agents.models` → `{models, source: "cli"|"fallback", error?}`；单 agent、15s 上限。
  - `agents.test` → `{ok, latency_ms, sample}`；`build_adapter("agent_cli", ...)` +
    `do_translate("Hello.")`，不走缓存；90s 上限，加入 `_BACKGROUND_METHODS`。
- `providers.list` 每个供应商加 `"agent": p.agent`。
- `providers.test` 遇到 `kind == "agent_cli"` 直接报「agent 供应商请在设置 › Agents 测试」。
- `_run_bounded(work, name, timeout=25, hint=...)` 增加参数；`agents.test` 用 90s + CLI 提示。
- `mcp_server.list_providers` 加 `agent`；`cli.py --list-models` 对 agent 供应商打印
  `@ agent:<id>` 而不是 `(no base_url)`。

## 3. 前端结构

### 3.1 设置项与面板

- `index.html`：`.settings-menu` 在「模型」后加 `data-section="agents"`；`#settings-host` 内加
  单栏 `<section class="settings-panel hidden" data-panel="agents">`，含标题、说明、
  `#btn-rescan-agents`、`#agents-msg`、`#agent-cards`。
- `app.ts`：`SettingsSection` 加 `"agents"`；`paintSettingsMode()` 在 `agents` 时调用
  `refreshAgents(false)`（首次或引擎就绪后）；只有 `model` 保持 `settings-host--split`。
- 面板是 760px 单栏卡片列表，不新开第三栏。

### 3.2 卡片交互

每张卡：

- 头部：名称（i18n）、状态 pill（已安装/未安装/未登录/未验证/不可用）、版本、路径
  （截断 + title）。
- 未安装：原因 + 安装/文档链接 + 「指定 CLI 路径」输入 + 保存/清除（写 `agents.paths`）。
- 已安装：模型列表（复选框）；已配置但不在当前列表里的自定义模型也显示；每行「测试」按钮；
  「刷新模型」按钮；「自定义模型 ID」输入 + 添加。
- 勾选模型：确保 `providers[agent.id] = {kind:"agent_cli", agent:id, enabled:true, models:{}}`，
  加 `{model: id}`，`config.save`；取消勾选删 key。同名供应商 kind/agent 不匹配时显示冲突并禁用。
- 测试：`agents.test`，显示 `latency_ms` + 截断 sample；失败显示错误。
- 链接用新增 IPC `shell:openExternal`（main.ts 只允许 `https://`，preload 暴露，renderer 调用）。
- 所有文案走 i18n；动态 agent 名用 `switch` 里的字面量 `t("agents.name.claude")`，保证
  `check:i18n` 能扫到。

### 3.3 与现有面板的边界

- `modelPanelProviderNames()` 过滤 `kind === "agent_cli"`；`pickProvider` /
  `renderProviderList` / 空状态都用它。
- `renderOptionModels()` 仍遍历全部供应商；agent 模型标签加 ` · CLI`；选中 agent 供应商时
  禁用「推理强度」下拉并加 title 说明。
- `ProviderDraft` 加 `agent?: string | null`；`ProviderSummary` 加 `agent: string | null`；
  `draftAgents()` 管理 `cachedConfig.agents.paths`。
- 保存后 `refreshProviders()` + `renderAgents()`；引擎就绪时若当前在 agents 面板则自动重试。

### 3.4 i18n 与 CSS

- 新增约 40 个 key，中英成对：`settings.sectionAgents`、`agents.intro/termsHint/rescan/detecting`、
  `agents.status.*`、`agents.reason.*`、`agents.name.*`、`agents.modelsTitle/refreshModels/customModel/addModel/modelExists`、
  `agents.test/testing/testOk/testFailed`、`agents.path.*`、`agents.saved/saveFailed/conflict/configUnavailable`、
  `options.agentSuffix`、`log.agentsFailed` 等。
- CSS 新增 `.agent-cards` / `.agent-card` / `.agent-card__head` / `.agent-card__meta` /
  `.agent-models` / `.agent-model-row` 等，复用现有变量、`.status`、`.hint`、`.msg`、`.btn--small`。

## 4. 边界情况

| 情况 | 行为 |
|---|---|
| CLI 未安装 / 不在 PATH | 卡片显示未安装 + 安装链接 + 手动路径 |
| 已安装但未登录 | 状态「未登录」+ 修复提示；测试/翻译抛永久 `AgentAuthError` |
| 已配置的 agent 后来被卸载 | 选项仍列出；翻译报清晰错误；卡片显示「已配置但不可用」 |
| 同名供应商冲突 | 拒绝编辑，提示先在「模型」里改名/删除 |
| 模型 id 含 `|` 或空白 | 前端拒绝添加并提示 |
| 输出不是 JSON | 严格重试 → 拆单段 → `AgentOutputError`（带截断输出） |
| 单段丢 `{vN}` / 空译文 | 基类按段抛错，不影响同批其他段 |
| 超时 | 杀进程树，抛可重试 `AgentTimeoutError`；converter 最多重试 3 次 |
| 取消任务 | 杀掉 CLI 进程树，`CancelledError` 向上传播 |
| 自定义 prompt | batch size = 1，逐段用原模板 |
| 自定义模型 ID | 允许，作为别名 |
| 未知 agent id 的已有配置 | 面板底部警告，提示编辑 config.yaml；不自动删除 |
| Windows `.cmd` shim | 已验证 Python 可执行；`CREATE_NO_WINDOW`；`taskkill /T` |
| argv 过长 | 按字符上限拆批；单段超限让 CLI 报错并展示 |
| 缓存串味 | `agent` + `agent_batch` 进缓存键 |
| 密钥泄露 | 探测只回状态；错误截断；日志不写 prompt/env/原始输出 |
| agent 用工具/写文件 | 空临时 cwd；Claude `--tools ""`、Codex read-only、ZCode plan；DSH 残余风险文档注明 |

## 5. 测试

- `tests/test_agent_cli.py`：批队列、解析、失败重试/拆单段、`_cache_params` 含 `agent`、
  自定义 prompt 时 batch=1、注册表含 `agent_cli`、`close()`。
- `tests/test_agents_detect.py`：路径覆盖优先、PATH 查找、未找到、版本解析、登录解析
  （不泄露原文）、Codex JSON 模型列表、CodeBuddy help 解析、DSH `--dump-config` YAML
  （含 `!!js` 容错）、ZCode 注册表路径推导。
- `tests/test_sidecar_agents.py`：`agents.list` 形状、`agents.models` 刷新/fallback、
  `agents.test` 用 fake runtime、`providers.list` 含 `agent`、`providers.test` 拒绝 agent_cli。
- `tests/test_config_providers.py` 增补：`agent_cli` + `agent` 解析/拍平、缺 `agent` 报错、
  `agents.paths` 往返与 `${VAR}` 展开、未知字段仍 forbid。
- 集成测试（可选/条件跳过）：fake CLI 脚本 + `api.translate_stream(mode="ultrafast")`；
  `pdf_inspector` 未安装时 skip。

## 6. 验证命令

```bash
python/.venv/Scripts/python.exe -m pytest tests/ -q
npm run typecheck
npm run check:i18n
npm run build
```

CDP/手动验收：打开设置 › Agents；五个卡片渲染；Claude/Codex/DSH 显示已安装；WorkBuddy 显示
「桌面版已装、CLI 缺失」；ZCode 能从 `D:\ZCode\resources\glm\zcode.cjs` 识别；勾选
`claude/sonnet` 后配置写入、选项下拉出现 `claude / sonnet · CLI`；点测试有结果；取消翻译能停。

fake CLI 端到端：临时 `.cmd`/`.py` 忽略 argv、读 stdin、返回 `{"translations":[...]}`，通过
`agents.paths.claude` 指过去，翻译一份真实 PDF，确认无额度消耗且输出正确；测试后清理临时配置
和脚本。真实 CLI 冒烟（需用户同意，会消耗额度）：每个已安装 agent 用 `agents.test` 发一句
`Hello.`，记录延迟/样例。

## 7. 文档（Lead 最后统一做）

- `docs/project-structure.md`：目录树、按需求定位、新模块一节、sidecar 方法表、设置项、
  测试清单。
- `docs/development-plan.md`：新增「外部 agent CLI 作为翻译后端」决策一节。
- `requirements/development-log.md`：新增日期小节。
- `AGENTS.md` 代码树加 `agents/`；README 中英各加一条 feature bullet（可选但建议）。
