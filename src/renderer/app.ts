/**
 * Renderer logic: file selection, model configuration, job control, progress.
 *
 * Talks to the main process only through `window.pdfxlate` (see preload.ts).
 *
 * Every user-visible string comes from the translation table: static markup is
 * translated by `initI18n()` at startup, and anything built in code goes
 * through `t()`. `onLocaleChange` re-runs those code-built render paths so a
 * language switch updates the whole UI without a restart.
 */

import {
  getLocale,
  initI18n,
  onLocaleChange,
  setLocale,
  t,
  LOCALES,
  LOCALE_LABELS,
} from "./i18n.js";

// One model under a provider, as summarised by `providers.list`.
interface ProviderModelSummary {
  model: string | null;
  reasoning_effort: string | null;
  temperature: number;
}

interface ProviderSummary {
  kind: string;
  base_url: string | null;
  has_api_key: boolean;
  models: Record<string, ProviderModelSummary>;
}

interface ProvidersResponse {
  providers: Record<string, ProviderSummary>;
}

/**
 * The editor's working copy of a model entry.
 *
 * Looser than the backend schema on purpose: `top_p` / `max_tokens` / `stream`
 * are not surfaced in the UI but are carried through a save untouched.
 */
interface ModelDraft {
  model?: string | null;
  temperature?: number;
  top_p?: number | null;
  max_tokens?: number | null;
  stream?: boolean;
  reasoning_effort?: string | null;
  context_length?: number | null;
  extra_body?: Record<string, unknown>;
}

/** The editor's working copy of one provider (endpoint + its models). */
interface ProviderDraft {
  kind?: string;
  base_url?: string | null;
  api_key?: string | null;
  models?: Record<string, ModelDraft>;
}

/** The full config document, as returned by `config.get`. */
type ConfigDoc = Record<string, unknown>;

// Named JobProgress rather than ProgressEvent: the latter is a DOM built-in
// and declaring it here would collide with lib.dom.
interface JobProgress {
  event: "progress";
  job_id?: string;
  stage: string;
  current: number;
  total: number;
  message: string;
}

interface DoneEvent {
  event: "done";
  job_id?: string;
  result: { outputs: Array<{ mono: string; dual: string }> };
}

type SidecarEvent = JobProgress | DoneEvent | { event: "ready"; version: string };

interface PdfxlateApi {
  onReady(cb: (payload: { ok: boolean; error?: string }) => void): () => void;
  getState(): Promise<{ ok: boolean; pending: boolean; error?: string } | null>;
  onEvent(cb: (event: SidecarEvent) => void): () => void;
  onLog(cb: (line: string) => void): () => void;
  request<T>(method: string, params?: Record<string, unknown>): Promise<T>;
  defaultOutputDir(): Promise<string>;
  listPdfs(dir: string): Promise<string[]>;
  pickFolder(locale?: string): Promise<string | null>;
  importPdf(source: string, destDir: string): Promise<string>;
  saveAs(source: string, suggestedName: string, locale?: string): Promise<string | null>;
  onWheel(cb: () => void): () => void;
  pickPdf(locale?: string): Promise<string[]>;
  pickOutputDir(locale?: string): Promise<string | null>;
  reveal(target: string): Promise<void>;
  openPath(target: string): Promise<void>;
  /** Absolute path -> `file://` URL. Synchronous: it is a pure conversion. */
  fileUrl(target: string): string;
  /** Absolute path of a dropped `File`; see the preload for why. */
  pathForFile(file: File): string;
  platform: string;
}

declare global {
  interface Window {
    pdfxlate: PdfxlateApi;
  }
}

export {};

const api = window.pdfxlate;

const $ = <T extends HTMLElement>(id: string): T =>
  document.getElementById(id) as unknown as T;

// --- state -----------------------------------------------------------------

/**
 * The PDFs in the browsed folder, and which of them are ticked for translation.
 *
 * Kept separately because the two answers differ: the list is everything you
 * might open, while `checkedFiles` is only what a job should consume. Nothing
 * is ticked by default — a folder typically holds a paper *and* its `-mono` /
 * `-dual` outputs, and pre-ticking would translate the translations.
 */
let folderFiles: string[] = [];
let checkedFiles = new Set<string>();
/** The folder being browsed, or null before one is chosen. */
let currentFolder: string | null = null;

const FOLDER_STORAGE_KEY = "pdfxlate.folder";

let outputDir: string | null = null;
let activeJobId: string | null = null;

type EngineState = "pending" | "ok" | "error";
let engineState: EngineState = "pending";

let downloadingAssets = false;
let apiKeySaved = false;

/**
 * A progress message is either a translation key (for text this module owns)
 * or a raw string (diagnostics produced by the Python engine, which are not
 * translated). Keeping the key lets a locale switch retranslate it.
 */
type ProgressMessage = { key: string; params?: Record<string, string> } | { raw: string };

let lastProgress: {
  stage: string;
  current: number;
  total: number;
  message: ProgressMessage;
} | null = null;
let lastProfileMessage: {
  key: string;
  params?: Record<string, string>;
  error: boolean;
} | null = null;

// Last `providers.list` summary and the full `config.get` document the model
// editor works against. The summary drives the two selectors and the API-key
// masking (`has_api_key`); the config document carries the model detail fields
// the summary omits (notably `extra_body`) and is what a save posts back.
let cachedProviders: ProvidersResponse | null = null;
let cachedConfig: ConfigDoc | null = null;
// True only once `config.get` has actually succeeded. Guards every save: a
// draft fabricated after a failed load must never be posted back, or it would
// overwrite the settings the load could not read.
let configLoaded = false;
/**
 * Which provider/model the Settings editor is currently showing.
 *
 * Named for what it is: the editing cursor, nothing more. It deliberately has
 * no influence on what a job runs with — that is the Options panel's choice.
 * The two used to be the same variable, so clicking around in Settings silently
 * changed which model the next translation used.
 */
let editingProvider: string | null = null;
let editingModel: string | null = null;

// --- preview state ---------------------------------------------------------

/**
 * Open documents, in the order their tabs appear. One is active at a time.
 *
 * A tab is just a path: any PDF can be opened, including files that are
 * themselves translations, which is what the removed Original/Translated/
 * Compare switch could not express.
 */
let openTabs: string[] = [];
let activeTab: string | null = null;

/**
 * Zoom level: an explicit percentage, or one of the two fit modes.
 *
 * "fit-width" scales a page so its full width is visible; "fit-height" scales
 * it so a whole page fits vertically (the better choice for a wide pane). The
 * two are separate modes rather than one "fit" toggle because which axis is the
 * binding constraint flips with the pane's aspect ratio.
 *
 * Defaults to fit-width because the preview pane is much narrower than a page
 * at 100% — a fixed 100% would open every document with a horizontal scrollbar.
 * Pressing +/- switches to an explicit percentage, as in other viewers.
 *
 * Global rather than per tab: it is a reading preference, and having the zoom
 * jump as you switch documents would be worse than having it uniform.
 */
type ZoomMode = "fit-width" | "fit-height";
let previewZoom: number | ZoomMode = "fit-width";

/**
 * Per-document scroll position and page count.
 *
 * Page number is remembered per tab so switching away and back returns you to
 * what you were reading; the count is cached so the label can show "N / M"
 * without asking the engine again on every switch.
 */
const pageState = new Map<string, { page: number; pages: number }>();

/** Zoom steps the +/- buttons cycle through. */
const ZOOM_STEPS = [50, 75, 100, 125, 150, 200, 300];

// --- settings state --------------------------------------------------------

/**
 * The sidebar either drives a translation job or edits settings.
 *
 * In settings mode the sidebar shows a menu and the right pane shows the
 * matching panel, so the two never share the pane and the task controls are
 * hidden while configuring.
 */
type AppMode = "task" | "settings";
type SettingsSection = "model" | "appearance" | "general";

let appMode: AppMode = "task";
let settingsSection: SettingsSection = "model";
// Cached from `ping`, for the General panel.
let engineVersion: string | null = null;
let engineConfigDir: string | null = null;

// --- sidebar collapse ------------------------------------------------------

/**
 * Whether the left sidebar is slid out of view.
 *
 * Held as a class on `<body>` rather than as per-element state because there
 * are two sidebars (task and settings) and both have to move together; the
 * class also survives the mode switch without re-applying anything.
 */
const SIDEBAR_STORAGE_KEY = "pdfxlate.sidebarCollapsed";
let sidebarCollapsed = false;

