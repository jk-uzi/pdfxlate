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

interface ProfileSummary {
  provider: string;
  model: string;
  base_url: string | null;
  reasoning_effort: string | null;
  has_api_key: boolean;
}

interface ProfilesResponse {
  default: string | null;
  profiles: Record<string, ProfileSummary>;
}

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
  pickPdf(locale?: string): Promise<string[]>;
  pickOutputDir(locale?: string): Promise<string | null>;
  reveal(target: string): Promise<void>;
  openPath(target: string): Promise<void>;
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
let selectedFiles: string[] = [];
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

// Remembered so a locale switch can rebuild strings that are otherwise only
// produced at event time — without disturbing the job in progress.
let lastResults: Array<{ mono: string; dual: string }> = [];
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

// --- locale ----------------------------------------------------------------

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
  paintApiKeyPlaceholder();
  renderDownloadButton();
  paintProgress();
  if (lastResults.length) paintResults();
  paintProfileMessage();
});

// --- engine status ---------------------------------------------------------

function applyEngineState(payload: { ok: boolean; error?: string }): void {
  engineState = payload.ok ? "ok" : "error";
  paintEngineStatus();
  if (payload.ok) {
    void refreshProfiles();
    void refreshAssets();
  } else {
    appendLog(t("log.engineFailed", { error: payload.error ?? t("log.unknownError") }));
  }
  updateTranslateButton();
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
  showProgress();
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

// --- file selection --------------------------------------------------------

function renderFiles(): void {
  const list = $("file-list");
  list.innerHTML = "";
  for (const file of selectedFiles) {
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = file.split(/[\\/]/).pop() ?? file;
    name.title = file;
    const remove = document.createElement("button");
    remove.className = "remove";
    remove.textContent = "×";
    remove.title = t("docs.remove");
    remove.addEventListener("click", () => {
      selectedFiles = selectedFiles.filter((f) => f !== file);
      renderFiles();
    });
    li.append(name, remove);
    list.append(li);
  }
  updateTranslateButton();
}

function addFiles(paths: string[]): void {
  for (const p of paths) {
    if (p.toLowerCase().endsWith(".pdf") && !selectedFiles.includes(p)) selectedFiles.push(p);
  }
  renderFiles();
}

$("btn-pick").addEventListener("click", async () => addFiles(await api.pickPdf(getLocale())));

const dropzone = $("dropzone");
dropzone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropzone.classList.add("dropzone--over");
});
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dropzone--over"));
dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("dropzone--over");
  // Electron exposes absolute paths on dropped files via webUtils in newer
  // versions; fall back to the File path property where available.
  const files = Array.from(e.dataTransfer?.files ?? []);
  const paths = files
    .map((f) => (f as File & { path?: string }).path)
    .filter((p): p is string => Boolean(p));
  if (paths.length) addFiles(paths);
  else appendLog(t("docs.dropUnreadable"));
});

// --- profiles --------------------------------------------------------------

async function refreshProfiles(): Promise<void> {
  try {
    const data = await api.request<ProfilesResponse>("profiles.list");
    const select = $<HTMLSelectElement>("profile-select");
    select.innerHTML = "";
    const names = Object.keys(data.profiles);
    if (!names.length) {
      const opt = document.createElement("option");
      opt.textContent = t("model.noProfiles");
      opt.value = "";
      select.append(opt);
      return;
    }
    for (const name of names) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      if (name === data.default) opt.selected = true;
      select.append(opt);
    }
    applyProfile(data, select.value);
  } catch (err) {
    appendLog(t("log.profilesFailed", { error: String(err) }));
  }
}

let cachedProfiles: ProfilesResponse | null = null;

function applyProfile(data: ProfilesResponse, name: string): void {
  cachedProfiles = data;
  const profile = data.profiles[name];
  if (!profile) return;
  $<HTMLInputElement>("field-provider").value = profile.provider;
  $<HTMLInputElement>("field-base-url").value = profile.base_url ?? "";
  $<HTMLInputElement>("field-model").value = profile.model;
  $<HTMLSelectElement>("field-reasoning").value = profile.reasoning_effort ?? "";
  const keyField = $<HTMLInputElement>("field-api-key");
  keyField.value = "";
  apiKeySaved = profile.has_api_key;
  paintApiKeyPlaceholder();
}

function paintApiKeyPlaceholder(): void {
  $<HTMLInputElement>("field-api-key").placeholder = t(
    apiKeySaved ? "model.apiKeySaved" : "model.apiKeyPlaceholder",
  );
}

$("profile-select").addEventListener("change", (e) => {
  if (cachedProfiles) applyProfile(cachedProfiles, (e.target as HTMLSelectElement).value);
});

