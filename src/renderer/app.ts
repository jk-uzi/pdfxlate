/**
 * Renderer logic: file selection, model configuration, job control, progress.
 *
 * Talks to the main process only through `window.pdfxlate` (see preload.ts).
 */

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
  pickPdf(): Promise<string[]>;
  pickOutputDir(): Promise<string | null>;
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
let engineReady = false;

// --- engine status ---------------------------------------------------------

function applyEngineState(payload: { ok: boolean; error?: string }): void {
  engineReady = payload.ok;
  const status = $("engine-status");
  if (payload.ok) {
    status.textContent = "engine ready";
    status.className = "status status--ok";
    void refreshProfiles();
    void refreshAssets();
  } else {
    status.textContent = "engine failed";
    status.className = "status status--error";
    appendLog(`engine failed to start: ${payload.error ?? "unknown error"}`);
  }
  updateTranslateButton();
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
    appendLog(`asset check failed: ${String(err)}`);
  }
}

$("btn-download-assets").addEventListener("click", async () => {
  const button = $<HTMLButtonElement>("btn-download-assets");
  button.disabled = true;
  button.textContent = "Downloading…";
  showProgress();
  try {
    await api.request("assets.download", { targets: ["layout_model", "fonts"], languages: ["zh"] });
    $("setup-banner").classList.add("hidden");
    setProgress("setup", 1, 1, "Assets ready.");
  } catch (err) {
    setProgress("setup", 0, 1, `Download failed: ${String(err)}`);
  } finally {
    button.disabled = false;
    button.textContent = "Download now";
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
    remove.title = "Remove";
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

$("btn-pick").addEventListener("click", async () => addFiles(await api.pickPdf()));

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
  else appendLog("Could not read dropped file paths; use “Choose files…” instead.");
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
      opt.textContent = "(no profiles configured)";
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
    appendLog(`could not load profiles: ${String(err)}`);
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
  keyField.placeholder = profile.has_api_key ? "(saved — leave blank to keep)" : "sk-…";
}

$("profile-select").addEventListener("change", (e) => {
  if (cachedProfiles) applyProfile(cachedProfiles, (e.target as HTMLSelectElement).value);
});

$("btn-save-profile").addEventListener("click", async () => {
  const msg = $("profile-msg");
  msg.className = "msg";
  const name = $<HTMLSelectElement>("profile-select").value;
  if (!name) {
    msg.textContent = "No profile to save.";
    msg.className = "msg msg--error";
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
        msg.textContent = "Extra body is not valid JSON.";
        msg.className = "msg msg--error";
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
    msg.textContent = "Saved.";
    await refreshProfiles();
  } catch (err) {
    msg.textContent = `Save failed: ${String(err)}`;
    msg.className = "msg msg--error";
  }
});

// --- options ---------------------------------------------------------------

$("btn-pick-output").addEventListener("click", async () => {
  const dir = await api.pickOutputDir();
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
  button.disabled = !engineReady || selectedFiles.length === 0 || activeJobId !== null;
}

$("btn-translate").addEventListener("click", async () => {
  if (!selectedFiles.length) return;
  const mode = $<HTMLSelectElement>("field-mode").value as "fast" | "ultrafast";
  activeJobId = "pending";
  updateTranslateButton();
  $("btn-cancel").classList.remove("hidden");
  $("result-card").classList.add("hidden");
  showProgress();
  setProgress("starting", 0, 1, "Starting…");

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
    setProgress("error", 0, 1, `Translation failed: ${String(err)}`);
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
    appendLog(`cancel failed: ${String(err)}`);
  }
});

// --- progress + results ----------------------------------------------------

function showProgress(): void {
  $("progress-card").classList.remove("hidden");
}

function setProgress(stage: string, current: number, total: number, message: string): void {
  const pct = total > 0 ? Math.min(100, Math.round((current / total) * 100)) : 0;
  $("progress-bar").style.width = `${pct}%`;
  const counter = total > 1 ? ` (${current}/${total})` : "";
  $("progress-text").textContent = `${stage}${counter} — ${message}`;
}

function handleProgress(event: JobProgress): void {
  showProgress();
  setProgress(event.stage, event.current, event.total, event.message);
}

function handleDone(event: DoneEvent): void {
  setProgress("done", 1, 1, "Finished.");
  renderResults(event.result.outputs);
}

function renderResults(outputs: Array<{ mono: string; dual: string }>): void {
  const card = $("result-card");
  const list = $("result-list");
  list.innerHTML = "";
  for (const pair of outputs) {
    for (const [label, file] of [
      ["mono", pair.mono],
      ["dual", pair.dual],
    ] as const) {
      const li = document.createElement("li");
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = label;
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = file.split(/[\\/]/).pop() ?? file;
      name.title = file;
      const open = document.createElement("button");
      open.className = "btn btn--small";
      open.textContent = "Open";
      open.addEventListener("click", () => void api.openPath(file));
      const reveal = document.createElement("button");
      reveal.className = "btn btn--small";
      reveal.textContent = "Show";
      reveal.addEventListener("click", () => void api.reveal(file));
      li.append(tag, name, open, reveal);
      list.append(li);
    }
  }
  card.classList.remove("hidden");
}

// --- log -------------------------------------------------------------------

function appendLog(line: string): void {
  const pane = $("log-pane");
  const atBottom = pane.scrollTop + pane.clientHeight >= pane.scrollHeight - 20;
  pane.textContent += line + "\n";
  if (atBottom) pane.scrollTop = pane.scrollHeight;
}

updateTranslateButton();