function paintSidebarCollapsed(): void {
  document.body.classList.toggle("sidebar-collapsed", sidebarCollapsed);
  $("btn-toggle-sidebar").setAttribute("aria-expanded", String(!sidebarCollapsed));
}

function setSidebarCollapsed(collapsed: boolean): void {
  sidebarCollapsed = collapsed;
  paintSidebarCollapsed();
  try {
    localStorage.setItem(SIDEBAR_STORAGE_KEY, collapsed ? "1" : "0");
  } catch {
    // Storage can be unavailable (private mode, quota); the toggle still works
    // for this session, it just will not be remembered.
  }
}

$("btn-toggle-sidebar").addEventListener("click", () =>
  setSidebarCollapsed(!sidebarCollapsed),
);

try {
  sidebarCollapsed = localStorage.getItem(SIDEBAR_STORAGE_KEY) === "1";
} catch {
  sidebarCollapsed = false;
}
paintSidebarCollapsed();

// --- pinned overlays -------------------------------------------------------

/**
 * Whether the document's corner controls stay on screen.
 *
 * Two states, and the default is the quieter one: hidden until wanted, which
 * keeps the page unobstructed. Pinned is for when you keep reaching for those
 * controls and would rather they never went away.
 */
const PIN_STORAGE_KEY = "pdfxlate.overlaysPinned";
let overlaysPinned = false;

function paintOverlaysPinned(): void {
  document.body.classList.toggle("overlays-pinned", overlaysPinned);
  const button = $("btn-toggle-overlays");
  button.setAttribute("aria-pressed", String(overlaysPinned));
  // Both keys named literally (rather than via a variable) so the i18n check
  // can see them. The tooltip names what pressing the button will do, so it
  // reads as an action rather than as a state to decode.
  const label = overlaysPinned
    ? t("titlebar.unpinOverlays")
    : t("titlebar.pinOverlays");
  button.title = label;
  button.setAttribute("aria-label", label);
}

function setOverlaysPinned(pinned: boolean): void {
  overlaysPinned = pinned;
  paintOverlaysPinned();
  try {
    localStorage.setItem(PIN_STORAGE_KEY, pinned ? "1" : "0");
  } catch {
    // Storage can be unavailable; the toggle still works for this session.
  }
}

$("btn-toggle-overlays").addEventListener("click", () =>
  setOverlaysPinned(!overlaysPinned),
);

try {
  overlaysPinned = localStorage.getItem(PIN_STORAGE_KEY) === "1";
} catch {
  overlaysPinned = false;
}
paintOverlaysPinned();

// --- sidebar width ---------------------------------------------------------

/**
 * Drag-to-resize for the sidebar.
 *
 * Only `--sidebar-width` is written. The width and the slide-out distance for
 * collapsing are both derived from it, so the collapse keeps working untouched,
 * and there is exactly one value to remember.
 */
const WIDTH_STORAGE_KEY = "pdfxlate.sidebarWidth";
const MIN_SIDEBAR = 240;
const MAX_SIDEBAR = 600;

/**
 * Clamp a width to something that leaves the document usable.
 *
 * The upper bound is also a share of the window, since 600px is comfortable at
 * 1280 but would leave almost nothing for the page on a narrow window. The
 * window's minimum width is 1000, so half of it still leaves the preview half.
 */
function clampSidebarWidth(width: number): number {
  const byWindow = Math.max(MIN_SIDEBAR, Math.floor(window.innerWidth / 2));
  return Math.round(Math.min(Math.max(width, MIN_SIDEBAR), Math.min(MAX_SIDEBAR, byWindow)));
}

function applySidebarWidth(width: number, persist = true): void {
  const clamped = clampSidebarWidth(width);
  document.documentElement.style.setProperty("--sidebar-width", `${clamped}px`);
  if (!persist) return;
  try {
    localStorage.setItem(WIDTH_STORAGE_KEY, String(clamped));
  } catch {
    // Unavailable storage only costs the memory of the width.
  }
}

function restoreSidebarWidth(): void {
  let stored: number | null = null;
  try {
    const raw = localStorage.getItem(WIDTH_STORAGE_KEY);
    stored = raw === null ? null : Number(raw);
  } catch {
    stored = null;
  }
  // Re-clamp on restore: the value may have been saved on a wider window, or
  // been edited by hand, and a bad width must not break the layout.
  applySidebarWidth(
    stored !== null && Number.isFinite(stored) ? stored : 340,
    false,
  );
}

/**
 * Drag the sidebar's edge to set its width.
 *
 * The move and release listeners are on `window`, not on the handle. The handle
 * is only 5px wide, and dragging means leaving it immediately — and once the
 * pointer is over the preview it is over an `<iframe>`, a separate document
 * whose events never reach this one. Listening on `window` keeps the gesture
 * alive across the whole width of the app.
 *
 * `setPointerCapture` would be the other way to do this, but it does not take
 * effect under synthetic (and, in this app's case, real) pointer input here —
 * measured: moves reached `window` while the handle never saw them. Acting on a
 * plain flag instead is both simpler and demonstrably working.
 */
const resizer = $("sidebar-resizer");
let resizing = false;

resizer.addEventListener("pointerdown", (e) => {
  e.preventDefault();
  resizing = true;
  document.body.classList.add("is-resizing");
});

window.addEventListener("pointermove", (e) => {
  if (!resizing) return;
  // The sidebar starts at the window's left edge, so the pointer's x is the
  // width being asked for.
  applySidebarWidth(e.clientX, false);
});

function endResize(): void {
  if (!resizing) return;
  resizing = false;
  document.body.classList.remove("is-resizing");
  // Persist on release rather than on every move, so a drag writes once.
  const current = parseFloat(
    getComputedStyle(document.documentElement).getPropertyValue("--sidebar-width"),
  );
  if (Number.isFinite(current)) applySidebarWidth(current);
}

window.addEventListener("pointerup", endResize);
window.addEventListener("pointercancel", endResize);

// A window resize can make a stored width too wide for the new size.
window.addEventListener("resize", () => {
  const current = parseFloat(
    getComputedStyle(document.documentElement).getPropertyValue("--sidebar-width"),
  );
  if (Number.isFinite(current)) applySidebarWidth(current, false);
});

restoreSidebarWidth();

// --- locale ----------------------------------------------------------------

/**
 * Tag the platform on `<body>` so CSS can reserve the right space for the
 * native window buttons.
 *
 * `titleBarOverlay` paints the minimise/maximise/close buttons in the top-right
 * (top-left on macOS, where they are traffic lights), and nothing in the DOM
 * knows where they are — the reserved padding has to be per platform.
 */
document.body.classList.add(`platform-${api.platform}`);

function renderLocaleSelect(): void {
  const select = $<HTMLSelectElement>("locale-select");
  select.innerHTML = "";
  for (const locale of LOCALES) {
    const opt = document.createElement("option");
    opt.value = locale;
    opt.textContent = LOCALE_LABELS[locale];
    select.append(opt);
  }
  select.value = getLocale();
}

$("locale-select").addEventListener("change", (e) => {
  setLocale((e.target as HTMLSelectElement).value as (typeof LOCALES)[number]);
});

// Translate the static markup and pick the startup locale before anything is
// rendered, so the first pass builds runtime strings in the right language.
initI18n();
renderLocaleSelect();

onLocaleChange(() => {
  renderLocaleSelect();
  paintEngineStatus();
  renderFiles();
  renderTabs();
  renderFolderLabel();
  paintApiKeyPlaceholder();
  renderDownloadButton();
  paintProgress();
  paintProfileMessage();
  renderProviderSelect();
  renderModelSelect();
  renderOptionModels();
  paintOptionSelects();
  paintOverlaysPinned();
  updatePreviewControls();
  renderPreview();
  paintSettingsMode();
  paintEngineFacts();
});

// --- settings mode ---------------------------------------------------------

/** Switch between running a job and editing settings. */
function setAppMode(mode: AppMode): void {
  appMode = mode;
  paintSettingsMode();
}

function setSettingsSection(section: SettingsSection): void {
  settingsSection = section;
  paintSettingsMode();
}

/**
 * Repaint the whole shell for the current mode.
 *
 * Only one of the two sidebars and one of the two right-hand panes is ever
 * visible, so the mode is expressed entirely through these four toggles.
 */
