/**
 * Minimal i18n for the renderer: a flat key table per locale, no framework.
 *
 * The UI ships in English and Simplified Chinese. Two mechanisms keep the
 * translation table and the markup in sync:
 *
 * * `t(key, params)` for strings built in code (dynamic messages, labels on
 *   elements created at runtime).
 * * `data-i18n*` attributes for static markup, applied by
 *   `applyStaticTranslations()` — see the supported attribute list below.
 *
 * The module is deliberately side-effect free at import time: nothing touches
 * `document` or `localStorage` until `initI18n()` or `setLocale()` is called.
 * That keeps the tables loadable in plain Node, where `auditKeys()` is used to
 * prove the two locales cover the same key set.
 */

export type Locale = "en" | "zh";

export const LOCALES: readonly Locale[] = ["en", "zh"];

/** Names shown in the language switcher, each written in its own language. */
export const LOCALE_LABELS: Record<Locale, string> = {
  en: "English",
  zh: "中文",
};

type Table = Record<string, string>;

const EN: Table = {
  "app.subtitle": "Layout-preserving PDF translation",

  "titlebar.toggleSidebar": "Collapse or expand the sidebar",

  "status.starting": "starting…",
  "status.ready": "engine ready",
  "status.failed": "engine failed",
  "status.localeLabel": "Interface language",

  // --- settings ---
  "settings.title": "Settings",
  "settings.open": "Settings",
  "settings.back": "Back",
  "settings.sectionModel": "Model",
  "settings.sectionAgents": "Agents",
  "settings.sectionAppearance": "Appearance",
  "settings.sectionGeneral": "General",
  "settings.sectionMcp": "MCP",
  "mcp.intro":
    "Let another AI agent use this app to translate PDFs. Point it at the server below and it can translate with a model you configured here, or with its own.",
  "mcp.snippetTitle": "Config to paste",
  "mcp.copy": "Copy",
  "mcp.copied": "Copied.",
  "mcp.copyFailed": "Could not copy: {error}",
  "mcp.agentsTitle": "Write it for me",
  "mcp.agentsHint":
    "Only agents found on this machine are listed. Each file is backed up before it is changed, and only our one entry is added.",
  "mcp.noAgents": "No supported agent config found on this machine.",
  "mcp.write": "Write",
  "mcp.writing": "Writing…",
  "mcp.written": "Added to {path}. Backup: {backup}",
  "mcp.alreadyPresent": "Already configured in {path} (nothing changed).",
  "mcp.writeFailed": "Could not write: {error}",
  "mcp.unavailable": "Engine not available yet: {error}",
  "mcp.agent.claude-desktop": "Claude Desktop",
  "mcp.agent.claude-code": "Claude Code",
  "mcp.agent.codex": "Codex",
  "mcp.agent.workbuddy": "WorkBuddy",
  "mcp.agent.zcode": "ZCode",

  "settings.language": "Interface language",
  "settings.engineStatus": "Engine",
  "settings.version": "Version",
  "settings.configDir": "Config directory",
  "settings.logTitle": "Engine log",
  "settings.unknown": "unknown",

  "setup.text":
    "<strong>First run:</strong> the layout model (75&nbsp;MB) and fonts must be downloaded before translating. This happens once.",
  "setup.download": "Download now",
  "setup.downloading": "Downloading…",
  "setup.ready": "Assets ready.",
  "setup.failed": "Download failed: {error}",

  "docs.title": "Documents",
  "docs.pickFolder": "Choose folder…",
  "docs.pickFolderShort": "Choose…",
  "docs.noFolder": "No folder chosen",
  "docs.includeInJob": "Include in the next translation",
  "docs.translated": "translated",
  "docs.search": "Search documents…",
  "docs.noMatch": "No document matches that search.",

  "model.title": "Model",
  "model.providerListTitle": "Providers",
  "model.addProvider": "Add provider",
  "model.modelListTitle": "Models",
  "model.addModel": "Add model",
  "model.edit": "Edit",
  "model.removeModel": "Remove model",
  "model.removeProvider": "Remove provider",
  "model.enabledHint": "Off takes this model out of rotation; its settings are kept.",
  "model.statusReady": "Endpoint and key are set",
  "model.statusIncomplete": "Endpoint or API key is missing",
  "model.statusEmpty": "No models yet",
  "model.runHint": "Which model a translation uses is chosen in Options.",
  "model.noProviderSelected": "Add a provider to start.",
  "model.newProviderPlaceholder": "name",
  "model.kind": "API format",
  "model.kindChatCompletions": "Chat Completions (/v1/chat/completions)",
  "model.kindResponses": "Responses (/responses)",
  "model.kindAnthropic": "Anthropic Messages (/v1/messages)",
  "model.baseUrl": "Base URL",
  "model.apiKey": "API key",
  "model.apiKeySaved": "(saved — leave blank to keep)",
  "model.apiKeyPlaceholder": "sk-…",
  "model.newModelPlaceholder": "alias",
  "model.modelId": "Model ID",
  "model.reasoning": "Reasoning effort",
  "model.reasoningDefault": "(default)",
  "model.reasoningLow": "low",
  "model.reasoningMedium": "medium",
  "model.reasoningHigh": "high",
  "model.contextLength": "Context length (tokens)",
  "model.maxTokens": "Max output tokens",
  "model.maxTokensAuto": "leave blank to use the API default",
  "model.maxTokensAutoValue": "suggested: {value}",
  "model.topP": "Top P",
  "model.stream": "Stream the response",
  "model.temperature": "Temperature",
  "model.temperatureAuto": "blank = use the API default (needed by some reasoning models)",
  "model.extraBody": "Extra body (JSON)",
  "model.add": "Add",
  "model.saveChanges": "Save changes",
  "model.collapse": "Close",
  "model.noModels": "(no models yet)",
  "model.noProvider": "No provider selected.",
  "model.nameRequired": "Enter a name first.",
  "model.providerExists": "A provider named “{name}” already exists.",
  "model.modelExists": "A model named “{name}” already exists.",
  "model.removeProviderConfirm": "Remove provider “{name}” and all of its models?",
  "model.removeModelConfirm": "Remove model “{name}”?",
  "model.save": "Save",
  "model.saved": "Saved.",
  "model.extraBodyInvalid": "Extra body is not valid JSON.",
  "model.configUnavailable": "Configuration is not loaded yet.",
  "model.saveFailed": "Save failed: {error}",
  "model.test": "Test connection",
  "model.testing": "Testing…",
  "model.testOk": "OK — {ms} ms, model answered “{sample}”",
  "model.testFailed": "Failed: {error}",
  "model.testNoModel": "Add a model first; the probe needs one to call.",
  "model.renameProvider": "Rename provider",
  "model.providerNamePlaceholder": "provider name",
  "model.providerEnabledHint": "Off takes every model of this provider out of rotation; the settings are kept.",
  "model.fetchModels": "Fetch from API",
  "model.fetching": "Fetching…",
  "model.fetchTitle": "Models offered by this endpoint",
  "model.fetchHint": "Ticked entries are already configured and will not be replaced.",
  "model.fetchSearch": "Filter by name",
  "model.fetchImport": "Import selected",
  "model.fetchAlreadyAdded": "added",
  "model.fetchSelectAll": "Select all",
  "model.fetchCount": "{n} of {m} selected",
  "model.aliasHint": "local alias: {alias}",
  "model.fetchNoMatch": "Nothing matches that filter.",
  "model.fetchEmpty": "The endpoint returned no models.",
  "model.fetchNoneSelected": "Tick at least one model first.",
  "model.fetchFailed": "Could not fetch the list: {error}",
  "model.cancel": "Cancel",

  // --- settings › agents (external agent CLIs as translation backends) ---
  "agents.intro":
    "Use an agent CLI already installed on this machine — and the login it already has — as a translation backend. Tick a model to make it available in Options.",
  "agents.termsHint":
    "Only the CLI's headless mode is called; no credentials are copied. Usage counts against that agent's own plan.",
  "agents.rescan": "Rescan",
  "agents.detecting": "Detecting installed agents…",
  "agents.empty": "The engine reported no agents.",
  "agents.status.installed": "Installed",
  "agents.status.missing": "Not installed",
  "agents.status.auth": "Not signed in",
  "agents.status.unknown": "Unverified",
  "agents.status.unavailable": "Unavailable",
  "agents.reason.not_installed":
    "The CLI was not found on PATH or in the standard install locations.",
  "agents.reason.app_only":
    "Only the desktop app is installed; this needs its separate CLI.",
  "agents.reason.node_missing": "This CLI needs Node.js, which was not found.",
  "agents.reason.invalid_path":
    "The configured CLI path does not point at an executable.",
  "agents.reason.unknown": "The agent reported an unrecognised problem.",
  "agents.authHint":
    "Sign in by running the CLI itself; this app never reads or stores its credentials.",
  "agents.configuredUnavailable":
    "Configured earlier, but the CLI is not installed now. Its models stay listed in Options until you untick them below.",
  "agents.name.claude": "Claude Code",
  "agents.name.codex": "Codex CLI",
  "agents.name.workbuddy": "WorkBuddy / CodeBuddy",
  "agents.name.zcode": "ZCode",
  "agents.name.dsh": "DeepSeek Harness",
  "agents.version": "v{version}",
  "agents.install": "Install",
  "agents.docs": "Docs",
  "agents.modelsTitle": "Models",
  "agents.refreshModels": "Refresh models",
  "agents.refreshing": "Refreshing…",
  "agents.modelsRefreshed": "Model list refreshed.",
  "agents.modelsFallback":
    "Could not query the CLI, using the built-in list: {error}",
  "agents.modelsFailed": "Could not refresh models: {error}",
  "agents.noModels": "This agent reported no models; add a model id below.",
  "agents.current": "Current",
  "agents.zcode.currentOnly":
    "ZCode CLI can only use its current model. Switch models inside ZCode first.",
  "agents.customModel": "custom",
  "agents.customModelPlaceholder": "custom model id",
  "agents.addModel": "Add",
  "agents.modelExists": "Model “{name}” is already configured.",
  "agents.modelInvalid": "A model id cannot be empty or contain whitespace or “|”.",
  "agents.test": "Test",
  "agents.testing": "Testing…",
  "agents.testOk": "OK — {ms} ms, {model} answered “{sample}”",
  "agents.testFailed": "Test failed: {error}",
  "agents.path.title": "CLI path",
  "agents.path.placeholder": "Path to the CLI executable (optional)",
  "agents.path.save": "Save",
  "agents.path.clear": "Clear",
  "agents.path.saved": "CLI path saved.",
  "agents.path.cleared": "CLI path override cleared.",
  "agents.path.required": "Enter a path first.",
  "agents.saved": "Saved.",
  "agents.saveFailed": "Save failed: {error}",
  "agents.conflict":
    "A provider named “{provider}” already exists with a different kind or agent. Rename or remove it in Settings › Model first.",
  "agents.configUnavailable": "Configuration is not loaded yet.",
  "agents.failed": "Could not detect agents: {error}",
  "agents.linkFailed": "Could not open the link: {error}",
  "agents.unknownConfigured":
    "Configured agent provider(s) this version does not recognise: {agents}. Edit config.yaml to fix or remove them.",

  "options.title": "Options",
  "options.followConfig": "Use the configured value",
  "options.model": "Model",
  "options.reasoning": "Reasoning effort",
  "options.agentSuffix": " · CLI",
  "options.reasoningAgent": "Reasoning effort does not apply to agent CLI models.",
  "sidebar.resize": "Drag to resize the sidebar",
  "options.langIn": "Source language",
  "options.langOut": "Target language",
  "options.mode": "Mode",
  "options.modeFast": "fast — ONNX layout detection",
  "options.modeUltrafast": "ultrafast — native text positions",
  "options.threads": "Threads",
  "options.outputMode": "Output",
  "options.outputDual": "Alternating — source, then translation",
  "options.outputMono": "Translation only",
  "options.pages": "Pages (optional)",
  "options.translate": "Translate",
  "options.cancel": "Cancel translation",
  "options.cancelling": "Cancelling…",

  // --- preview pane ---
  "preview.emptyTitle": "No document open",
  "preview.emptyHint": "Drop a PDF here, or use “Choose files…” in the sidebar.",
  "tab.close": "Close",
  "tab.scrollLeft": "Scroll tabs left",
  "tab.scrollRight": "Scroll tabs right",

  "drop.notPdf": "Only PDF files can be opened; skipped {name}",
  "drop.noFolder": "No folder chosen, so the file was opened without being filed (choose a folder to keep dropped files).",
  "drop.copied": "Copied into the folder: {path}",
  "drop.importFailed": "Could not copy into the folder, opened in place: {error}",

  "preview.dropUnreadable": "Could not read dropped file paths; use “Choose files…” instead.",
  "preview.page": "Page",
  "preview.prevPage": "Previous page",
  "preview.nextPage": "Next page",
  "preview.zoomOut": "Zoom out",
  "preview.zoomIn": "Zoom in",
  "preview.fitWidth": "Fit width",
  "preview.fitHeight": "Fit height",
  "preview.spread": "Two pages side by side",
  "preview.spreadPending": "Building the facing-page view…",
  "preview.spreadFailed": "Could not build the facing-page view: {error}",
  "preview.saveAs": "Save a copy",
  "preview.pinOverlays": "Keep the page controls on screen",
  "preview.unpinOverlays": "Let the page controls hide again",
  "preview.openFile": "Open in the default app",
  "preview.revealFile": "Show in folder",
  "preview.title": "Preview",

  "progress.idle": "Ready.",
  "progress.starting": "Starting…",
  "progress.done": "Finished.",
  "progress.failed": "Translation failed: {error}",
  "progress.cancelled": "incomplete files were not written.",
  "progress.cacheHits": "Translation cache: {hits}/{total} paragraphs reused.",
  "progress.providerCacheHits": "Provider prompt cache: {hit}/{total} tokens.",


  // `log.title` went with the log group; the panel heading is now
  // `settings.logTitle`, since the log lives in the General settings panel.
  "log.engineFailed": "engine failed to start: {error}",
  "log.assetCheckFailed": "asset check failed: {error}",
  "log.providersFailed": "could not load providers: {error}",
  "log.agentsFailed": "could not detect agents: {error}",
  "log.engineFactsFailed": "could not read engine details: {error}",
  "log.pageCountFailed": "could not read the page count: {error}",
  "log.savedAs": "saved a copy to {path}",
  "log.saveAsFailed": "could not save a copy: {error}",
  "log.folderFailed": "could not list the folder: {error}",
  "log.mcpWrote": "MCP config updated: {path} (backup {backup})",
  "log.cancelFailed": "cancel failed: {error}",
  "log.unknownError": "unknown error",

  // Stage names arrive from the sidecar as bare identifiers; unknown stages
  // fall through to the raw value (see t()).
  "stage.assets": "assets",
  "stage.layout": "layout",
  "stage.translation": "translation",
  "stage.typesetting": "typesetting",
  "stage.save": "save",
  "stage.setup": "setup",
  "stage.starting": "starting",
  "stage.done": "done",
  "stage.error": "error",
  "stage.cancelled": "cancelled",

  "dialog.selectPdf": "Select PDF files",
  "dialog.engineErrorTitle": "Could not start the translation engine",
  "dialog.engineErrorHint":
    "If running from source, make sure the Python environment is set up:",
};

