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
  max_tokens: number | null;
  top_p: number | null;
  stream: boolean;
  context_length: number | null;
  enabled: boolean;
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
  enabled?: boolean;
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
  /** How an agent should launch this install's MCP server. */
  mcpLaunch(): Promise<{ command: string; args: string[] }>;
  /** Agents found on this machine, with the config file to write into. */
  mcpAgents(): Promise<Array<{ id: string; target: string; path: string }>>;
  /** System clipboard; `navigator.clipboard` is unavailable from `file://`. */
  copyText(text: string): Promise<void>;
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
/**
 * Which model's inline editor is open, or `null` for none.
 *
 * At most one is open at a time, and the editor is a single DOM element moved
 * under the expanded row. That is what keeps every field id unique, so the
 * existing apply/commit helpers can go on addressing fields by id.
 */
let expandedModel: string | null = null;

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
 * Whether two pages are shown side by side.
 *
 * Global like the zoom, for the same reason: it describes how you read, not
 * which document you are reading. The built-in viewer has no spread mode of its
 * own (no fragment parameter selects one), so this is implemented by mounting a
 * second frame next to the first and stepping the page number by two.
 *
 * `page` keeps meaning "the first page of the spread", so turning the mode off
 * leaves you on a page that is actually on screen.
 */
let spreadMode = false;

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
type SettingsSection = "model" | "appearance" | "general" | "mcp";

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
  renderProviderList();
  renderModelList();
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
  // The strip has no width while hidden, so the overflow state cannot be
  // measured then; recompute once it is back on screen.
  if (!inSettings) requestAnimationFrame(updateTabbarScroll);

  for (const button of document.querySelectorAll<HTMLElement>(".settings-menu__item")) {
    button.classList.toggle("is-active", button.dataset.section === settingsSection);
  }
  for (const panel of document.querySelectorAll<HTMLElement>(".settings-panel")) {
    panel.classList.toggle("hidden", panel.dataset.panel !== settingsSection);
  }
  // The model editor is live in the settings panel; make sure it reflects the
  // cached config whenever the panel is (re)opened.
  if (inSettings && settingsSection === "model") {
    renderProviderList();
    renderModelList();
    applyProviderFields();
    applyModelFields();
  }
  // Only the model section is a two-column layout; the other three keep the
  // centred single column, so the host's split styling follows the section.
  $("settings-host").classList.toggle("settings-host--split", inSettings && settingsSection === "model");
  if (inSettings && settingsSection === "general") paintEngineFacts();
  // Built on open rather than at startup: it needs the real launch command,
  // which comes from the main process, and there is no reason to ask for it
  // unless the panel is actually being looked at.
  if (inSettings && settingsSection === "mcp") void refreshMcpPanel();
}

/** Fill the General panel's read-only diagnostics. */
function paintEngineFacts(): void {
  $("engine-version").textContent = engineVersion ?? t("settings.unknown");
  $("engine-config-dir").textContent = engineConfigDir ?? t("settings.unknown");
}

// --- MCP setup -------------------------------------------------------------

/**
 * The snippet to paste into any MCP client.
 *
 * Built from the real launch command rather than a template in the docs: the
 * command differs between a source checkout and a packaged install, so a
 * hard-coded example would be wrong for most readers and would rot as soon as
 * anything moved.
 */
let mcpSnippetText = "";
let mcpAgents: Array<{ id: string; target: string; path: string }> = [];
// Kept from the same call that built the snippet, so the entry written into an
// agent's config is byte-for-byte what the snippet tells people to use.
let mcpLaunch: { command: string; args: string[] } | null = null;

async function refreshMcpPanel(): Promise<void> {
  try {
    const [launch, agents] = await Promise.all([api.mcpLaunch(), api.mcpAgents()]);
    mcpLaunch = launch;
    mcpAgents = agents;
    const snippet = await api.request<{ snippet: string }>("mcp.snippet", {
      command: launch.command,
      args: launch.args,
    });
    mcpSnippetText = snippet.snippet;
    $("mcp-snippet").textContent = mcpSnippetText;
  } catch (err) {
    // The engine may not be up yet; the panel explains itself rather than
    // showing an empty box.
    $("mcp-snippet").textContent = t("mcp.unavailable", { error: String(err) });
    mcpSnippetText = "";
  }
  paintMcpAgents();
}

function paintMcpAgents(): void {
  const list = $("mcp-agent-list");
  list.innerHTML = "";
  if (!mcpAgents.length) {
    const li = document.createElement("li");
    li.className = "agent-list__empty";
    li.textContent = t("mcp.noAgents");
    list.append(li);
    return;
  }
  for (const agent of mcpAgents) {
    const li = document.createElement("li");
    li.className = "agent-row";

    const name = document.createElement("span");
    name.className = "agent-row__name";
    name.textContent = t(`mcp.agent.${agent.id}`);
    name.title = agent.path;

    const where = document.createElement("span");
    where.className = "agent-row__path";
    where.textContent = agent.path;
    where.title = agent.path;

    const write = document.createElement("button");
    write.className = "btn btn--small";
    write.textContent = t("mcp.write");
    write.addEventListener("click", () => void writeMcpConfig(agent, write));

    li.append(name, where, write);
    list.append(li);
  }
}