function paintSettingsMode(): void {
  const inSettings = appMode === "settings";
  $("sidebar-task").classList.toggle("hidden", inSettings);
  $("sidebar-settings").classList.toggle("hidden", !inSettings);
  // The tabs belong to the document pane, which settings replaces, so they hide
  // with it rather than offering to open a file into a pane that is not there.
  $("tabbar").classList.toggle("hidden", inSettings);
  $("preview-host").classList.toggle("hidden", inSettings);
  $("settings-host").classList.toggle("hidden", !inSettings);

  for (const button of document.querySelectorAll<HTMLElement>(".settings-menu__item")) {
    button.classList.toggle("is-active", button.dataset.section === settingsSection);
  }
  for (const panel of document.querySelectorAll<HTMLElement>(".settings-panel")) {
    panel.classList.toggle("hidden", panel.dataset.panel !== settingsSection);
  }
  // The model editor is live in the settings panel; make sure it reflects the
  // cached config whenever the panel is (re)opened.
  if (inSettings && settingsSection === "model") {
    renderProviderSelect();
    renderModelSelect();
    applyProviderFields();
    applyModelFields();
  }
  if (inSettings && settingsSection === "general") paintEngineFacts();
}

/** Fill the General panel's read-only diagnostics. */
function paintEngineFacts(): void {
  $("engine-version").textContent = engineVersion ?? t("settings.unknown");
  $("engine-config-dir").textContent = engineConfigDir ?? t("settings.unknown");
}

$("btn-open-settings").addEventListener("click", () => setAppMode("settings"));
$("btn-close-settings").addEventListener("click", () => setAppMode("task"));
for (const button of document.querySelectorAll<HTMLElement>(".settings-menu__item")) {
  button.addEventListener("click", () =>
    setSettingsSection(button.dataset.section as SettingsSection),
  );
}

// --- engine status ---------------------------------------------------------

function applyEngineState(payload: { ok: boolean; error?: string }): void {
  engineState = payload.ok ? "ok" : "error";
  paintEngineStatus();
  if (payload.ok) {
    void refreshProviders();
    void refreshAssets();
    void refreshEngineFacts();
  } else {
    appendLog(t("log.engineFailed", { error: payload.error ?? t("log.unknownError") }));
  }
  updateTranslateButton();
}

/** Read the engine's version and config location for the General panel. */
async function refreshEngineFacts(): Promise<void> {
  try {
    const info = await api.request<{ version: string; config_dir: string }>("ping");
    engineVersion = info.version;
    engineConfigDir = info.config_dir;
  } catch (err) {
    appendLog(t("log.engineFactsFailed", { error: String(err) }));
  }
  paintEngineFacts();
}

function paintEngineStatus(): void {
  const status = $("engine-status");
  if (engineState === "ok") {
    status.textContent = t("status.ready");
    status.className = "status status--ok";
  } else if (engineState === "error") {
    status.textContent = t("status.failed");
    status.className = "status status--error";
  } else {
    status.textContent = t("status.starting");
    status.className = "status status--pending";
  }
}

// Listen for the event, and also query the current state: the engine may
// already be up by the time this script runs, in which case the one-shot
// event has been missed.
api.onReady(applyEngineState);
void api.getState().then((state) => {
  if (state && !state.pending) applyEngineState(state);
});

api.onLog((line) => appendLog(line));

api.onEvent((event) => {
  if (event.event === "progress") handleProgress(event);
  else if (event.event === "done") handleDone(event);
});

// --- first-run assets ------------------------------------------------------

async function refreshAssets(): Promise<void> {
  try {
    const status = await api.request<Record<string, boolean>>("assets.status");
    const missing = !status.layout_model || !status.font_zh;
    $("setup-banner").classList.toggle("hidden", !missing);
  } catch (err) {
    appendLog(t("log.assetCheckFailed", { error: String(err) }));
  }
}

function renderDownloadButton(): void {
  const button = $<HTMLButtonElement>("btn-download-assets");
  button.disabled = downloadingAssets;
  button.textContent = t(downloadingAssets ? "setup.downloading" : "setup.download");
}

$("btn-download-assets").addEventListener("click", async () => {
  downloadingAssets = true;
  renderDownloadButton();
  setProgress("setup", 0, 1, { key: "setup.downloading" });
  try {
    await api.request("assets.download", { targets: ["layout_model", "fonts"], languages: ["zh"] });
    $("setup-banner").classList.add("hidden");
    setProgress("setup", 1, 1, { key: "setup.ready" });
  } catch (err) {
    setProgress("setup", 0, 1, { key: "setup.failed", params: { error: String(err) } });
  } finally {
    downloadingAssets = false;
    renderDownloadButton();
  }
});

// --- folder browsing -------------------------------------------------------

/** Base name of a path, for display. */
function baseName(file: string): string {
  return file.split(/[\\/]/).pop() ?? file;
}

/**
 * List a folder's PDFs.
 *
 * Files that were dropped in from elsewhere are folded in too, so anything you
 * have open is also something you can tick — otherwise a dropped file would be
 * visible in a tab but absent from the list it is supposed to be translated
 * from.
 */
async function openFolder(dir: string): Promise<void> {
  try {
    const found = await api.listPdfs(dir);
    currentFolder = dir;
    try {
      localStorage.setItem(FOLDER_STORAGE_KEY, dir);
    } catch {
      // Storage may be unavailable; the folder still works for this session.
    }
    mergeFolderFiles(found);
  } catch (err) {
    // A remembered folder that has since been deleted is an ordinary outcome.
    appendLog(t("log.folderFailed", { error: String(err) }));
    currentFolder = null;
    folderFiles = [];
    renderFiles();
  }
}

/** Union the folder contents with files already open, keeping folder order. */
function mergeFolderFiles(found: string[]): void {
  const merged = [...found];
  for (const path of openTabs) {
    if (!merged.includes(path)) merged.push(path);
  }
  folderFiles = merged;
  // A ticked file that disappeared from the folder must not stay ticked.
  for (const file of [...checkedFiles]) {
    if (!folderFiles.includes(file)) checkedFiles.delete(file);
  }
  renderFiles();
}

function renderFolderLabel(): void {
  const label = $("folder-label");
  if (currentFolder) {
    label.textContent = currentFolder;
    label.title = currentFolder;
    label.classList.remove("muted");
  } else {
    label.textContent = t("docs.noFolder");
    label.title = "";
    label.classList.add("muted");
  }
}

function renderFiles(): void {
  const list = $("file-list");
  list.innerHTML = "";
  for (const file of folderFiles) {
    const li = document.createElement("li");
    li.className = "file-row";

    // The checkbox marks what a job will translate, which is a different
    // question from which file the preview is showing.
    const tick = document.createElement("input");
    tick.type = "checkbox";
    tick.className = "file-row__tick";
    tick.checked = checkedFiles.has(file);
    tick.title = t("docs.includeInJob");
    tick.addEventListener("change", () => {
      if (tick.checked) checkedFiles.add(file);
      else checkedFiles.delete(file);
      updateTranslateButton();
    });

    // The name opens the document, rather than a separate button: a list of
    // files reads as something you click to open.
    const name = document.createElement("button");
    name.className = "file-row__name";
    name.textContent = baseName(file);
    name.title = file;
    name.classList.toggle("is-open", openTabs.includes(file));
    name.classList.toggle("is-active", activeTab === file);
    name.addEventListener("click", () => openDocument(file));

    li.append(tick, name);
    list.append(li);
  }
  renderFolderLabel();
  updateTranslateButton();
}

$("btn-pick-folder").addEventListener("click", async () => {
  const dir = await api.pickFolder(getLocale());
  if (dir) await openFolder(dir);
});

// --- tabs ------------------------------------------------------------------

function renderTabs(): void {
  const bar = $("tabbar");
  bar.innerHTML = "";
  for (const file of openTabs) {
    const tab = document.createElement("div");
    tab.className = "tab";
    tab.classList.toggle("is-active", activeTab === file);
    // The full path in the tooltip: two folders can hold the same file name.
    tab.title = file;

    const name = document.createElement("span");
    name.className = "tab__name";
    name.textContent = baseName(file);

    const close = document.createElement("button");
    close.className = "tab__close";
    close.textContent = "×";
    close.title = t("tab.close");
    close.addEventListener("click", (e) => {
      // Without this the click also lands on the tab underneath and re-selects
      // the document that is being closed.
      e.stopPropagation();
      closeTab(file);
    });

    // Clicking anywhere on the tab activates it, matching how tabs behave
    // elsewhere; the close button is the only part that acts differently.
    tab.addEventListener("click", () => activateTab(file));

    tab.append(name, close);
    bar.append(tab);
  }
}

/** Open a document, or focus its tab if it is already open. */
function openDocument(file: string): void {
  if (!openTabs.includes(file)) openTabs.push(file);
  if (!folderFiles.includes(file)) folderFiles.push(file);
  activateTab(file);
  renderFiles();
}

