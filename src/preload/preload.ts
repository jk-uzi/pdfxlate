/**
 * Preload: the only bridge between the sandboxed renderer and the main process.
 *
 * The renderer gets a small, explicit API surface — never Node or raw IPC.
 */

import { contextBridge, ipcRenderer, webUtils } from "electron";
import { pathToFileURL } from "node:url";

export interface OutputPair {
  mono: string;
  dual: string;
}

export interface ProfileSummary {
  provider: string;
  model: string;
  base_url: string | null;
  reasoning_effort: string | null;
  has_api_key: boolean;
}

export interface ProfilesResponse {
  default: string | null;
  profiles: Record<string, ProfileSummary>;
}

export interface ProgressEvent {
  event: "progress";
  job_id?: string;
  stage: string;
  current: number;
  total: number;
  message: string;
}

export interface DoneEvent {
  event: "done";
  job_id?: string;
  result: { outputs: OutputPair[] };
}

export type SidecarEvent = ProgressEvent | DoneEvent | { event: "ready"; version: string };

export interface TranslateParams {
  files: string[];
  profile?: string;
  output?: string;
  pages?: number[];
  mode?: "fast" | "ultrafast";
  output_mode?: "mono" | "dual" | "both";
  thread?: number;
}

const api = {
  /** Whether the Python engine started successfully. */
  onReady: (cb: (payload: { ok: boolean; error?: string }) => void) =>
    subscribe("sidecar:ready", cb),

  /** Current engine state, for a renderer that loaded after startup. */
  getState: (): Promise<{ ok: boolean; pending: boolean; error?: string } | null> =>
    ipcRenderer.invoke("sidecar:state"),

  /** Progress and completion events from a running job. */
  onEvent: (cb: (event: SidecarEvent) => void) => subscribe("sidecar:event", cb),

  /** Raw stderr from the engine, for the log pane. */
  onLog: (cb: (line: string) => void) => subscribe("sidecar:log", cb),

  request: <T>(method: string, params: Record<string, unknown> = {}): Promise<T> =>
    ipcRenderer.invoke("sidecar:request", method, params),

  /**
   * PDFs in `dir`, plus the ones under its `translated/` subfolder.
   *
   * The renderer is sandboxed, so listing a directory has to happen in the main
   * process. Each entry carries a `translated` flag: outputs are listed so they
   * can be found again, but are not valid inputs for another job. Only the
   * immediate contents are read — this backs a flat list, and walking a tree
   * would make it unbounded.
   */
  listPdfs: (dir: string): Promise<Array<{ path: string; translated: boolean }>> =>
    ipcRenderer.invoke("app:listPdfs", dir),

  /** Native "choose a folder" dialog, for browsing a folder of PDFs. */
  pickFolder: (locale?: string): Promise<string | null> =>
    ipcRenderer.invoke("dialog:pickFolder", locale),

  /**
   * Copy a dropped PDF into `destDir`, returning where it actually landed.
   *
   * The name is made unique rather than overwriting an existing file. Returns
   * the destination path, which differs from the source name when a collision
   * was resolved.
   */
  importPdf: (source: string, destDir: string): Promise<string> =>
    ipcRenderer.invoke("app:importPdf", source, destDir),

  /**
   * Copy one file somewhere the user picks, via a native save dialog.
   *
   * Returns the chosen path, or null if the dialog was cancelled.
   */
  saveAs: (source: string, suggestedName: string, locale?: string): Promise<string | null> =>
    ipcRenderer.invoke("dialog:saveAs", source, suggestedName, locale),

  /**
   * How an agent should launch this install's MCP server.
   *
   * Resolved in the main process because it knows whether this is a source
   * checkout or a packaged install, and only it can ask the OS for the config
   * directory. The renderer must not build this itself: a config that points at
   * a path the app does not actually use is worse than no config.
   */
  mcpLaunch: (): Promise<{ command: string; args: string[] }> =>
    ipcRenderer.invoke("app:mcpLaunch"),

  /**
   * The agents installed on this machine, with their config paths.
   *
   * Only ones whose config file already exists are returned.
   */
  mcpAgents: (): Promise<Array<{ id: string; target: string; path: string }>> =>
    ipcRenderer.invoke("app:mcpAgents"),

  /** System clipboard. Not available as `navigator.clipboard` from `file://`. */
  copyText: (text: string): Promise<void> => ipcRenderer.invoke("clipboard:copy", text),

  /**
   * Native "open files" dialog. `locale` is the renderer's active interface
   * language; the main process falls back to the OS locale when it is omitted.
   */
  pickPdf: (locale?: string): Promise<string[]> =>
    ipcRenderer.invoke("dialog:pickPdf", locale),
  reveal: (target: string): Promise<void> => ipcRenderer.invoke("shell:reveal", target),
  openPath: (target: string): Promise<void> => ipcRenderer.invoke("shell:openPath", target),

  /**
   * Absolute filesystem path -> `file://` URL, for the PDF preview iframe.
   *
   * A pure conversion, not an IPC round trip: the renderer is sandboxed with
   * `contextIsolation` and has no access to Node's `url` module, so the
   * conversion has to be handed to it. Windows paths in particular cannot be
   * built by hand — `pathToFileURL` handles the drive letter, backslashes and
   * percent-encoding.
   */
  fileUrl: (target: string): string => pathToFileURL(target).href,

  /**
   * Absolute path of a dropped `File`.
   *
   * This is the documented replacement for the `File.path` property, which
   * Electron removed in v32 — reading `file.path` now yields `undefined`, which
   * silently broke dropping files into the window. The renderer cannot look
   * this up itself; `webUtils` is main-process/preload-only.
   *
   * Returns an empty string for a `File` that is not backed by a real file
   * (one built in JS), so callers can treat "" as "not a real file".
   */
  pathForFile: (file: File): string => webUtils.getPathForFile(file),

  platform: process.platform,
};

function subscribe<T>(channel: string, cb: (payload: T) => void): () => void {
  const handler = (_event: unknown, payload: T) => cb(payload);
  ipcRenderer.on(channel, handler);
  return () => ipcRenderer.removeListener(channel, handler);
}

contextBridge.exposeInMainWorld("pdfxlate", api);

export type PdfxlateApi = typeof api;