const ZH: Table = {
  "app.subtitle": "保持版式的 PDF 文献翻译",

  "titlebar.toggleSidebar": "折叠或展开侧栏",

  "status.starting": "启动中…",
  "status.ready": "引擎就绪",
  "status.failed": "引擎启动失败",
  "status.localeLabel": "界面语言",

  "settings.title": "设置",
  "settings.open": "设置",
  "settings.back": "返回",
  "settings.sectionModel": "模型",
  "settings.sectionAgents": "外部 Agent",
  "settings.sectionAppearance": "外观",
  "settings.sectionGeneral": "常规",
  "settings.sectionMcp": "MCP",
  "mcp.intro":
    "让别的 AI agent 用这个软件翻译 PDF。把它指向下面的服务器，它就能用你在这里配好的模型，或用它自己的模型来翻译。",
  "mcp.snippetTitle": "复制这段配置",
  "mcp.copy": "复制",
  "mcp.copied": "已复制。",
  "mcp.copyFailed": "复制失败：{error}",
  "mcp.agentsTitle": "帮我写入",
  "mcp.agentsHint":
    "只列出本机检测到的 agent。写入前会先备份原文件，且只添加我们这一项。",
  "mcp.noAgents": "本机没有检测到支持的 agent 配置。",
  "mcp.write": "写入",
  "mcp.writing": "正在写入…",
  "mcp.written": "已写入 {path}。备份：{backup}",
  "mcp.alreadyPresent": "{path} 中已配置过（未做改动）。",
  "mcp.writeFailed": "写入失败：{error}",
  "mcp.unavailable": "引擎尚不可用：{error}",
  "mcp.agent.claude-desktop": "Claude Desktop",
  "mcp.agent.claude-code": "Claude Code",
  "mcp.agent.codex": "Codex",
  "mcp.agent.workbuddy": "WorkBuddy",
  "mcp.agent.zcode": "ZCode",

  "settings.language": "界面语言",
  "settings.engineStatus": "引擎",
  "settings.version": "版本",
  "settings.configDir": "配置目录",
  "settings.logTitle": "引擎日志",
  "settings.unknown": "未知",

  "setup.text":
    "<strong>首次运行：</strong>翻译前需要先下载版面模型（75&nbsp;MB）与字体，此操作只需一次。",
  "setup.download": "立即下载",
  "setup.downloading": "下载中…",
  "setup.ready": "资源已就绪。",
  "setup.failed": "下载失败：{error}",

  "docs.title": "文档",
  "docs.pickFolder": "选择文件夹…",
  "docs.pickFolderShort": "选择…",
  "docs.noFolder": "尚未选择文件夹",
  "docs.includeInJob": "加入下一次翻译",
  "docs.translated": "译文",
  "docs.search": "搜索文档…",
  "docs.noMatch": "没有匹配的文档。",

  "model.title": "模型",
  "model.providerListTitle": "服务商",
  "model.addProvider": "添加服务商",
  "model.modelListTitle": "模型列表",
  "model.addModel": "添加模型",
  "model.edit": "编辑",
  "model.removeModel": "删除模型",
  "model.removeProvider": "删除服务商",
  "model.enabledHint": "关闭后该模型不再参与翻译，但配置会保留。",
  "model.statusReady": "接口地址与密钥均已填写",
  "model.statusIncomplete": "缺少接口地址或 API 密钥",
  "model.statusEmpty": "还没有模型",
  "model.runHint": "翻译时用哪个模型，在「选项」里选。",
  "model.noProviderSelected": "先添加一个服务商。",
  "model.newProviderPlaceholder": "名称",
  "model.kind": "类型",
  "model.kindChatCompletions": "Chat Completions（/v1/chat/completions）",
  "model.kindResponses": "Responses（/responses）",
  "model.kindAnthropic": "Anthropic Messages（/v1/messages）",
  "model.baseUrl": "接口地址",
  "model.apiKey": "API 密钥",
  "model.apiKeySaved": "（已保存 — 留空则保持不变）",
  "model.apiKeyPlaceholder": "sk-…",
  "model.newModelPlaceholder": "别名",
  "model.modelId": "模型 ID",
  "model.reasoning": "推理强度",
  "model.reasoningDefault": "（默认）",
  "model.reasoningLow": "低",
  "model.reasoningMedium": "中",
  "model.reasoningHigh": "高",
  "model.contextLength": "上下文长度（token）",
  "model.maxTokens": "最大输出 token",
  "model.maxTokensAuto": "留空则用接口默认值",
  "model.maxTokensAutoValue": "建议：{value}",
  "model.topP": "Top P",
  "model.stream": "流式返回",
  "model.temperature": "温度",
  "model.temperatureAuto": "留空则不发送该参数（部分推理模型不接受它）",
  "model.extraBody": "额外请求体（JSON）",
  "model.add": "添加",
  "model.saveChanges": "保存修改",
  "model.collapse": "收起",
  "model.noModels": "（暂无模型）",
  "model.noProvider": "未选择服务商。",
  "model.nameRequired": "请先填写名称。",
  "model.providerExists": "已存在名为「{name}」的服务商。",
  "model.modelExists": "已存在名为「{name}」的模型。",
  "model.removeProviderConfirm": "确定移除服务商「{name}」及其全部模型？",
  "model.removeModelConfirm": "确定移除模型「{name}」？",
  "model.save": "保存",
  "model.saved": "已保存。",
  "model.extraBodyInvalid": "额外请求体不是合法的 JSON。",
  "model.configUnavailable": "配置尚未加载。",
  "model.saveFailed": "保存失败：{error}",
  "model.test": "测试连接",
  "model.testing": "测试中…",
  "model.testOk": "连接正常 — {ms} ms，模型返回「{sample}」",
  "model.testFailed": "连接失败：{error}",
  "model.testNoModel": "请先添加一个模型，测试需要调用它。",
  "model.renameProvider": "重命名服务商",
  "model.providerNamePlaceholder": "服务商名称",
  "model.providerEnabledHint": "关闭后该服务商下的所有模型都不再参与翻译，配置会保留。",
  "model.fetchModels": "从上游拉取",
  "model.fetching": "拉取中…",
  "model.fetchTitle": "该接口提供的模型",
  "model.fetchHint": "已勾选的表示当前已配置，导入时不会覆盖。",
  "model.fetchSearch": "按名称筛选",
  "model.fetchImport": "导入所选",
  "model.fetchAlreadyAdded": "已添加",
  "model.fetchSelectAll": "全选",
  "model.fetchCount": "已选 {n} / {m}",
  "model.aliasHint": "本地别名：{alias}",
  "model.fetchNoMatch": "没有匹配的模型。",
  "model.fetchEmpty": "该接口没有返回任何模型。",
  "model.fetchNoneSelected": "请先勾选至少一个模型。",
  "model.fetchFailed": "拉取失败：{error}",
  "model.cancel": "取消",

  "agents.intro":
    "把本机已安装的 agent CLI（连同它自己的登录态）当作翻译后端。勾选模型后即可在「选项」里选用。",
  "agents.termsHint":
    "只以 headless 模式调用 CLI，不复制任何密钥；消耗的是该 agent 自己的额度。",
  "agents.rescan": "重新检测",
  "agents.detecting": "正在检测已安装的 agent…",
  "agents.empty": "引擎没有返回任何 agent。",
  "agents.status.installed": "已安装",
  "agents.status.missing": "未安装",
  "agents.status.auth": "未登录",
  "agents.status.unknown": "未验证",
  "agents.status.unavailable": "不可用",
  "agents.reason.not_installed": "在 PATH 与标准安装位置都没有找到该 CLI。",
  "agents.reason.app_only": "只检测到桌面版；这里需要它的独立 CLI。",
  "agents.reason.node_missing": "运行该 CLI 需要 Node.js，但没有找到。",
  "agents.reason.invalid_path": "配置的 CLI 路径不是可执行文件。",
  "agents.reason.unknown": "该 agent 报告了无法识别的问题。",
  "agents.authHint": "请先在命令行里完成该 CLI 的登录；本应用不会读取或保存它的凭据。",
  "agents.configuredUnavailable":
    "之前配置过，但现在没有检测到该 CLI。在下面取消勾选之前，它的模型仍会出现在「选项」里。",
  "agents.name.claude": "Claude Code",
  "agents.name.codex": "Codex CLI",
  "agents.name.workbuddy": "WorkBuddy / CodeBuddy",
  "agents.name.zcode": "ZCode",
  "agents.name.dsh": "DeepSeek Harness",
  "agents.version": "v{version}",
  "agents.install": "安装",
  "agents.docs": "文档",
  "agents.modelsTitle": "模型",
  "agents.refreshModels": "刷新模型",
  "agents.refreshing": "刷新中…",
  "agents.modelsRefreshed": "模型列表已刷新。",
  "agents.modelsFallback": "无法查询该 CLI，已使用内置列表：{error}",
  "agents.modelsFailed": "刷新模型失败：{error}",
  "agents.noModels": "该 agent 没有返回模型；可在下面手动添加模型 ID。",
  "agents.current": "当前",
  "agents.zcode.currentOnly":
    "ZCode CLI 只能使用它当前的模型；请先在 ZCode 内切换模型。",
  "agents.customModel": "自定义",
  "agents.customModelPlaceholder": "自定义模型 ID",
  "agents.addModel": "添加",
  "agents.modelExists": "模型「{name}」已经配置过了。",
  "agents.modelInvalid": "模型 ID 不能为空，也不能包含空白或「|」。",
  "agents.test": "测试",
  "agents.testing": "测试中…",
  "agents.testOk": "正常 — {ms} ms，{model} 返回「{sample}」",
  "agents.testFailed": "测试失败：{error}",
  "agents.path.title": "CLI 路径",
  "agents.path.placeholder": "CLI 可执行文件路径（可选）",
  "agents.path.save": "保存",
  "agents.path.clear": "清除",
  "agents.path.saved": "CLI 路径已保存。",
  "agents.path.cleared": "已清除 CLI 路径覆盖。",
  "agents.path.required": "请先填写路径。",
  "agents.saved": "已保存。",
  "agents.saveFailed": "保存失败：{error}",
  "agents.conflict":
    "已存在同名服务商「{provider}」，但类型或 agent 不匹配。请先在「设置 › 模型」中改名或删除。",
  "agents.configUnavailable": "配置尚未加载。",
  "agents.failed": "检测 agent 失败：{error}",
  "agents.linkFailed": "无法打开链接：{error}",
  "agents.unknownConfigured":
    "这些已配置的 agent 供应商本版本无法识别：{agents}。请编辑 config.yaml 修正或删除。",

  "options.title": "选项",
  "options.followConfig": "跟随配置",
  "options.model": "模型",
  "options.reasoning": "推理强度",
  "options.agentSuffix": " · CLI",
  "options.reasoningAgent": "外部 Agent 模型不支持推理强度。",
  "sidebar.resize": "拖动以调整侧栏宽度",
  "options.langIn": "源语言",
  "options.langOut": "目标语言",
  "options.mode": "模式",
  "options.modeFast": "fast — ONNX 版面检测",
  "options.modeUltrafast": "ultrafast — 原生文本位置",
  "options.threads": "并发线程",
  "options.outputMode": "译文形式",
  "options.outputDual": "交替（原文 + 译文）",
  "options.outputMono": "仅译文",
  "options.pages": "页码范围（可选）",
  "options.translate": "开始翻译",
  "options.cancel": "取消翻译",
  "options.cancelling": "正在取消…",

  "preview.emptyTitle": "尚未打开文档",
  "preview.emptyHint": "将 PDF 拖到此处，或用左侧的「选择文件…」。",
  "tab.close": "关闭",
  "tab.scrollLeft": "向左滚动标签",
  "tab.scrollRight": "向右滚动标签",

  "drop.notPdf": "只能打开 PDF，已跳过 {name}",
  "drop.noFolder": "尚未选择文件夹，文件已打开但未归档（选择文件夹后可自动归档拖入的文件）。",
  "drop.copied": "已复制到文件夹：{path}",
  "drop.importFailed": "复制到文件夹失败，已在原位置打开：{error}",

  "preview.dropUnreadable": "无法读取拖入文件的路径，请改用「选择文件…」。",
  "preview.page": "页",
  "preview.prevPage": "上一页",
  "preview.nextPage": "下一页",
  "preview.zoomOut": "缩小",
  "preview.zoomIn": "放大",
  "preview.fitWidth": "适应宽度",
  "preview.fitHeight": "适应高度",
  "preview.spread": "双栏显示（左右两页）",
  "preview.spreadPending": "正在生成左右对照视图…",
  "preview.spreadFailed": "无法生成左右对照视图：{error}",
  "preview.saveAs": "另存为",
  "preview.pinOverlays": "让页面控件常驻显示",
  "preview.unpinOverlays": "恢复为鼠标移入才显示",
  "preview.openFile": "用默认应用打开",
  "preview.revealFile": "在文件夹中显示",
  "preview.title": "预览",

  "progress.idle": "就绪。",
  "progress.starting": "正在启动…",
  "progress.done": "已完成。",
  "progress.failed": "翻译失败：{error}",
  "progress.cancelled": "未完成的文件没有写出。",
  "progress.cacheHits": "本地缓存命中 {hits}/{total} 段。",
  "progress.providerCacheHits": "服务端提示词缓存 {hit}/{total} tokens。",


  "log.engineFailed": "引擎启动失败：{error}",
  "log.assetCheckFailed": "资源检查失败：{error}",
  "log.providersFailed": "无法加载服务商：{error}",
  "log.agentsFailed": "无法检测 agent：{error}",
  "log.engineFactsFailed": "无法读取引擎信息：{error}",
  "log.pageCountFailed": "无法读取页数：{error}",
  "log.savedAs": "已另存为 {path}",
  "log.saveAsFailed": "另存为失败：{error}",
  "log.folderFailed": "无法列出文件夹：{error}",
  "log.mcpWrote": "已更新 MCP 配置：{path}（备份 {backup}）",
  "log.cancelFailed": "取消失败：{error}",
  "log.unknownError": "未知错误",

  "stage.assets": "资源",
  "stage.layout": "版面",
  "stage.translation": "翻译",
  "stage.typesetting": "排版",
  "stage.save": "保存",
  "stage.setup": "初始化",
  "stage.starting": "启动",
  "stage.done": "完成",
  "stage.error": "错误",
  "stage.cancelled": "已取消",

  "dialog.selectPdf": "选择 PDF 文件",
  "dialog.engineErrorTitle": "无法启动翻译引擎",
  "dialog.engineErrorHint": "若从源码运行，请确认 Python 环境已配置：",
};