function activateTab(file: string): void {
  activeTab = file;
  renderTabs();
  renderFiles();
  // Ask for the count only when this document has never been shown; the answer
  // does not change while the app runs.
  if (!pageState.has(file)) void refreshPageCount();
  updatePreviewControls();
  renderPreview();
}

function closeTab(file: string): void {
  const index = openTabs.indexOf(file);
  if (index < 0) return;
  openTabs.splice(index, 1);
  pageState.delete(file);
  if (activeTab === file) {
    // Prefer the tab to the right, which is where the one being closed was
    // pointing; fall back to the left, then to nothing open at all.
    activeTab = openTabs[index] ?? openTabs[index - 1] ?? null;
    if (activeTab) {
      if (!pageState.has(activeTab)) void refreshPageCount();
    }
  }
  renderTabs();
  renderFiles();
  updatePreviewControls();
  renderPreview();
}

// --- preview pane ----------------------------------------------------------

/** The document the preview shows: the active tab, or null. */
function previewSource(): string | null {
  return activeTab;
}

/** The page/count record for a document, creating it if needed. */
function stateFor(file: string): { page: number; pages: number } {
  let state = pageState.get(file);
  if (!state) {
    state = { page: 1, pages: 0 };
    pageState.set(file, state);
  }
  return state;
}

/** Ask the engine how many pages a document has. */
async function refreshPageCount(): Promise<void> {
  const file = activeTab;
  if (!file) return;
  try {
    // `pdf.info` may be missing on an older engine; an unknown count is fine —
    // the controls then show the page number alone.
    const info = await api.request<{ pages: number }>("pdf.info", { path: file });
    // The active tab may have changed while this was in flight.
    if (!pageState.has(file)) return;
    stateFor(file).pages = info.pages ?? 0;
  } catch (err) {
    stateFor(file).pages = 0;
    appendLog(t("log.pageCountFailed", { error: String(err) }));
  }
  updatePreviewControls();
}

/**
 * Build the viewer URL for one file.
 *
 * The fragment is what drives the built-in viewer: `toolbar=0&navpanes=0`
 * hides its own chrome (that is why the app can supply a toolbar of its own),
 * and `page` / `zoom` / `view` set the initial view.
 *
 * Fit-width maps to `zoom=page-width`, which this viewer honours. Fit-height
 * does NOT: `zoom=page-height` and `zoom=page-fit` are accepted but ignored in
 * this Chromium build, whereas `view=FitV` does resize the rendered page. So
 * the two fit modes use two different fragment parameters.
 */
function viewerUrl(file: string): string {
  const fragment =
    previewZoom === "fit-width"
      ? "zoom=page-width"
      : previewZoom === "fit-height"
        ? "view=FitV"
        : `zoom=${previewZoom}`;
  const page = stateFor(file).page;
  return `${api.fileUrl(file)}#toolbar=0&navpanes=0&page=${page}&${fragment}`;
}

/**
 * Repaint the preview for the active tab, page and zoom.
 *
 * The frame is built fresh rather than having its `src` updated: changing the
 * fragment of an already-loaded viewer frame does NOT navigate, so replacing
 * the element is the only way to move to another page or zoom level.
 */
function renderPreview(): void {
  const host = $("preview-host");
  // Every overlay lives inside the host, so clearing it would delete them along
  // with the frame. Detach and re-append them: they are absolutely positioned,
  // so their place in the flow does not matter.
  const overlays = VIEWER_OVERLAYS.map((id) => $(id));
  host.innerHTML = "";
  host.append(...overlays);
  const source = previewSource();
  if (!source) {
    const empty = document.createElement("div");
    empty.className = "preview__empty";
    const title = document.createElement("p");
    title.className = "preview__empty-title";
    title.textContent = t("preview.emptyTitle");
    const hint = document.createElement("p");
    hint.className = "preview__empty-hint";
    hint.textContent = t("preview.emptyHint");
    empty.append(title, hint);
    host.append(empty);
    return;
  }

  const frame = document.createElement("iframe");
  frame.className = "preview__frame";
  frame.src = viewerUrl(source);
  // A drag may be in flight; a freshly built frame must not become droppable
  // again underneath it.
  if (!frameDroppable) frame.style.pointerEvents = "none";
  host.append(frame);
}

/** Enable/disable the viewer controls and paint the page and zoom readouts. */
function updatePreviewControls(): void {
  const hasFile = previewSource() !== null;
  const state = activeTab ? stateFor(activeTab) : null;

  // Fit buttons: the matching one lights up, and neither does while an explicit
  // percentage is set. Both are unavailable without a document.
  $<HTMLButtonElement>("fit-width").disabled = !hasFile;
  $<HTMLButtonElement>("fit-height").disabled = !hasFile;
  $("fit-width").classList.toggle("is-active", previewZoom === "fit-width");
  $("fit-height").classList.toggle("is-active", previewZoom === "fit-height");

  const page = state?.page ?? 1;
  const pages = state?.pages ?? 0;
  const known = pages > 0;
  $("page-label").textContent = hasFile
    ? known
      ? `${page} / ${pages}`
      : String(page)
    : "–";
  $<HTMLButtonElement>("page-prev").disabled = !hasFile || page <= 1;
  $<HTMLButtonElement>("page-next").disabled = !hasFile || (known && page >= pages);

  $("zoom-label").textContent =
    previewZoom === "fit-width"
      ? t("preview.fitWidth")
      : previewZoom === "fit-height"
        ? t("preview.fitHeight")
        : `${previewZoom}%`;
  $<HTMLButtonElement>("zoom-out").disabled = !hasFile || previewZoom === ZOOM_STEPS[0];
  $<HTMLButtonElement>("zoom-in").disabled =
    !hasFile || previewZoom === ZOOM_STEPS.at(-1)!;
  $<HTMLButtonElement>("btn-open-file").disabled = !hasFile;
  $<HTMLButtonElement>("btn-save-as").disabled = !activeTab;

  // The overlays are meaningless without a document; hide them outright rather
  // than relying on the auto-hide timer, which only runs after an interaction.
  for (const id of VIEWER_OVERLAYS) $(id).classList.toggle("hidden", !hasFile);
}

/**
 * The file "save a copy" should copy: the document on screen.
 *
 * Unambiguous now that a tab holds a single document — there is no second pane
 * to choose between.
 */
function saveAsSource(): string | null {
  return activeTab;
}

$("btn-save-as").addEventListener("click", async () => {
  const file = saveAsSource();
  if (!file) return;
  const suggested = baseName(file);
  try {
    const saved = await api.saveAs(file, suggested, getLocale());
    // A cancelled dialog returns null; that is a normal outcome, not a failure.
    if (saved) appendLog(t("log.savedAs", { path: saved }));
  } catch (err) {
    appendLog(t("log.saveAsFailed", { error: String(err) }));
  }
});

/**
 * Show the viewer overlays for a moment, then let them fade.
 *
 * Both overlay chips (paging/zoom bottom-right, save top-right) stay in the DOM
 * at `opacity: 0` so CSS `:hover` can still reveal them; this timer is the
 * second, interaction-driven trigger — changing page, zoom, or scrolling
 * surfaces the controls without the pointer having to find them. Every call
 * restarts the clock so rapid interaction does not hide them mid-sequence.
 */
let overlayFlashTimer: ReturnType<typeof setTimeout> | null = null;

/** Every overlay chip that shares the reveal rule. */
const VIEWER_OVERLAYS = ["viewer-fit", "viewer-overlay", "viewer-saveas"] as const;

function flashViewerOverlay(): void {
  for (const id of VIEWER_OVERLAYS) $(id).classList.add("is-visible");
  if (overlayFlashTimer !== null) clearTimeout(overlayFlashTimer);
  overlayFlashTimer = setTimeout(() => {
    for (const id of VIEWER_OVERLAYS) $(id).classList.remove("is-visible");
    overlayFlashTimer = null;
  }, 1600);
}

/**
 * Whether the pointer is currently over the preview area.
 *
 * Needed because scroll gestures cannot be seen from here: the PDF viewer runs
 * in another process, so its wheel events never reach this document. The main
 * process sees the wheel but not where it happened, so it forwards a bare ping
 * and this flag supplies the missing half. `mouseenter`/`mouseleave` on the
 * frame's container do fire in this document, which is what makes the pairing
 * possible at all.
 */
let pointerOverPreview = false;

