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
  "settings.sectionAppearance": "Appearance",
  "settings.sectionGeneral": "General",
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
  "docs.choose": "Choose files…",
  "docs.remove": "Remove",

  "model.title": "Model",
  "model.providerSection": "Provider",
  "model.providerName": "Provider",
  "model.newProviderPlaceholder": "name",
  "model.kind": "Kind",
  "model.baseUrl": "Base URL",
  "model.apiKey": "API key",
  "model.apiKeySaved": "(saved — leave blank to keep)",
  "model.apiKeyPlaceholder": "sk-…",
  "model.modelSection": "Model",
  "model.modelName": "Model",
  "model.newModelPlaceholder": "alias",
  "model.modelId": "Model ID",
  "model.reasoning": "Reasoning effort",
  "model.reasoningDefault": "(default)",
  "model.reasoningLow": "low",
  "model.reasoningMedium": "medium",
  "model.reasoningHigh": "high",
  "model.temperature": "Temperature",
  "model.extraBody": "Extra body (JSON)",
  "model.add": "Add",
  "model.remove": "Remove",
  "model.defaultSuffix": " (default)",
  "model.noProviders": "(no providers configured)",
  "model.noModels": "(no models)",
  "model.noProvider": "No provider selected.",
  "model.noModel": "No model selected.",
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

  "options.title": "Options",
  "options.langIn": "Source language",
  "options.langOut": "Target language",
  "options.mode": "Mode",
  "options.modeFast": "fast — ONNX layout detection",
  "options.modeUltrafast": "ultrafast — native text positions",
  "options.threads": "Threads",
  "options.pages": "Pages (optional)",
  "options.output": "Output directory",
  "options.outputPlaceholder": "(same folder as input)",
  "options.translate": "Translate",
  "options.cancel": "Cancel",

  // --- preview pane ---
  "preview.emptyTitle": "No document open",
  "preview.emptyHint": "Drop a PDF here, or use “Choose files…” in the sidebar.",
  "preview.dropUnreadable": "Could not read dropped file paths; use “Choose files…” instead.",
  "preview.viewOriginal": "Original",
  "preview.viewTranslated": "Translated",
  "preview.viewCompare": "Compare",
  "preview.needResults": "Translate a document first to see the translated pages.",
  "preview.page": "Page",
  "preview.prevPage": "Previous page",
  "preview.nextPage": "Next page",
  "preview.zoomOut": "Zoom out",
  "preview.zoomIn": "Zoom in",
  "preview.fitWidth": "Fit width",
  "preview.fitHeight": "Fit height",
  "preview.openFile": "Open in the default app",
  "preview.revealFile": "Show in folder",
  "preview.labelOriginal": "Original",
  "preview.labelTranslated": "Translated",
  "preview.title": "Preview",

  "progress.idle": "Ready.",
  "progress.starting": "Starting…",
  "progress.done": "Finished.",
  "progress.failed": "Translation failed: {error}",

  "results.title": "Results",
  "results.mono": "mono",
  "results.dual": "dual",
  "results.open": "Open",
  "results.reveal": "Show",

  // `log.title` went with the log group; the panel heading is now
  // `settings.logTitle`, since the log lives in the General settings panel.
  "log.engineFailed": "engine failed to start: {error}",
  "log.assetCheckFailed": "asset check failed: {error}",
  "log.providersFailed": "could not load providers: {error}",
  "log.engineFactsFailed": "could not read engine details: {error}",
  "log.pageCountFailed": "could not read the page count: {error}",
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

  "dialog.selectPdf": "Select PDF files",
  "dialog.selectOutput": "Select output directory",
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
  "settings.sectionAppearance": "外观",
  "settings.sectionGeneral": "常规",
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
  "docs.choose": "选择文件…",
  "docs.remove": "移除",

  "model.title": "模型",
  "model.providerSection": "服务商",
  "model.providerName": "服务商",
  "model.newProviderPlaceholder": "名称",
  "model.kind": "类型",
  "model.baseUrl": "接口地址",
  "model.apiKey": "API 密钥",
  "model.apiKeySaved": "（已保存 — 留空则保持不变）",
  "model.apiKeyPlaceholder": "sk-…",
  "model.modelSection": "模型",
  "model.modelName": "模型",
  "model.newModelPlaceholder": "别名",
  "model.modelId": "模型 ID",
  "model.reasoning": "推理强度",
  "model.reasoningDefault": "（默认）",
  "model.reasoningLow": "低",
  "model.reasoningMedium": "中",
  "model.reasoningHigh": "高",
  "model.temperature": "温度",
  "model.extraBody": "额外请求体（JSON）",
  "model.add": "添加",
  "model.remove": "移除",
  "model.defaultSuffix": "（默认）",
  "model.noProviders": "（尚未配置任何服务商）",
  "model.noModels": "（暂无模型）",
  "model.noProvider": "未选择服务商。",
  "model.noModel": "未选择模型。",
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

  "options.title": "选项",
  "options.langIn": "源语言",
  "options.langOut": "目标语言",
  "options.mode": "模式",
  "options.modeFast": "fast — ONNX 版面检测",
  "options.modeUltrafast": "ultrafast — 原生文本位置",
  "options.threads": "并发线程",
  "options.pages": "页码范围（可选）",
  "options.output": "输出目录",
  "options.outputPlaceholder": "（与输入文件同目录）",
  "options.translate": "开始翻译",
  "options.cancel": "取消",

  "preview.emptyTitle": "尚未打开文档",
  "preview.emptyHint": "将 PDF 拖到此处，或用左侧的「选择文件…」。",
  "preview.dropUnreadable": "无法读取拖入文件的路径，请改用「选择文件…」。",
  "preview.viewOriginal": "原文",
  "preview.viewTranslated": "译文",
  "preview.viewCompare": "对比",
  "preview.needResults": "请先翻译文档，才能查看译文页面。",
  "preview.page": "页",
  "preview.prevPage": "上一页",
  "preview.nextPage": "下一页",
  "preview.zoomOut": "缩小",
  "preview.zoomIn": "放大",
  "preview.fitWidth": "适应宽度",
  "preview.fitHeight": "适应高度",
  "preview.openFile": "用默认应用打开",
  "preview.revealFile": "在文件夹中显示",
  "preview.labelOriginal": "原文",
  "preview.labelTranslated": "译文",
  "preview.title": "预览",

  "progress.idle": "就绪。",
  "progress.starting": "正在启动…",
  "progress.done": "已完成。",
  "progress.failed": "翻译失败：{error}",

  "results.title": "结果",
  "results.mono": "纯译文",
  "results.dual": "双语对照",
  "results.open": "打开",
  "results.reveal": "显示位置",

  "log.engineFailed": "引擎启动失败：{error}",
  "log.assetCheckFailed": "资源检查失败：{error}",
  "log.providersFailed": "无法加载服务商：{error}",
  "log.engineFactsFailed": "无法读取引擎信息：{error}",
  "log.pageCountFailed": "无法读取页数：{error}",
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

  "dialog.selectPdf": "选择 PDF 文件",
  "dialog.selectOutput": "选择输出目录",
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
