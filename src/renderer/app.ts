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
  default_provider: string | null;
  default_model: string | null;
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
let currentProvider: string | null = null;
let currentModel: string | null = null;

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
  renderProviderSelect();
  renderModelSelect();
});

// --- engine status ---------------------------------------------------------

function applyEngineState(payload: { ok: boolean; error?: string }): void {
  engineState = payload.ok ? "ok" : "error";
  paintEngineStatus();
  if (payload.ok) {
    void refreshProviders();
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
    currentProvider = pickProvider();
    currentModel = currentProvider ? pickModel(currentProvider) : null;
    renderProviderSelect();
    renderModelSelect();
    applyProviderFields();
    applyModelFields();
  } catch (err) {
    appendLog(t("log.providersFailed", { error: String(err) }));
  }
}

/** The provider to select: the current one, else the default, else the first. */
function pickProvider(): string | null {
  const names = draftProviderNames();
  if (currentProvider && names.includes(currentProvider)) return currentProvider;
  const def = defaultProvider();
  if (def && names.includes(def)) return def;
  return names[0] ?? null;
}

/** The model to select within `provider`: current, else default, else first. */
function pickModel(provider: string): string | null {
  const models = draftModelNames(provider);
  if (currentModel && models.includes(currentModel)) return currentModel;
  const def = defaultModel();
  if (defaultProvider() === provider && def && models.includes(def)) return def;
  return models[0] ?? null;
}

function defaultProvider(): string | null {
  const value = cachedConfig?.default_provider;
  return typeof value === "string" && value ? value : null;
}

function defaultModel(): string | null {
  const value = cachedConfig?.default_model;
  return typeof value === "string" && value ? value : null;
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
  const def = defaultProvider();
  for (const name of names) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name + (def === name ? t("model.defaultSuffix") : "");
    if (name === currentProvider) opt.selected = true;
    select.append(opt);
  }
}

/** Rebuild the model `<select>` for the current provider. */
function renderModelSelect(): void {
  const select = $<HTMLSelectElement>("model-select");
  select.innerHTML = "";
  const names = currentProvider ? draftModelNames(currentProvider) : [];
  if (!names.length) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = t("model.noModels");
    select.append(opt);
    select.disabled = true;
    return;
  }
  select.disabled = false;
  const def = defaultModel();
  const isDefaultProvider = defaultProvider() === currentProvider;
  for (const name of names) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent =
      name + (isDefaultProvider && def === name ? t("model.defaultSuffix") : "");
    if (name === currentModel) opt.selected = true;
    select.append(opt);
  }
}

/** Fill the provider-level fields from the cached config, and mask the key. */
function applyProviderFields(): void {
  const provider = draftProvider(currentProvider);
  $<HTMLInputElement>("field-kind").value = provider?.kind ?? "";
  $<HTMLInputElement>("field-base-url").value = provider?.base_url ?? "";
  // The key field always starts empty: the stored secret is never shown back,
  // only the `has_api_key` flag the summary reports.
  $<HTMLInputElement>("field-api-key").value = "";
  apiKeySaved = !!cachedProviders?.providers[currentProvider ?? ""]?.has_api_key;
  paintApiKeyPlaceholder();
}

/** Fill the model-level fields from the cached config. */
function applyModelFields(): void {
  const model = draftModel(currentProvider, currentModel);
  $<HTMLInputElement>("field-model-id").value = model?.model ?? "";
  $<HTMLSelectElement>("field-reasoning").value = model?.reasoning_effort ?? "";
  $<HTMLInputElement>("field-temperature").value = String(model?.temperature ?? 0);
  const extra = model?.extra_body;
  $<HTMLInputElement>("field-extra-body").value =
    extra && Object.keys(extra).length ? JSON.stringify(extra) : "";
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
  currentProvider = (e.target as HTMLSelectElement).value || null;
  // A model alias is only meaningful within its own provider, so start the new
  // provider at its default (or first) model rather than carrying the old one.
  currentModel = currentProvider ? pickModel(currentProvider) : null;
  renderModelSelect();
  applyProviderFields();
  applyModelFields();
});

$("model-select").addEventListener("change", (e) => {
  currentModel = (e.target as HTMLSelectElement).value || null;
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
  prov.models[alias] = {
    ...existing,
    model: $<HTMLInputElement>("field-model-id").value || alias,
    reasoning_effort: $<HTMLSelectElement>("field-reasoning").value || null,
    temperature: Number($<HTMLInputElement>("field-temperature").value) || 0,
    extra_body: extraBody,
  };
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
  currentProvider = name;
  currentModel = null;
  renderProviderSelect();
  renderModelSelect();
  applyProviderFields();
  applyModelFields();
  void saveConfig("model.saved");
});

$("btn-remove-provider").addEventListener("click", () => {
  const name = currentProvider;
  if (!name) {
    setProfileMessage("model.noProvider", undefined, true);
    return;
  }
  if (!confirm(t("model.removeProviderConfirm", { name }))) return;
  const providers = draftProviders();
  delete providers[name];
  if (cachedConfig && cachedConfig.default_provider === name) {
    cachedConfig.default_provider = null;
    cachedConfig.default_model = null;
  }
  // Re-pick against the mutated draft so the selection can never point at the
  // provider that was just deleted.
  currentProvider = null;
  currentModel = null;
  currentProvider = pickProvider();
  currentModel = currentProvider ? pickModel(currentProvider) : null;
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
  const provider = currentProvider;
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
  currentModel = name;
  renderModelSelect();
  applyModelFields();
  void saveConfig("model.saved");
});

$("btn-remove-model").addEventListener("click", () => {
  const provider = currentProvider;
  const alias = currentModel;
  if (!provider || !alias) {
    setProfileMessage("model.noModel", undefined, true);
    return;
  }
  if (!confirm(t("model.removeModelConfirm", { name: alias }))) return;
  const providers = draftProviders();
  const prov = providers[provider];
  if (prov?.models) delete prov.models[alias];
  if (
    cachedConfig &&
    cachedConfig.default_provider === provider &&
    cachedConfig.default_model === alias
  ) {
    cachedConfig.default_model = null;
  }
  // Re-pick from the mutated draft so the selection never points at a deleted
  // model.
  currentModel = pickModel(provider);
  renderModelSelect();
  applyModelFields();
  void saveConfig("model.saved");
});

$("btn-save-model").addEventListener("click", async () => {
  const provider = currentProvider;
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
  if (currentModel) commitModelFields(provider, currentModel);
  await saveConfig("model.saved");
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
        provider: currentProvider ?? undefined,
        model: currentModel ?? undefined,
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