function trackPointerOverPreview(): void {
  const host = $("preview-host");
  host.addEventListener("mouseenter", () => {
    pointerOverPreview = true;
  });
  host.addEventListener("mouseleave", () => {
    pointerOverPreview = false;
  });
  // A wheel anywhere else in the window must not reveal controls that belong to
  // the document, so the ping is ignored unless the pointer is over the preview.
  api.onWheel(() => {
    if (pointerOverPreview) flashViewerOverlay();
  });
}

/** Switch to a fit mode (or back to fit-width) from the title-bar buttons. */
function setZoomMode(mode: ZoomMode): void {
  if (!previewSource()) return;
  if (previewZoom === mode) return;
  previewZoom = mode;
  updatePreviewControls();
  renderPreview();
  // The overlay's zoom readout just changed, so surface it (same rule as
  // stepZoom). setViewMode does not flash: it changes nothing the overlay shows.
  flashViewerOverlay();
}

$("fit-width").addEventListener("click", () => setZoomMode("fit-width"));
$("fit-height").addEventListener("click", () => setZoomMode("fit-height"));

function stepPage(delta: number): void {
  const file = activeTab;
  if (!file) return;
  const state = stateFor(file);
  const next = state.page + delta;
  if (next < 1) return;
  if (state.pages > 0 && next > state.pages) return;
  state.page = next;
  updatePreviewControls();
  renderPreview();
  flashViewerOverlay();
}

$("page-prev").addEventListener("click", () => stepPage(-1));
$("page-next").addEventListener("click", () => stepPage(1));

function stepZoom(delta: number): void {
  // From either fit mode, the first press lands on the step nearest to 100% so
  // the transition is predictable rather than jumping to an extreme.
  if (typeof previewZoom === "string") {
    previewZoom = ZOOM_STEPS[delta > 0 ? ZOOM_STEPS.indexOf(125) : ZOOM_STEPS.indexOf(75)];
    updatePreviewControls();
    renderPreview();
    flashViewerOverlay();
    return;
  }
  const current: number = previewZoom;
  const index = ZOOM_STEPS.indexOf(current);
  // An off-step value can only come from outside the buttons; snap to the
  // nearest step at or above it so the direction of travel is preserved.
  const from = index >= 0 ? index : ZOOM_STEPS.findIndex((v) => v >= current);
  const next = ZOOM_STEPS[
    Math.min(ZOOM_STEPS.length - 1, Math.max(0, (from < 0 ? 0 : from) + delta))
  ];
  if (next === current) return;
  previewZoom = next;
  updatePreviewControls();
  renderPreview();
  flashViewerOverlay();
}

$("zoom-out").addEventListener("click", () => stepZoom(-1));
$("zoom-in").addEventListener("click", () => stepZoom(1));

$("btn-open-file").addEventListener("click", () => {
  const file = previewSource();
  if (file) void api.openPath(file);
});

// --- canvas drop target ----------------------------------------------------

/**
 * Dropping a PDF anywhere on the document area opens it.
 *
 * The path comes from `api.pathForFile`, not from `File.path`: Electron removed
 * that property in v32, and reading it silently yields `undefined`, which made
 * this handler a no-op. The `filter(Boolean)` below is what hid that — it turns
 * "every path is undefined" into "nothing was dropped".
 *
 * An open document covers this area with the viewer's `<iframe>`, which is a
 * separate document and would take the drop itself; measured directly, the
 * parent receives `dragenter` but never `drop`. So a drag in progress switches
 * the frame off, letting the drop land here. Without this, dropping would work
 * only when nothing was open — exactly backwards for a drop zone.
 */
const previewHost = $("preview-host");

/**
 * Whether the viewer frame currently accepts pointer events.
 *
 * Tracked as well as applied, because `renderPreview()` builds a fresh frame
 * and a new element would come back interactive even in the middle of a drag.
 */
let frameDroppable = true;

function setFrameDroppable(droppable: boolean): void {
  frameDroppable = droppable;
  for (const frame of previewHost.querySelectorAll<HTMLIFrameElement>(".preview__frame")) {
    frame.style.pointerEvents = droppable ? "" : "none";
  }
}

// Listened for on `document`, not on the host: with a document open the frame
// swallows events aimed at the host, and this is the one event that still
// reaches the parent, so it is where the frame has to be switched off.
document.addEventListener("dragenter", () => {
  setFrameDroppable(false);
  previewHost.classList.add("preview__host--over");
});
document.addEventListener("dragleave", (e) => {
  // Only when the pointer has actually left the window; moving between elements
  // fires this constantly and would make the highlight flicker.
  if (!e.relatedTarget) {
    setFrameDroppable(true);
    previewHost.classList.remove("preview__host--over");
  }
});
previewHost.addEventListener("dragover", (e) => {
  e.preventDefault();
  previewHost.classList.add("preview__host--over");
});
previewHost.addEventListener("drop", (e) => {
  e.preventDefault();
  setFrameDroppable(true);
  previewHost.classList.remove("preview__host--over");
  const files = Array.from(e.dataTransfer?.files ?? []);
  const paths = files.map((f) => api.pathForFile(f)).filter((p): p is string => Boolean(p));
  if (!paths.length) {
    appendLog(t("preview.dropUnreadable"));
    return;
  }
  void handleDropped(paths);
});

/**
 * Open dropped files, filing each into the browsed folder first.
 *
 * A drop is a request to work with a document, and the folder is where this app
 * looks for documents, so anything arriving from elsewhere is copied in — that
 * is also what makes it show up in the list and become tickable for a job. A
 * file already in the folder is opened in place rather than duplicated, and a
 * name that is already taken gets a suffix rather than replacing what is there.
 *
 * Without a folder there is nowhere to file anything, so the document is simply
 * opened and the omission mentioned once.
 */
async function handleDropped(paths: string[]): Promise<void> {
  const pdfs = paths.filter((p) => p.toLowerCase().endsWith(".pdf"));
  for (const p of paths) {
    if (!pdfs.includes(p)) appendLog(t("drop.notPdf", { name: baseName(p) }));
  }
  if (!pdfs.length) return;

  if (!currentFolder) {
    appendLog(t("drop.noFolder"));
    for (const file of pdfs) openDocument(file);
    return;
  }

  for (const file of pdfs) {
    try {
      const placed = await api.importPdf(file, currentFolder);
      if (placed !== file) appendLog(t("drop.copied", { path: placed }));
      openDocument(placed);
    } catch (err) {
      // A failed copy must not lose the document: open it where it is.
      appendLog(t("drop.importFailed", { error: String(err) }));
      openDocument(file);
    }
  }
  // Pick up whatever was just added to the folder.
  await openFolder(currentFolder);
}

// --- collapsible groups ----------------------------------------------------

/**
 * Open one group and close the others.
 *
 * The sidebar is narrow and the groups are long, so keeping exactly one open
 * keeps the relevant controls in view without manual scrolling. `null` closes
 * nothing (used when a group should merely be revealed).
 */
function expandGroup(id: string): void {
  const target = document.getElementById(id) as HTMLDetailsElement | null;
  if (!target) return;
  target.open = true;
}

// --- providers & models ----------------------------------------------------

/**
 * Reload the provider summary and the full config document.
 *
 * The two are fetched together because the editor needs both: the draft config
 * is the source of truth for the editor's structure (provider names, model
 * aliases, defaults, detail fields), while the summary supplies `has_api_key`,
 * which the config document's expanded key cannot be trusted to answer after a
 * user edit.
 */
async function refreshProviders(): Promise<void> {
  try {
    const [summary, config] = await Promise.all([
      api.request<ProvidersResponse>("providers.list"),
      api.request<ConfigDoc>("config.get"),
    ]);
    cachedProviders = summary;
    cachedConfig = config;
    configLoaded = true;
    // Keep the current selection if it still exists; otherwise fall back to
    // the configured default, then to the first entry.
    editingProvider = pickProvider();
    editingModel = editingProvider ? pickModel(editingProvider) : null;
    renderProviderSelect();
    renderModelSelect();
    // The options panel lists the same pairs, and it reads from this cached
    // config, so it has to be rebuilt whenever the config is reloaded.
    renderOptionModels();
    applyProviderFields();
    applyModelFields();
  } catch (err) {
    appendLog(t("log.providersFailed", { error: String(err) }));
  }
}

/**
 * Which provider the editor should show: the one already being edited if it
 * still exists, otherwise the first.
 *
 * There is no "default" to prefer any more — this is the editing cursor, not a
 * choice about what to run.
 */
function pickProvider(): string | null {
  const names = draftProviderNames();
  if (editingProvider && names.includes(editingProvider)) return editingProvider;
  return names[0] ?? null;
}