const MESSAGES: Record<Locale, Table> = { en: EN, zh: ZH };
for (const table of Object.values(MESSAGES)) Object.freeze(table);

/**
 * Read-only view of one locale's table.
 *
 * Exists for tooling — `scripts/check-i18n.mjs` compares placeholders and
 * checks for empty values, which `auditKeys()` (key parity only) cannot do.
 * The tables are frozen and otherwise module-private so runtime callers cannot
 * mutate them; use `t()` to resolve strings.
 */
export function messagesFor(locale: Locale): Readonly<Record<string, string>> {
  return MESSAGES[locale];
}

const STORAGE_KEY = "pdfxlate.locale";

/** Locale whose table supplies any key missing from the active locale's. */
const FALLBACK_LOCALE: Locale = "en";

let current: Locale = FALLBACK_LOCALE;
const listeners = new Set<(locale: Locale) => void>();

function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && (LOCALES as readonly string[]).includes(value);
}

/** Best guess from the OS/browser locale; anything non-Chinese maps to English. */
export function detectLocale(): Locale {
  const candidates: string[] = [];
  try {
    if (typeof navigator !== "undefined" && navigator.language) {
      candidates.push(navigator.language);
    }
    if (typeof navigator !== "undefined" && navigator.languages) {
      candidates.push(...navigator.languages);
    }
  } catch {
    // navigator can be unavailable in exotic contexts; the default stands.
  }
  for (const tag of candidates) {
    if (tag.toLowerCase().startsWith("zh")) return "zh";
    if (tag) return FALLBACK_LOCALE;
  }
  return FALLBACK_LOCALE;
}