function setMcpMessage(key: string, params?: Record<string, string>, error = false): void {
  const msg = $("mcp-msg");
  msg.textContent = t(key, params);
  msg.className = error ? "msg msg--error" : "msg";
}

/**
 * Add our entry to one agent's config.
 *
 * The merge, the backup and the post-write check all happen in the engine; this
 * only reports the outcome. That split is deliberate: writing another program's
 * config is the part that must not go wrong, so it lives where it can be tested
 * against real files rather than in a click handler.
 */
async function writeMcpConfig(
  agent: { id: string; target: string; path: string },
  button: HTMLButtonElement,
): Promise<void> {
  if (!mcpLaunch) {
    setMcpMessage("mcp.unavailable", { error: t("settings.unknown") }, true);
    return;
  }
  button.disabled = true;
  setMcpMessage("mcp.writing");
  try {
    const result = await api.request<{ path: string; backup: string; changed: boolean }>(
      "mcp.installConfig",
      {
        path: agent.path,
        target: agent.target,
        command: mcpLaunch.command,
        args: mcpLaunch.args,
      },
    );
    setMcpMessage(result.changed ? "mcp.written" : "mcp.alreadyPresent", {
      path: result.path,
      backup: result.backup,
    });
    appendLog(t("log.mcpWrote", { path: result.path, backup: result.backup }));
  } catch (err) {
    setMcpMessage("mcp.writeFailed", { error: String(err) }, true);
  } finally {
    button.disabled = false;
  }
}

$("btn-copy-mcp").addEventListener("click", async () => {
  if (!mcpSnippetText) return;
  try {
    await api.copyText(mcpSnippetText);
    setMcpMessage("mcp.copied");
  } catch (err) {
    setMcpMessage("mcp.copyFailed", { error: String(err) }, true);
  }
});

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

/**
 * Paint the folder selector's two lines.
 *
 * The name is the folder's own last segment — that is what you scan for — while
 * the full path sits underneath for when two folders share a name. With no
 * folder chosen the path line is hidden rather than blanked, so the row stays
 * one line tall instead of leaving an empty gap under the label.
 */