/** Which model to show within `provider`: the one being edited, else the first. */
function pickModel(provider: string): string | null {
  const models = draftModelNames(provider);
  if (editingModel && models.includes(editingModel)) return editingModel;
  return models[0] ?? null;
}

/** Rebuild the provider `<select>` from the draft config. */
function renderProviderSelect(): void {
  const select = $<HTMLSelectElement>("provider-select");
  select.innerHTML = "";
  const names = draftProviderNames();
  if (!names.length) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = t("model.noProviders");
    select.append(opt);
    select.disabled = true;
    return;
  }
  select.disabled = false;
  for (const name of names) {
    const opt = document.createElement("option");
    opt.value = name;
    // No "(default)" marker: nothing is the default, this is just which entry
    // is open in the editor.
    opt.textContent = name;
    if (name === editingProvider) opt.selected = true;
    select.append(opt);
  }
}

/** Rebuild the model `<select>` for the current provider. */
function renderModelSelect(): void {
  const select = $<HTMLSelectElement>("model-select");
  select.innerHTML = "";
  const names = editingProvider ? draftModelNames(editingProvider) : [];
  if (!names.length) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = t("model.noModels");
    select.append(opt);
    select.disabled = true;
    return;
  }
  select.disabled = false;
  for (const name of names) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    if (name === editingModel) opt.selected = true;
    select.append(opt);
  }
}

/** Fill the provider-level fields from the cached config, and mask the key. */
function applyProviderFields(): void {
  const provider = draftProvider(editingProvider);
  $<HTMLInputElement>("field-kind").value = provider?.kind ?? "";
  $<HTMLInputElement>("field-base-url").value = provider?.base_url ?? "";
  // The key field always starts empty: the stored secret is never shown back,
  // only the `has_api_key` flag the summary reports.
  $<HTMLInputElement>("field-api-key").value = "";
  apiKeySaved = !!cachedProviders?.providers[editingProvider ?? ""]?.has_api_key;
  paintApiKeyPlaceholder();
}

/** Fill the model-level fields from the cached config. */
function applyModelFields(): void {
  const model = draftModel(editingProvider, editingModel);
  $<HTMLInputElement>("field-model-id").value = model?.model ?? "";
  $<HTMLSelectElement>("field-reasoning").value = model?.reasoning_effort ?? "";
  $<HTMLInputElement>("field-temperature").value = String(model?.temperature ?? 0);
  // Empty means "unset", which is different from 0 for these: leaving them
  // blank lets the API apply its own default.
  $<HTMLInputElement>("field-max-tokens").value =
    model?.max_tokens === null || model?.max_tokens === undefined ? "" : String(model.max_tokens);
  $<HTMLInputElement>("field-top-p").value =
    model?.top_p === null || model?.top_p === undefined ? "" : String(model.top_p);
  $<HTMLInputElement>("field-context-length").value =
    model?.context_length === null || model?.context_length === undefined
      ? ""
      : String(model.context_length);
  $<HTMLInputElement>("field-stream").checked = model?.stream === true;
  paintMaxTokensHint(model?.context_length ?? null);
  const extra = model?.extra_body;
  $<HTMLInputElement>("field-extra-body").value =
    extra && Object.keys(extra).length ? JSON.stringify(extra) : "";
}

/**
 * Suggest a `max_tokens` alongside the context length.
 *
 * A quarter of the window is the common convention, and it is only ever a
 * placeholder: the engine will not invent a `max_tokens`, because truncating a
 * reply is worse than leaving the choice to the API. Showing the number makes
 * the relationship visible without pretending to enforce it.
 */
function paintMaxTokensHint(contextLength: number | null): void {
  const field = $<HTMLInputElement>("field-max-tokens");
  field.placeholder = contextLength
    ? t("model.maxTokensAutoValue", { value: String(Math.round(contextLength / 4)) })
    : t("model.maxTokensAuto");
}

/** Parse an optional positive integer field; `null` when blank. */
function readOptionalInt(id: string): number | null {
  const raw = $<HTMLInputElement>(id).value.trim();
  if (!raw) return null;
  const value = Number(raw);
  return Number.isFinite(value) && value > 0 ? Math.round(value) : null;
}

/** The `providers` map in the draft config, created on demand. */
function draftProviders(): Record<string, ProviderDraft> {
  if (!cachedConfig) cachedConfig = {};
  let providers = cachedConfig.providers as Record<string, ProviderDraft> | undefined;
  if (!providers || typeof providers !== "object") {
    providers = {};
    cachedConfig.providers = providers;
  }
  return providers;
}

function draftProviderNames(): string[] {
  return Object.keys(draftProviders());
}

function draftModelNames(provider: string): string[] {
  const models = draftProviders()[provider]?.models;
  return models ? Object.keys(models) : [];
}

function draftProvider(name: string | null): ProviderDraft | null {
  if (!name) return null;
  return draftProviders()[name] ?? null;
}

function draftModel(provider: string | null, alias: string | null): ModelDraft | null {
  if (!provider || !alias) return null;
  return draftProvider(provider)?.models?.[alias] ?? null;
}

function paintApiKeyPlaceholder(): void {
  $<HTMLInputElement>("field-api-key").placeholder = t(
    apiKeySaved ? "model.apiKeySaved" : "model.apiKeyPlaceholder",
  );
}

$("provider-select").addEventListener("change", (e) => {
  editingProvider = (e.target as HTMLSelectElement).value || null;
  // A model alias is only meaningful within its own provider, so start the new
  // provider at its default (or first) model rather than carrying the old one.
  editingModel = editingProvider ? pickModel(editingProvider) : null;
  renderModelSelect();
  applyProviderFields();
  applyModelFields();
});

$("model-select").addEventListener("change", (e) => {
  editingModel = (e.target as HTMLSelectElement).value || null;
  applyModelFields();
});

/** Commit the provider-level fields into the draft config, in place. */
function commitProviderFields(name: string): void {
  const providers = draftProviders();
  const existing = providers[name] ?? {};
  providers[name] = {
    ...existing,
    kind: $<HTMLInputElement>("field-kind").value || "openai_compat",
    base_url: $<HTMLInputElement>("field-base-url").value || null,
    models: existing.models ?? {},
  };
  const apiKey = $<HTMLInputElement>("field-api-key").value;
  // Only overwrite the stored key when the user typed a new one, so a masked
  // value never erases the saved credential.
  if (apiKey) providers[name].api_key = apiKey;
}

/** Parse the extra-body field; `undefined` means "leave the draft as is". */
function readExtraBody(): Record<string, unknown> | undefined {
  const raw = $<HTMLInputElement>("field-extra-body").value.trim();
  if (!raw) return {};
  try {
    const parsed: unknown = JSON.parse(raw);
    // The backend schema types this as a mapping; a bare number, string or
    // array would be rejected on save, so treat it as invalid here too.
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      return undefined;
    }
    return parsed as Record<string, unknown>;
  } catch {
    return undefined;
  }
}

/** Commit the model-level fields into the draft config, in place. */
function commitModelFields(provider: string, alias: string): void {
  const extraBody = readExtraBody();
  if (extraBody === undefined) return;
  const providers = draftProviders();
  const prov = providers[provider];
  if (!prov) return;
  if (!prov.models) prov.models = {};
  const existing = prov.models[alias] ?? {};
  const topP = readOptionalFloat("field-top-p");
  prov.models[alias] = {
    ...existing,
    model: $<HTMLInputElement>("field-model-id").value || alias,
    reasoning_effort: $<HTMLSelectElement>("field-reasoning").value || null,
    temperature: Number($<HTMLInputElement>("field-temperature").value) || 0,
    max_tokens: readOptionalInt("field-max-tokens"),
    top_p: topP,
    stream: $<HTMLInputElement>("field-stream").checked,
    context_length: readOptionalInt("field-context-length"),
    extra_body: extraBody,
  };
  paintMaxTokensHint(readOptionalInt("field-context-length"));
}