function paintProfileMessage(): void {
  const msg = $("profile-msg");
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

$("btn-save-profile").addEventListener("click", async () => {
  const name = $<HTMLSelectElement>("profile-select").value;
  if (!name) {
    setProfileMessage("model.noProfile", undefined, true);
    return;
  }
  try {
    const config = await api.request<Record<string, unknown>>("config.get");
    const extraRaw = $<HTMLInputElement>("field-extra-body").value.trim();
    let extraBody: Record<string, unknown> = {};
    if (extraRaw) {
      try {
        extraBody = JSON.parse(extraRaw);
      } catch {
        setProfileMessage("model.extraBodyInvalid", undefined, true);
        return;
      }
    }
    const profiles = (config.profiles ?? {}) as Record<string, Record<string, unknown>>;
    const existing = profiles[name] ?? {};
    const apiKey = $<HTMLInputElement>("field-api-key").value;
    profiles[name] = {
      ...existing,
      provider: $<HTMLInputElement>("field-provider").value,
      base_url: $<HTMLInputElement>("field-base-url").value || null,
      model: $<HTMLInputElement>("field-model").value,
      reasoning_effort: $<HTMLSelectElement>("field-reasoning").value || null,
      temperature: Number($<HTMLInputElement>("field-temperature").value) || 0,
      extra_body: extraBody,
      // Only overwrite the stored key when the user typed a new one, so a
      // masked value never erases the saved credential.
      ...(apiKey ? { api_key: apiKey } : {}),
    };
    config.profiles = profiles;
    await api.request("config.save", { config });
    setProfileMessage("model.saved");
    await refreshProfiles();
  } catch (err) {
    setProfileMessage("model.saveFailed", { error: String(err) }, true);
  }
});

// --- options ---------------------------------------------------------------

$("btn-pick-output").addEventListener("click", async () => {
  const dir = await api.pickOutputDir(getLocale());
  if (dir) {
    outputDir = dir;
    $<HTMLInputElement>("field-output").value = dir;
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
    engineState !== "ok" || selectedFiles.length === 0 || activeJobId !== null;
}

$("btn-translate").addEventListener("click", async () => {
  if (!selectedFiles.length) return;
  const mode = $<HTMLSelectElement>("field-mode").value as "fast" | "ultrafast";
  activeJobId = "pending";
  updateTranslateButton();
  $("btn-cancel").classList.remove("hidden");
  $("result-card").classList.add("hidden");
  showProgress();
  setProgress("starting", 0, 1, { key: "progress.starting" });

  try {
    const result = await api.request<{ job_id: string; outputs: Array<{ mono: string; dual: string }> }>(
      "translate.start",
      {
        files: selectedFiles,
        profile: $<HTMLSelectElement>("profile-select").value || undefined,
        output: outputDir ?? undefined,
        pages: parsePages($<HTMLInputElement>("field-pages").value),
        mode,
        thread: Number($<HTMLInputElement>("field-thread").value) || undefined,
      },
    );
    renderResults(result.outputs);
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

function showProgress(): void {
  $("progress-card").classList.remove("hidden");
}

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
 * Deliberately does not show/hide the card: repainting after a locale switch
 * must leave the current stage and percentage exactly as they were.
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
  showProgress();
  // `event.message` is a diagnostic from the Python engine, not UI copy, so it
  // is shown verbatim in either locale.
  setProgress(event.stage, event.current, event.total, { raw: event.message });
}

function handleDone(event: DoneEvent): void {
  setProgress("done", 1, 1, { key: "progress.done" });
  renderResults(event.result.outputs);
}

function renderResults(outputs: Array<{ mono: string; dual: string }>): void {
  lastResults = outputs;
  paintResults();
  $("result-card").classList.remove("hidden");
}

/** Rebuild the result rows from `lastResults`; visibility is left untouched. */
function paintResults(): void {
  const list = $("result-list");
  list.innerHTML = "";
  for (const pair of lastResults) {
    for (const [labelKey, file] of [
      ["results.mono", pair.mono],
      ["results.dual", pair.dual],
    ] as const) {
      const li = document.createElement("li");
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = t(labelKey);
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = file.split(/[\\/]/).pop() ?? file;
      name.title = file;
      const open = document.createElement("button");
      open.className = "btn btn--small";
      open.textContent = t("results.open");
      open.addEventListener("click", () => void api.openPath(file));
      const reveal = document.createElement("button");
      reveal.className = "btn btn--small";
      reveal.textContent = t("results.reveal");
      reveal.addEventListener("click", () => void api.reveal(file));
      li.append(tag, name, open, reveal);
      list.append(li);
    }
  }
}

// --- log -------------------------------------------------------------------

function appendLog(line: string): void {
  const pane = $("log-pane");
  const atBottom = pane.scrollTop + pane.clientHeight >= pane.scrollHeight - 20;
  pane.textContent += line + "\n";
  if (atBottom) pane.scrollTop = pane.scrollHeight;
}

updateTranslateButton();