export function getLocale(): Locale {
  return current;
}

function readStoredLocale(): Locale | null {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return isLocale(stored) ? stored : null;
  } catch {
    return null;
  }
}

function storeLocale(locale: Locale): void {
  try {
    localStorage.setItem(STORAGE_KEY, locale);
  } catch {
    // Private mode or a locked-down origin: the choice just is not persisted.
  }
}

/**
 * Translate a key, substituting `{name}` placeholders from `params`.
 *
 * Unknown keys return the key itself so a missing translation is visible in
 * the UI rather than rendering as an empty element. Placeholders without a
 * matching param are left untouched.
 *
 * `fallback`, when given, replaces the key for a lookup that misses BOTH the
 * active table and the fallback locale's. Use it for values that legitimately
 * have no translation — most notably sidecar stage identifiers, where
 * `t(\`stage.${stage}\`, undefined, stage)` must render the raw stage name
 * instead of `stage.foo`. Omitting it keeps the historical behaviour of
 * returning the key.
 */
export function t(
  key: string,
  params?: Record<string, string | number>,
  fallback?: string,
): string {
  const table = MESSAGES[current] ?? MESSAGES[FALLBACK_LOCALE];
  const raw = table[key] ?? MESSAGES[FALLBACK_LOCALE][key] ?? fallback ?? key;
  if (!params) return raw;
  return raw.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in params ? String(params[name]) : match,
  );
}