function renderFolderLabel(): void {
  const name = $("folder-name");
  const path = $("folder-label");
  if (currentFolder) {
    name.textContent = baseName(currentFolder) || currentFolder;
    path.textContent = currentFolder;
    path.title = currentFolder;
    path.classList.remove("hidden");
  } else {
    name.textContent = t("docs.noFolder");
    path.textContent = "";
    path.title = "";
    path.classList.add("hidden");
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

/** How long a finished drag suppresses the click it would otherwise cause. */
const DRAG_CLICK_GUARD_MS = 250;

/** Set by a tab drag, so the click that follows `dragend` does not activate. */
let tabDragEndedAt = 0;

/** The path being dragged, so a drop can be matched against it. */
let draggingTab: string | null = null;

function renderTabs(): void {
  const strip = $("tabbar-strip");
  // Rebuilding the children discards the scroll position, which would send a
  // strip scrolled to the end back to the first tab every time a document is
  // activated or a locale changes. Carried across instead.
  const previousScroll = strip.scrollLeft;
  strip.innerHTML = "";

  for (const file of openTabs) {
    const tab = document.createElement("div");
    tab.className = "tab";
    tab.classList.toggle("is-active", activeTab === file);
    // The full path in the tooltip: two folders can hold the same file name.
    tab.title = file;
    // The path is what identifies the tab across a reorder; the DOM order is
    // only the user's preferred arrangement of it.
    tab.dataset.path = file;
    tab.draggable = true;

    const name = document.createElement("span");
    name.className = "tab__name";
    name.textContent = baseName(file);

    const close = document.createElement("button");
    close.className = "tab__close";
    close.textContent = "×";
    close.title = t("tab.close");
    // A draggable child would start a tab drag instead of pressing the button.
    close.draggable = false;
    close.addEventListener("click", (e) => {
      // Without this the click also lands on the tab underneath and re-selects
      // the document that is being closed.
      e.stopPropagation();
      closeTab(file);
    });

    // Clicking anywhere on the tab activates it, matching how tabs behave
    // elsewhere; the close button is the only part that acts differently.
    tab.addEventListener("click", () => {
      // A drag ends with a click on the tab it started from; activating there
      // would fight the reorder the user just performed.
      if (Date.now() - tabDragEndedAt < DRAG_CLICK_GUARD_MS) return;
      activateTab(file);
    });

    tab.addEventListener("dragstart", (e) => beginTabDrag(e, tab, file));
    tab.addEventListener("dragend", () => endTabDrag(tab));

    tab.append(name, close);
    strip.append(tab);
  }

  strip.scrollLeft = previousScroll;
  // Deferred: the widths the browser settles on are only known after layout,
  // and both the overflow state and the active tab's visibility depend on them.
  requestAnimationFrame(() => {
    updateTabbarScroll();
    revealActiveTab();
  });
}

/**
 * Start reordering a tab.
 *
 * The payload is a private MIME type rather than `text/plain`: the latter would
 * also be accepted by anything that takes text drops — including the address
 * bar of a browser window opened over this one — and dragging a tab out of the
 * window should do nothing at all.
 */
function beginTabDrag(e: DragEvent, tab: HTMLElement, file: string): void {
  draggingTab = file;
  tab.classList.add("is-dragging");
  e.dataTransfer?.setData("application/x-pdfxlate-tab", file);
  // Copy, not move: a move cursor promises the tab will leave the bar.
  if (e.dataTransfer) e.dataTransfer.effectAllowed = "copyMove";
}

/**
 * Finish a reorder and adopt the new order.
 *
 * The order is read back from the DOM rather than tracked during the drag:
 * `dragover` moves the element itself, so the DOM already is the arrangement
 * the user sees, and re-deriving it would only give two sources of truth.
 */
function endTabDrag(tab: HTMLElement): void {
  tab.classList.remove("is-dragging");
  draggingTab = null;
  tabDragEndedAt = Date.now();

  const strip = $("tabbar-strip");
  const order = Array.from(strip.querySelectorAll<HTMLElement>(".tab"))
    .map((el) => el.dataset.path)
    .filter((path): path is string => Boolean(path));
  // Every open tab has a DOM node, so a length mismatch means the DOM was
  // rewritten underneath us; keeping the previous order beats dropping tabs.
  if (order.length !== openTabs.length) return;
  if (order.every((path, i) => path === openTabs[i])) return;

  openTabs = order;
  // No re-render: the DOM already shows this order, and rebuilding it here
  // would cancel the animation the browser is finishing.
  renderFiles();
  updateTabbarScroll();
}

/**
 * Move a tab to the pointer's position while a reorder is in progress.
 *
 * The insertion point comes from each tab's midpoint, which is what makes the
 * row feel like it is rearranging under the cursor rather than only updating on
 * drop.
 */
function dragTabOver(e: DragEvent): void {
  if (!draggingTab) return;
  e.preventDefault();
  if (e.dataTransfer) e.dataTransfer.dropEffect = "move";

  const strip = $("tabbar-strip");
  const dragged = strip.querySelector<HTMLElement>(".tab.is-dragging");
  if (!dragged) return;

  const siblings = Array.from(strip.querySelectorAll<HTMLElement>(".tab")).filter(
    (el) => el !== dragged,
  );
  const after = siblings.find((el) => {
    const box = el.getBoundingClientRect();
    return e.clientX < box.left + box.width / 2;
  });
  if (after) strip.insertBefore(dragged, after);
  else strip.append(dragged);
}

/**
 * Show the arrows only when there is something to scroll to, and disable the
 * one pointing at an end that has been reached.
 *
 * Called after every render, on scroll and on resize: all three change either
 * the content width or the visible width.
 */
function updateTabbarScroll(): void {
  const bar = $("tabbar");
  const strip = $("tabbar-strip");
  // A hidden strip (settings mode) has no width, and measuring it would report
  // overflow for zero tabs.
  if (!strip.clientWidth) {
    bar.classList.remove("is-overflowing");
    return;
  }
  const overflowing = strip.scrollWidth > strip.clientWidth + 1;
  bar.classList.toggle("is-overflowing", overflowing);
  // The arrows are `display: none` while everything fits, so their disabled
  // state is only meaningful once they are shown.
  if (!overflowing) return;
  // 1px of slack: sub-pixel layout means the two ends rarely compare exactly.
  $<HTMLButtonElement>("tabbar-prev").disabled = strip.scrollLeft <= 1;
  $<HTMLButtonElement>("tabbar-next").disabled =
    strip.scrollLeft >= strip.scrollWidth - strip.clientWidth - 1;
}

/** Scroll the strip by most of a screenful, the way tab bars elsewhere do. */
function scrollTabs(direction: -1 | 1): void {
  const strip = $("tabbar-strip");
  const step = Math.max(120, Math.round(strip.clientWidth * 0.8));
  strip.scrollBy({ left: direction * step, behavior: "smooth" });
}

/**
 * Bring the active tab into view.
 *
 * Only scrolls when it is actually outside the strip: a tab that is already
 * visible should not shift the row under the user's pointer.
 */
function revealActiveTab(): void {
  const strip = $("tabbar-strip");
  const active = strip.querySelector<HTMLElement>(".tab.is-active");
  if (!active) return;
  const stripBox = strip.getBoundingClientRect();
  const tabBox = active.getBoundingClientRect();
  if (tabBox.left < stripBox.left) {
    strip.scrollLeft -= stripBox.left - tabBox.left;
  } else if (tabBox.right > stripBox.right) {
    strip.scrollLeft += tabBox.right - stripBox.right;
  }
}

$("tabbar-prev").addEventListener("click", () => scrollTabs(-1));
$("tabbar-next").addEventListener("click", () => scrollTabs(1));
$("tabbar-strip").addEventListener("scroll", updateTabbarScroll, { passive: true });
$("tabbar-strip").addEventListener("dragover", dragTabOver);
// The strip is also where a tab is dropped when the pointer is past the last
// tab, and `drop` must be cancelled there too or the browser refuses it.
$("tabbar-strip").addEventListener("drop", (e) => {
  if (!draggingTab) return;
  e.preventDefault();
});
window.addEventListener("resize", updateTabbarScroll);

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
  // In spread mode this document may never have been combined into a spread
  // file. Built after the first paint so switching tabs stays instant: until it
  // arrives the pane shows a placeholder, not the wrong document.
  if (spreadMode) void prepareSpreadFor(file);
}

/** Build a spread for `file` if needed, then repaint if it is still current. */
async function prepareSpreadFor(file: string): Promise<void> {
  try {
    await ensureSpread(file);
  } catch (err) {
    appendLog(t("preview.spreadFailed", { error: String(err) }));
    return;
  }
  // The tab may have changed while the engine was working.
  if (activeTab === file && spreadMode) renderPreview();
}

function closeTab(file: string): void {
  const index = openTabs.indexOf(file);
  if (index < 0) return;
  openTabs.splice(index, 1);
  pageState.delete(file);
  // The derived file is still on disk and cheap to reuse, but this map is only
  // a session cache; dropping the entry keeps it from growing with every file
  // ever opened.
  spreadFiles.delete(file);
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
function viewerUrl(file: string, page: number): string {
  const fragment =
    previewZoom === "fit-width"
      ? "zoom=page-width"
      : previewZoom === "fit-height"
        ? "view=FitV"
        : `zoom=${previewZoom}`;
  return `${api.fileUrl(file)}#toolbar=0&navpanes=0&page=${page}&${fragment}`;
}

/**
 * The pages a spread shows, in order.
 *
 * Two adjacent pages, except at the end of an odd-length document where there
 * is only one left. Pages are 1-based, matching both the viewer fragment and
 * `state.page`.
 */
function spreadPages(state: { page: number; pages: number }): number[] {
  const first = state.page;
  const second = first + 1;
  if (state.pages > 0 && second > state.pages) return [first];
  return [first, second];
}

/**
 * Derived spread documents, keyed by source path.
 *
 * Building one rewrites every page, so the result is kept for the session and
 * the engine also caches it on disk. Cleared on locale change? No — the path
 * does not depend on language; it is dropped when a tab closes.
 */
const spreadFiles = new Map<string, string>();

/**
 * Make sure a spread document exists for `file`, returning its path.
 *
 * Rejects rather than falling back to the source: showing the single-page
 * document in spread mode would look like the toggle silently failed.
 */
async function ensureSpread(file: string): Promise<string> {
  const cached = spreadFiles.get(file);
  if (cached) return cached;
  const result = await api.request<{ path: string }>("pdf.spread", { path: file });
  spreadFiles.set(file, result.path);
  return result.path;
}

/**
 * Repaint the preview for the active tab, page and zoom.
 *
 * Always exactly one frame, including in spread mode: the facing pages are
 * combined into a derived document by the engine, so there is one viewer and
 * one scrollbar. Two frames would each own a scrollbar and could not be kept in
 * step — the viewer runs out of process and does not report its scroll offset.
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

  const state = stateFor(source);
  const spreadPath = spreadMode ? spreadFiles.get(source) : undefined;
  if (spreadMode && !spreadPath) {
    // The file has not been built yet. `toggleSpread` and `activateTab` build it
    // before asking for a repaint, so this is only a transient state; showing
    // the source meanwhile is better than an empty pane.
    paintEmpty(t("preview.spreadPending"));
    return;
  }

  const frame = document.createElement("iframe");
  frame.className = "preview__frame";
  // In spread mode the derived document has one page per pair, so the page
  // number is halved (and rounded up: source page 3 lives on spread page 2).
  const page = spreadPath ? Math.ceil(state.page / 2) : state.page;
  frame.src = viewerUrl(spreadPath ?? source, page);
  // A drag may be in flight; a freshly built frame must not become droppable
  // again underneath it.
  if (!frameDroppable) frame.style.pointerEvents = "none";
  host.append(frame);
}

/** A one-line placeholder inside the preview host. */
function paintEmpty(text: string): void {
  const host = $("preview-host");
  const empty = document.createElement("div");
  empty.className = "preview__empty";
  const title = document.createElement("p");
  title.className = "preview__empty-title";
  title.textContent = text;
  empty.append(title);
  host.append(empty);
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

  // Spread is independent of the zoom mode, so it is its own toggle rather than
  // a third entry in the fit group.
  const spread = $<HTMLButtonElement>("spread-toggle");
  spread.disabled = !hasFile;
  spread.classList.toggle("is-active", spreadMode);
  spread.setAttribute("aria-pressed", String(spreadMode));

  const page = state?.page ?? 1;
  const pages = state?.pages ?? 0;
  const known = pages > 0;
  // In a spread the label names both pages on screen; the second is omitted
  // when the document ends on the first one.
  const shown = hasFile && spreadMode && state ? spreadPages(state) : [];
  $("page-label").textContent = hasFile
    ? shown.length === 2
      ? `${shown[0]}–${shown[1]} / ${known ? pages : "?"}`
      : known
        ? `${page} / ${pages}`
        : String(page)
    : "–";
  $<HTMLButtonElement>("page-prev").disabled = !hasFile || page <= 1;
  // In a spread the last page can be on screen while `page` is still below
  // `pages`, so the boundary is the last page the next step would reveal.
  const lastVisible = spreadMode ? Math.min(page + 1, known ? pages : Infinity) : page;
  $<HTMLButtonElement>("page-next").disabled =
    !hasFile || (known && lastVisible >= pages);

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
  // A spread moves by two, so the same page does not appear on both sides of
  // two consecutive views.
  const stride = spreadMode ? 2 : 1;
  const next = state.page + delta * stride;
  if (next < 1) return;
  if (state.pages > 0 && next > state.pages) return;
  state.page = next;
  updatePreviewControls();
  renderPreview();
  flashViewerOverlay();
}

$("page-prev").addEventListener("click", () => stepPage(-1));
$("page-next").addEventListener("click", () => stepPage(1));

/**
 * Turn the two-page spread on or off.
 *
 * Async because the first time a document is shown this way, the engine has to
 * build the derived file. The mode is only switched after that succeeds: a
 * toggle that flipped and then showed the single-page document would look
 * broken, so a failure leaves the view as it was and says why in the log.
 *
 * `page` always means the left page of the spread, so switching off lands on a
 * page that is genuinely on screen rather than on the right-hand one, which
 * would be indistinguishable from skipping forward.
 */
async function toggleSpread(): Promise<void> {
  const file = previewSource();
  if (!file) return;
  if (!spreadMode) {
    const button = $<HTMLButtonElement>("spread-toggle");
    button.disabled = true;
    try {
      await ensureSpread(file);
    } catch (err) {
      appendLog(t("preview.spreadFailed", { error: String(err) }));
      return;
    } finally {
      button.disabled = false;
    }
    spreadMode = true;
  } else {
    spreadMode = false;
  }
  updatePreviewControls();
  renderPreview();
  flashViewerOverlay();
}

$("spread-toggle").addEventListener("click", () => void toggleSpread());

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

/**
 * Whether a drag carries files rather than one of the app's own drags.
 *
 * Reordering a tab also fires `dragenter` on the document, and without this
 * test that would switch the viewer frame off and light up the drop outline —
 * so dragging a tab across the window would look like a file about to be
 * dropped, and dropping it there would run the file handler on no files.
 */
function isFileDrag(e: DragEvent): boolean {
  return Array.from(e.dataTransfer?.types ?? []).includes("Files");
}

// Listened for on `document`, not on the host: with a document open the frame
// swallows events aimed at the host, and this is the one event that still
// reaches the parent, so it is where the frame has to be switched off.
document.addEventListener("dragenter", (e) => {
  if (!isFileDrag(e)) return;
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
  if (!isFileDrag(e)) return;
  e.preventDefault();
  previewHost.classList.add("preview__host--over");
});
previewHost.addEventListener("drop", (e) => {
  if (!isFileDrag(e)) return;
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
    // A reload can drop the model whose editor was open (deleted elsewhere, or
    // just removed here), so the cursor has to follow the new draft.
    if (expandedModel && !draftModelNames(editingProvider ?? "").includes(expandedModel)) {
      expandedModel = null;
    }
    renderProviderList();
    renderModelList();
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

/**
 * The provider list in the model section's left column.
 *
 * Replaces the old provider `<select>`: the list is the navigation for the
 * detail column, so it has to show every provider at once rather than hide
 * them behind a dropdown.
 */
function renderProviderList(): void {
  const list = $("provider-list");
  list.innerHTML = "";
  const names = draftProviderNames();

  for (const name of names) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "provider-row";
    row.setAttribute("role", "option");
    row.dataset.provider = name;
    if (name === editingProvider) {
      row.classList.add("is-active");
      row.setAttribute("aria-selected", "true");
    }

    const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    icon.setAttribute("class", "provider-row__icon");
    icon.setAttribute("viewBox", "0 0 24 24");
    icon.setAttribute("aria-hidden", "true");
    for (const d of ["M12 2.5 21 7v10l-9 4.5L3 17V7z", "M3 7l9 4.5L21 7M12 11.5V21.5"]) {
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", d);
      icon.append(path);
    }

    const label = document.createElement("span");
    label.className = "provider-row__name";
    label.textContent = name;
    label.title = name;

    const dot = document.createElement("span");
    const state = providerState(name);
    dot.className = `provider-dot provider-dot--${state}`;
    dot.title = t(
      state === "ready"
        ? "model.statusReady"
        : state === "incomplete"
          ? "model.statusIncomplete"
          : "model.statusEmpty",
    );

    row.append(icon, label, dot);
    row.addEventListener("click", () => selectProvider(name));
    list.append(row);
  }

  $("provider-empty").classList.toggle("hidden", names.length > 0);
  $("provider-detail").classList.toggle("hidden", names.length === 0);
}

/**
 * What the status dot means.
 *
 * It reports the config, not reachability: nothing in the engine probes the
 * endpoint, so a dot claiming "online" would be invented. Green means the
 * provider can actually be used (endpoint and key both present), amber means
 * one is missing, grey means it has no models yet.
 */
function providerState(name: string): "ready" | "incomplete" | "empty" {
  const provider = draftProviders()[name];
  if (!provider) return "empty";
  if (!provider.models || !Object.keys(provider.models).length) return "empty";
  // The key is never sent back to the renderer, so a stored one is only
  // visible through the summary's flag. A key typed in this session lives on
  // the draft and counts immediately.
  const hasKey = !!provider.api_key || !!cachedProviders?.providers[name]?.has_api_key;
  return provider.base_url && hasKey ? "ready" : "incomplete";
}

/** Switch the detail column to another provider. */
function selectProvider(name: string): void {
  if (name === editingProvider) return;
  // Preserve whatever is in the editor before the provider changes under it.
  commitOpenEditor();
  editingProvider = name;
  editingModel = pickModel(name);
  expandedModel = null;
  renderProviderList();
  renderModelList();
  applyProviderFields();
  applyModelFields();
}

/** Format a context window as a short badge value: 128K, 1M. */
function formatContextBadge(tokens: number): string {
  if (tokens >= 1_000_000) {
    const millions = tokens / 1_000_000;
    return `${Number.isInteger(millions) ? millions : millions.toFixed(1)}M`;
  }
  if (tokens >= 1000) return `${Math.round(tokens / 1000)}K`;
  return String(tokens);
}

/**
 * The single inline model editor.
 *
 * It is moved under whichever row is expanded, so it spends time detached from
 * the document (while the list is rebuilt) — `getElementById` would then miss
 * it. Holding the reference once avoids that.
 */
let modelEditorEl: HTMLElement | null = null;

function modelEditor(): HTMLElement {
  if (!modelEditorEl) modelEditorEl = $("model-editor");
  return modelEditorEl;
}

/**
 * Rebuild the model rows for the current provider.
 *
 * The inline editor is a single element that gets moved under whichever row is
 * expanded, which is why the field ids stay unique and the apply/commit
 * helpers can keep using them.
 */
function renderModelList(): void {
  const list = $("model-list");
  const editor = modelEditor();
  // Detach first: the list is about to be emptied, which would otherwise take
  // the editor (and any unsaved input in it) with it.
  editor.remove();
  list.innerHTML = "";

  const names = editingProvider ? draftModelNames(editingProvider) : [];
  if (!names.length) {
    const empty = document.createElement("div");
    empty.className = "model-list__empty";
    empty.textContent = t("model.noModels");
    list.append(empty);
    return;
  }

  for (const alias of names) {
    const model = draftModel(editingProvider, alias) ?? {};
    const row = document.createElement("div");
    row.className = "model-row";
    row.setAttribute("role", "listitem");
    row.dataset.model = alias;
    if (model.enabled === false) row.classList.add("is-disabled");
    if (alias === expandedModel) row.classList.add("is-expanded");

    const name = document.createElement("span");
    name.className = "model-row__name";
    name.textContent = alias;
    name.title = alias;
    row.append(name);

    // The API id is only worth showing when it differs from the alias; when
    // they match it would just repeat the row's own name.
    const modelId = model.model ?? alias;
    if (modelId && modelId !== alias) {
      const id = document.createElement("span");
      id.className = "model-row__id";
      id.textContent = modelId;
      id.title = modelId;
      row.append(id);
    }

    if (typeof model.context_length === "number" && model.context_length > 0) {
      const badge = document.createElement("span");
      badge.className = "model-row__badge";
      badge.textContent = formatContextBadge(model.context_length);
      row.append(badge);
    }
    if (model.stream === true) {
      const badge = document.createElement("span");
      badge.className = "model-row__badge";
      badge.textContent = t("model.stream");
      row.append(badge);
    }

    const spacer = document.createElement("span");
    spacer.className = "model-row__spacer";
    row.append(spacer);

    const actions = document.createElement("span");
    actions.className = "model-row__actions";

    const edit = iconButton("model.edit", "M4 20h4l10-10-4-4L4 16z M13 5l4 4");
    edit.addEventListener("click", (event) => {
      event.stopPropagation();
      toggleModelEditor(alias);
    });

    const remove = iconButton("model.removeModel", "M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6");
    remove.classList.add("icon-btn--danger");
    remove.addEventListener("click", (event) => {
      event.stopPropagation();
      void removeModel(alias);
    });

    actions.append(edit, remove);

    // The switch flips the stored flag immediately. It is the one control on
    // this row that changes what a translation may use, so leaving it pending
    // behind a Save button would be easy to miss.
    const toggle = document.createElement("label");
    toggle.className = "switch";
    toggle.title = t("model.enabledHint");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = model.enabled !== false;
    checkbox.setAttribute("aria-label", t("model.enabledHint"));
    const track = document.createElement("span");
    track.className = "switch__track";
    toggle.append(checkbox, track);
    toggle.addEventListener("click", (event) => event.stopPropagation());
    checkbox.addEventListener("change", () => {
      void setModelEnabled(alias, checkbox.checked);
    });

    actions.append(toggle);
    row.append(actions);

    row.addEventListener("click", () => toggleModelEditor(alias));
    list.append(row);
  }

  // Re-attach the editor. When nothing is expanded it goes back to the end of
  // the list (hidden), rather than staying detached: an element outside the
  // document cannot be found by id, and every field in it is addressed that
  // way.
  const expandedRow = expandedModel
    ? list.querySelector<HTMLElement>(`.model-row[data-model="${cssEscape(expandedModel)}"]`)
    : null;
  if (expandedRow) {
    editor.classList.remove("hidden");
    expandedRow.after(editor);
  } else {
    expandedModel = null;
    editor.classList.add("hidden");
    list.append(editor);
  }
}

/** Minimal attribute-selector escape: aliases are user text. */
function cssEscape(value: string): string {
  return value.replace(/["\\]/g, "\\$&");
}

/** A borderless icon button with a tooltip and an accessible name. */
function iconButton(key: string, pathData: string): HTMLButtonElement {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "icon-btn icon-btn--bare";
  button.title = t(key);
  button.setAttribute("aria-label", t(key));
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", pathData);
  svg.append(path);
  button.append(svg);
  return button;
}

/** Open the editor under `alias`, or close it when it is already open. */
function toggleModelEditor(alias: string): void {
  if (expandedModel === alias) {
    commitOpenEditor();
    expandedModel = null;
    renderModelList();
    return;
  }
  // Keep edits from the model being left behind rather than discarding them;
  // the draft is not persisted until Save, so this loses nothing.
  commitOpenEditor();
  expandedModel = alias;
  editingModel = alias;
  renderModelList();
  applyModelFields();
}

/** Commit the open editor into the draft, if one is open and valid. */
function commitOpenEditor(): void {
  if (!expandedModel || !editingProvider) return;
  commitModelFields(editingProvider, expandedModel);
}

/** Flip one model's enabled flag and persist it. */
async function setModelEnabled(alias: string, enabled: boolean): Promise<void> {
  const provider = editingProvider;
  if (!provider) return;
  // Commit first so a pending edit in the open editor is not overwritten by
  // the reload that follows the save.
  commitOpenEditor();
  const entry = draftProviders()[provider]?.models?.[alias];
  if (!entry) return;
  entry.enabled = enabled;
  await saveConfig("model.saved");
}

/** Remove one model, with the same confirmation the old button used. */
async function removeModel(alias: string): Promise<void> {
  const provider = editingProvider;
  if (!provider) return;
  if (!confirm(t("model.removeModelConfirm", { name: alias }))) return;
  const prov = draftProviders()[provider];
  if (prov?.models) delete prov.models[alias];
  if (expandedModel === alias) expandedModel = null;
  editingModel = pickModel(provider);
  renderModelList();
  applyModelFields();
  await saveConfig("model.saved");
}

/**
 * The API shapes the engine has adapters for.
 *
 * Kept in step with the registered adapters by hand — a value here that has no
 * adapter would be a selectable option that fails at translate time.
 */
const PROVIDER_KINDS = ["openai_compat", "openai_responses", "anthropic_messages"] as const;

/**
 * Select a provider's `kind` in the dropdown.
 *
 * A kind written by hand into `config.yaml` that is not one of the built-ins
 * gets a temporary option of its own: without it the select would fall back to
 * the first entry and the next save would silently rewrite the provider to
 * `openai_compat`, turning a typo into a behaviour change.
 */
function paintProviderKind(kind: string): void {
  const select = $<HTMLSelectElement>("field-kind");
  const extra = select.querySelector<HTMLOptionElement>('option[data-kind-extra="true"]');
  if (kind && !(PROVIDER_KINDS as readonly string[]).includes(kind)) {
    const option = extra ?? document.createElement("option");
    option.value = kind;
    option.textContent = kind;
    option.dataset.kindExtra = "true";
    if (!extra) select.append(option);
    select.value = kind;
    return;
  }
  if (extra) extra.remove();
  select.value = kind || PROVIDER_KINDS[0];
}

/** Fill the provider-level fields from the cached config, and mask the key. */
function applyProviderFields(): void {
  const provider = draftProvider(editingProvider);
  $("provider-title").textContent = editingProvider ?? "";
  paintProviderKind(provider?.kind ?? "");
  $<HTMLInputElement>("field-base-url").value = provider?.base_url ?? "";
  // The key field always starts empty: the stored secret is never shown back,
  // only the `has_api_key` flag the summary reports.
  $<HTMLInputElement>("field-api-key").value = "";
  apiKeySaved = !!cachedProviders?.providers[editingProvider ?? ""]?.has_api_key;
  paintApiKeyPlaceholder();
}

/** Fill the model-level fields from the cached config. */
function applyModelFields(): void {
  if (!expandedModel) return;
  const model = draftModel(editingProvider, expandedModel);
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

/** Commit the provider-level fields into the draft config, in place. */
function commitProviderFields(name: string): void {
  const providers = draftProviders();
  const existing = providers[name] ?? {};
  providers[name] = {
    ...existing,
    kind: $<HTMLSelectElement>("field-kind").value || "openai_compat",
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
    // The switch is not in the editor, so carry the stored flag through rather
    // than letting a save reset it to the default.
    enabled: existing.enabled !== false,
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
  const box = $("provider-add");
  box.classList.toggle("hidden");
  if (!box.classList.contains("hidden")) {
    $<HTMLInputElement>("new-provider-name").focus();
  }
});

$("btn-add-provider-empty").addEventListener("click", () => {
  $("provider-add").classList.remove("hidden");
  $<HTMLInputElement>("new-provider-name").focus();
});

$("btn-add-provider-confirm").addEventListener("click", () => {
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
  $("provider-add").classList.add("hidden");
  editingProvider = name;
  editingModel = null;
  expandedModel = null;
  renderProviderList();
  renderModelList();
  applyProviderFields();
  applyModelFields();
  void saveConfig("model.saved");
});

$("new-provider-name").addEventListener("keydown", (event) => {
  if ((event as KeyboardEvent).key === "Enter") $("btn-add-provider-confirm").click();
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
  expandedModel = null;
  editingProvider = pickProvider();
  editingModel = editingProvider ? pickModel(editingProvider) : null;
  renderProviderList();
  renderModelList();
  applyProviderFields();
  applyModelFields();
  void saveConfig("model.saved");
});

$("btn-add-model").addEventListener("click", () => {
  const box = $("model-add");
  box.classList.toggle("hidden");
  if (!box.classList.contains("hidden")) {
    $<HTMLInputElement>("new-model-name").focus();
  }
});

$("btn-add-model-confirm").addEventListener("click", () => {
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
  $("model-add").classList.add("hidden");
  editingModel = name;
  expandedModel = name;
  renderModelList();
  applyModelFields();
  void saveConfig("model.saved");
});

$("new-model-name").addEventListener("keydown", (event) => {
  if ((event as KeyboardEvent).key === "Enter") $("btn-add-model-confirm").click();
});

$("btn-editor-close").addEventListener("click", () => {
  commitOpenEditor();
  expandedModel = null;
  renderModelList();
});

$("btn-editor-save").addEventListener("click", async () => {
  const provider = editingProvider;
  if (!provider) {
    setProfileMessage("model.noProvider", undefined, true);
    return;
  }
  if (readExtraBody() === undefined) {
    setProfileMessage("model.extraBodyInvalid", undefined, true);
    return;
  }
  commitProviderFields(provider);
  if (expandedModel) commitModelFields(provider, expandedModel);
  await saveConfig("model.saved");
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
  if (expandedModel) commitModelFields(provider, expandedModel);
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

  const providers = (cachedConfig?.providers ?? {}) as Record<string, ProviderDraft>;
  const values: string[] = [];
  for (const [providerName, provider] of Object.entries(providers)) {
    for (const [alias, entry] of Object.entries(provider.models ?? {})) {
      // A disabled model is not offered: the backend refuses to resolve it, so
      // listing it here would only turn into a failure at translate time.
      if (entry?.enabled === false) continue;
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