/** Parse an optional 0..1 float field; `null` when blank. */
function readOptionalFloat(id: string): number | null {
  const raw = $<HTMLInputElement>(id).value.trim();
  if (!raw) return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

/** POST the cached config document, reporting the outcome on `model-msg`. */
async function saveConfig(successKey: string): Promise<void> {
  // Refuse to save a document that was never successfully loaded, so a failed
  // `config.get` can never be "fixed" by writing an empty config over the real
  // one.
  if (!configLoaded || !cachedConfig) {
    setProfileMessage("model.configUnavailable", undefined, true);
    return;
  }
  try {
    await api.request("config.save", { config: cachedConfig });
    setProfileMessage(successKey);
    await refreshProviders();
  } catch (err) {
    setProfileMessage("model.saveFailed", { error: String(err) }, true);
  }
}

function paintProfileMessage(): void {
  const msg = $("model-msg");
  if (!lastProfileMessage) {
    msg.textContent = "";
    msg.className = "msg";
    return;
  }
  msg.textContent = t(lastProfileMessage.key, lastProfileMessage.params);
  msg.className = lastProfileMessage.error ? "msg msg--error" : "msg";
}

function setProfileMessage(
  key: string,
  params?: Record<string, string>,
  error = false,
): void {
  lastProfileMessage = { key, params, error };
  paintProfileMessage();
}

$("btn-add-provider").addEventListener("click", () => {
  const input = $<HTMLInputElement>("new-provider-name");
  const name = input.value.trim();
  if (!name) {
    setProfileMessage("model.nameRequired", undefined, true);
    return;
  }
  const providers = draftProviders();
  if (providers[name]) {
    setProfileMessage("model.providerExists", { name }, true);
    return;
  }
  providers[name] = {
    kind: "openai_compat",
    base_url: null,
    models: {},
  };
  input.value = "";
  editingProvider = name;
  editingModel = null;
  renderProviderSelect();
  renderModelSelect();
  applyProviderFields();
  applyModelFields();
  void saveConfig("model.saved");
});

$("btn-remove-provider").addEventListener("click", () => {
  const name = editingProvider;
  if (!name) {
    setProfileMessage("model.noProvider", undefined, true);
    return;
  }
  if (!confirm(t("model.removeProviderConfirm", { name }))) return;
  const providers = draftProviders();
  delete providers[name];
  // Re-pick against the mutated draft so the selection can never point at the
  // provider that was just deleted.
  editingProvider = null;
  editingModel = null;
  editingProvider = pickProvider();
  editingModel = editingProvider ? pickModel(editingProvider) : null;
  renderProviderSelect();
  renderModelSelect();
  applyProviderFields();
  applyModelFields();
  void saveConfig("model.saved");
});

$("btn-add-model").addEventListener("click", () => {
  const name = $<HTMLInputElement>("new-model-name").value.trim();
  if (!name) {
    setProfileMessage("model.nameRequired", undefined, true);
    return;
  }
  const provider = editingProvider;
  if (!provider) {
    setProfileMessage("model.noProvider", undefined, true);
    return;
  }
  const providers = draftProviders();
  const prov = providers[provider];
  if (!prov) return;
  if (!prov.models) prov.models = {};
  if (prov.models[name]) {
    setProfileMessage("model.modelExists", { name }, true);
    return;
  }
  // A new model defaults to its alias as the API model id, matching the
  // backend's own fill-in behaviour.
  prov.models[name] = { model: name, temperature: 0 };
  $<HTMLInputElement>("new-model-name").value = "";
  editingModel = name;
  renderModelSelect();
  applyModelFields();
  void saveConfig("model.saved");
});

$("btn-remove-model").addEventListener("click", () => {
  const provider = editingProvider;
  const alias = editingModel;
  if (!provider || !alias) {
    setProfileMessage("model.noModel", undefined, true);
    return;
  }
  if (!confirm(t("model.removeModelConfirm", { name: alias }))) return;
  const providers = draftProviders();
  const prov = providers[provider];
  if (prov?.models) delete prov.models[alias];
  // Re-pick from the mutated draft so the selection never points at a deleted
  // model.
  editingModel = pickModel(provider);
  renderModelSelect();
  applyModelFields();
  void saveConfig("model.saved");
});

$("btn-save-model").addEventListener("click", async () => {
  const provider = editingProvider;
  if (!provider) {
    setProfileMessage("model.noProvider", undefined, true);
    return;
  }
  // Validate the extra body before writing anything, so a bad value cannot
  // leave a half-committed draft.
  if (readExtraBody() === undefined) {
    setProfileMessage("model.extraBodyInvalid", undefined, true);
    return;
  }
  commitProviderFields(provider);
  if (editingModel) commitModelFields(provider, editingModel);
  await saveConfig("model.saved");
});

// --- options ---------------------------------------------------------------

/**
 * Per-job choices, for the options panel.
 *
 * These change the next translation only, and are the sole place a run's model
 * is chosen. Kept in localStorage rather than the config file: they describe a
 * run, not what models exist, and writing them back would turn a one-off
 * choice into saved state.
 *
 * Empty means "not specified", which for the languages and reasoning effort
 * leaves the engine on its configured value. The model is different — see
 * `renderOptionModels` — because it is no longer optional.
 */
const OPTION_KEYS = {
  model: "pdfxlate.option.model",
  reasoning: "pdfxlate.option.reasoning",
  langIn: "pdfxlate.option.langIn",
  langOut: "pdfxlate.option.langOut",
} as const;

type OptionName = keyof typeof OPTION_KEYS;

const optionValues: Record<OptionName, string> = {
  model: "",
  reasoning: "",
  langIn: "",
  langOut: "",
};

function loadOptionValues(): void {
  for (const [name, key] of Object.entries(OPTION_KEYS) as [OptionName, string][]) {
    try {
      optionValues[name] = localStorage.getItem(key) ?? "";
    } catch {
      optionValues[name] = "";
    }
  }
}

function storeOption(name: OptionName, value: string): void {
  optionValues[name] = value;
  try {
    localStorage.setItem(OPTION_KEYS[name], value);
  } catch {
    // Unavailable storage only costs the memory of the choice.
  }
}

/** Languages offered in the dropdowns, as codes the engine is happy with. */
const LANGUAGES = [
  "en", "zh", "zh-tw", "ja", "ko", "fr", "de", "es", "pt", "it", "ru",
  "ar", "hi", "th", "vi", "id", "tr", "nl", "pl", "uk",
] as const;

/**
 * A language's name in the interface language.
 *
 * `Intl.DisplayNames` is used rather than twenty hand-written labels per
 * locale: it is built into the runtime and already knows these names in every
 * UI language, so there is nothing to keep in step. The code is shown as a
 * fallback if it is unavailable or does not recognise one.
 */
function languageLabel(code: string): string {
  try {
    const names = new Intl.DisplayNames([getLocale() === "zh" ? "zh-CN" : "en"], {
      type: "language",
    });
    return names.of(code) ?? code;
  } catch {
    return code;
  }
}

function fillLanguageSelect(select: HTMLSelectElement, name: OptionName): void {
  select.innerHTML = "";
  const follow = document.createElement("option");
  follow.value = "";
  follow.textContent = t("options.followConfig");
  select.append(follow);
  for (const code of LANGUAGES) {
    const opt = document.createElement("option");
    opt.value = code;
    // Each language named in its own language, so the list is usable whatever
    // the interface language is.
    opt.textContent = languageLabel(code);
    select.append(opt);
  }
  select.value = optionValues[name];
}

function paintOptionSelects(): void {
  fillLanguageSelect($<HTMLSelectElement>("opt-lang-in"), "langIn");
  fillLanguageSelect($<HTMLSelectElement>("opt-lang-out"), "langOut");
  $<HTMLSelectElement>("opt-reasoning").value = optionValues.reasoning;
}

/**
 * Rebuild the model dropdown from the loaded config.
 *
 * Every provider/model pair, labelled `provider · model`, with the alias as the
 * value so the job can name it exactly as the engine expects.
 */
function renderOptionModels(): void {
  const select = $<HTMLSelectElement>("opt-model");
  select.innerHTML = "";

  const providers = cachedConfig?.providers ?? {};
  const values: string[] = [];
  for (const [providerName, provider] of Object.entries(providers)) {
    for (const alias of Object.keys(provider.models ?? {})) {
      const opt = document.createElement("option");
      // `provider|model`: neither half may contain a pipe, so this round-trips
      // without needing a nested structure the DOM cannot carry.
      opt.value = `${providerName}|${alias}`;
      opt.textContent = `${providerName} · ${alias}`;
      select.append(opt);
      values.push(opt.value);
    }
  }
  if (!values.length) {
    select.value = "";
    select.disabled = true;
    return;
  }
  select.disabled = false;
  // No "follow the default" entry: there is no stored default any more, and
  // this panel is where the model for a run is chosen. A remembered choice
  // that no longer exists falls back to the first, rather than leaving the
  // dropdown on a value the engine would reject.
  if (!values.includes(optionValues.model)) {
    storeOption("model", values[0]);
  }
  select.value = optionValues.model;
}

/** The provider/model pair for the next job, or null to use the default. */
function selectedModel(): { provider: string; model: string } | null {
  if (!optionValues.model) return null;
  const [provider, model] = optionValues.model.split("|");
  return provider && model ? { provider, model } : null;
}

$("opt-model").addEventListener("change", (e) =>
  storeOption("model", (e.target as HTMLSelectElement).value),
);
$("opt-reasoning").addEventListener("change", (e) =>
  storeOption("reasoning", (e.target as HTMLSelectElement).value),
);
$("opt-lang-in").addEventListener("change", (e) =>
  storeOption("langIn", (e.target as HTMLSelectElement).value),
);
$("opt-lang-out").addEventListener("change", (e) =>
  storeOption("langOut", (e.target as HTMLSelectElement).value),
);

/**
 * The directory the engine writes to when the user has not chosen one.
 *
 * Held so the output field can show it and a job can pass it explicitly, which
 * keeps what the UI promises and what the engine does in step. The main process
 * also fills it in as a backstop, so an omitted value can never fall through to
 * the engine's working directory.
 */
let defaultOutput: string | null = null;

async function loadDefaultOutput(): Promise<void> {
  try {
    defaultOutput = await api.defaultOutputDir();
  } catch (err) {
    appendLog(t("log.defaultOutputFailed", { error: String(err) }));
    return;
  }
  paintOutputPlaceholder();
}

/**
 * Show the resolved default in the field's placeholder.
 *
 * The element has no `data-i18n-placeholder`, so this is its only writer; the
 * real path beats a translated description, and `title` carries the whole path
 * for when the field is too narrow to show even the abbreviated form.
 */
function paintOutputPlaceholder(): void {
  const field = $<HTMLInputElement>("field-output");
  if (defaultOutput) {
    field.placeholder = abbreviatePath(defaultOutput);
    field.title = defaultOutput;
  } else {
    // Resolution failed; the descriptive text beats a blank field implying
    // there is no default at all.
    field.placeholder = t("options.outputPlaceholder");
  }
}

/** Trim a path to its last two segments for display. */
function abbreviatePath(full: string): string {
  const parts = full.split(/[\\/]/).filter(Boolean);
  return parts.length <= 2 ? full : parts.slice(-2).join("\\");
}

$("btn-pick-output").addEventListener("click", async () => {
  const dir = await api.pickOutputDir(getLocale());
  if (dir) {
    outputDir = dir;
    $<HTMLInputElement>("field-output").value = dir;
    $<HTMLInputElement>("field-output").title = dir;
  }
});

// --- translate -------------------------------------------------------------

function parsePages(spec: string): number[] | undefined {
  const trimmed = spec.trim();
  if (!trimmed) return undefined;
  const pages: number[] = [];
  for (const part of trimmed.split(",")) {
    const p = part.trim();
    if (!p) continue;
    if (p.includes("-")) {
      const [a, b] = p.split("-").map((n) => parseInt(n, 10));
      for (let i = a; i <= b; i++) pages.push(i - 1);
    } else {
      pages.push(parseInt(p, 10) - 1);
    }
  }
  return pages.length ? pages : undefined;
}

function updateTranslateButton(): void {
  const button = $<HTMLButtonElement>("btn-translate");
  button.disabled =
    engineState !== "ok" || checkedFiles.size === 0 || activeJobId !== null;
}

$("btn-translate").addEventListener("click", async () => {
  if (!checkedFiles.size) return;
  const files = [...checkedFiles];
  const mode = $<HTMLSelectElement>("field-mode").value as "fast" | "ultrafast";
  const picked = selectedModel();
  activeJobId = "pending";
  updateTranslateButton();
  $("btn-cancel").classList.remove("hidden");
  setProgress("starting", 0, 1, { key: "progress.starting" });

  try {
    const result = await api.request<{ job_id: string; outputs: Array<{ mono: string; dual: string }> }>(
      "translate.start",
      {
        files,
        // The Options panel is the only thing that decides which model a run
        // uses. Settings shows the same providers but its selection is an
        // editing cursor, so reading it here would let browsing the settings
        // change the next job.
        provider: picked?.provider,
        model: picked?.model,
        lang_in: optionValues.langIn || undefined,
        lang_out: optionValues.langOut || undefined,
        reasoning_effort: optionValues.reasoning || undefined,
        // Pass the resolved default explicitly rather than leaving it undefined:
        // what the field shows and what the engine writes must be the same
        // thing. The main process still fills it in if this is ever missing.
        output: outputDir ?? defaultOutput ?? undefined,
        pages: parsePages($<HTMLInputElement>("field-pages").value),
        mode,
        thread: Number($<HTMLInputElement>("field-thread").value) || undefined,
      },
    );
    showOutputs(result.outputs);
  } catch (err) {
    setProgress("error", 0, 1, { key: "progress.failed", params: { error: String(err) } });
  } finally {
    activeJobId = null;
    $("btn-cancel").classList.add("hidden");
    updateTranslateButton();
  }
});

$("btn-cancel").addEventListener("click", async () => {
  try {
    await api.request("translate.cancel", {});
  } catch (err) {
    appendLog(t("log.cancelFailed", { error: String(err) }));
  }
});

// --- progress + results ----------------------------------------------------

function messageText(message: ProgressMessage): string {
  return "key" in message ? t(message.key, message.params) : message.raw;
}

function setProgress(
  stage: string,
  current: number,
  total: number,
  message: ProgressMessage,
): void {
  lastProgress = { stage, current, total, message };
  paintProgress();
}

/**
 * Repaint the progress line from `lastProgress`.
 *
 * Does nothing before the first job: the status bar is always on screen and
 * starts on its static `progress.idle` text, which `applyStaticTranslations`
 * owns. Repainting after a locale switch must leave the current stage and
 * percentage exactly as they were, so this only ever writes, never resets.
 */
function paintProgress(): void {
  if (!lastProgress) return;
  const { stage, current, total, message } = lastProgress;
  const pct = total > 0 ? Math.min(100, Math.round((current / total) * 100)) : 0;
  $("progress-bar").style.width = `${pct}%`;
  const counter = total > 1 ? ` (${current}/${total})` : "";
  // Unknown stages (the sidecar may add new ones) fall back to the raw id
  // rather than rendering as "stage.foo".
  $("progress-text").textContent = `${t(`stage.${stage}`, undefined, stage)}${counter} — ${messageText(message)}`;
}

function handleProgress(event: JobProgress): void {
  // `event.message` is a diagnostic from the Python engine, not UI copy, so it
  // is shown verbatim in either locale.
  setProgress(event.stage, event.current, event.total, { raw: event.message });
}

function handleDone(event: DoneEvent): void {
  setProgress("done", 1, 1, { key: "progress.done" });
  showOutputs(event.result.outputs);
}

/**
 * Surface a finished job's files as tabs.
 *
 * There is no results list any more: an output is just a PDF, so the honest
 * thing is to open it the same way any other PDF is opened. Both files are
 * opened, and the translation is the one left in front — it is what the job was
 * for, while the dual is for reading side by side.
 *
 * Files that land outside the browsed folder are added to the list too, so they
 * remain reachable rather than existing only as a tab.
 */
function showOutputs(outputs: Array<{ mono: string; dual: string }>): void {
  if (!outputs.length) return;
  const opened: string[] = [];
  for (const pair of outputs) {
    for (const file of [pair.dual, pair.mono]) {
      if (!openTabs.includes(file)) openTabs.push(file);
      if (!folderFiles.includes(file)) folderFiles.push(file);
      opened.push(file);
    }
  }
  renderFiles();
  // Activate the last mono pushed above: the frame the job was run for.
  const firstMono = outputs[0].mono;
  activateTab(firstMono);
  expandGroup("group-docs");
}

// --- log -------------------------------------------------------------------

function appendLog(line: string): void {
  const pane = $("log-pane");
  const atBottom = pane.scrollTop + pane.clientHeight >= pane.scrollHeight - 20;
  pane.textContent += line + "\n";
  if (atBottom) pane.scrollTop = pane.scrollHeight;
}

updateTranslateButton();
renderTabs();
renderFiles();
loadOptionValues();
paintOptionSelects();
updatePreviewControls();
renderPreview();
trackPointerOverPreview();
void loadDefaultOutput();

/**
 * Restore the folder browsed last time.
 *
 * Only the folder, not the open tabs: reopening documents nobody asked for on
 * startup would be presumptuous, while the folder is where they were working
 * and is what makes the list useful at all.
 */
async function restoreFolder(): Promise<void> {
  let stored: string | null = null;
  try {
    stored = localStorage.getItem(FOLDER_STORAGE_KEY);
  } catch {
    return;
  }
  if (stored) await openFolder(stored);
}

void restoreFolder();