/**
 * Apply translations to static markup under `root`.
 *
 * Supported attributes, each holding a message key:
 *
 * * `data-i18n` — replaces `textContent`.
 * * `data-i18n-html` — replaces `innerHTML` (for copy containing markup).
 * * `data-i18n-title` — sets the `title` attribute.
 * * `data-i18n-placeholder` — sets the `placeholder` attribute.
 * * `data-i18n-aria-label` — sets the `aria-label` attribute.
 */
export function applyStaticTranslations(root: ParentNode = document): void {
  for (const el of root.querySelectorAll<HTMLElement>("[data-i18n]")) {
    el.textContent = t(el.dataset.i18n as string);
  }
  for (const el of root.querySelectorAll<HTMLElement>("[data-i18n-html]")) {
    el.innerHTML = t(el.dataset.i18nHtml as string);
  }
  for (const el of root.querySelectorAll<HTMLElement>("[data-i18n-title]")) {
    el.title = t(el.dataset.i18nTitle as string);
  }
  for (const el of root.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>(
    "[data-i18n-placeholder]",
  )) {
    el.placeholder = t(el.dataset.i18nPlaceholder as string);
  }
  for (const el of root.querySelectorAll<HTMLElement>("[data-i18n-aria-label]")) {
    el.setAttribute("aria-label", t(el.dataset.i18nAriaLabel as string));
  }
}

/**
 * Switch locale: update the document, re-apply static markup and notify
 * listeners so runtime-built strings can be rebuilt.
 */
export function setLocale(locale: Locale, persist = true): void {
  if (!isLocale(locale)) return;
  current = locale;
  if (persist) storeLocale(locale);
  if (typeof document !== "undefined") {
    document.documentElement.lang = locale === "zh" ? "zh-CN" : "en";
    applyStaticTranslations();
  }
  for (const listener of listeners) listener(locale);
}

/** Subscribe to locale changes; returns an unsubscribe function. */
export function onLocaleChange(listener: (locale: Locale) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/**
 * Resolve the startup locale (stored choice, else detected) and apply it.
 *
 * Returns the active locale so the caller can render its own dynamic strings
 * with the right language on the first pass.
 */
export function initI18n(): Locale {
  const locale = readStoredLocale() ?? detectLocale();
  setLocale(locale, false);
  return locale;
}

/**
 * Report keys present in one locale but missing from another.
 *
 * `auditKeys()` is the check that keeps the tables from drifting apart: the
 * verification step asserts it returns an empty array.
 */
export function auditKeys(): Array<{ locale: Locale; key: string }> {
  const missing: Array<{ locale: Locale; key: string }> = [];
  for (const locale of LOCALES) {
    const other = locale === "en" ? "zh" : "en";
    for (const key of Object.keys(MESSAGES[other])) {
      if (!(key in MESSAGES[locale])) missing.push({ locale, key });
    }
  }
  return missing;
}
