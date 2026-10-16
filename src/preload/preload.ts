/**
 * Preload: the only bridge between the sandboxed renderer and the main process.
 *
 * The renderer gets a small, explicit API surface — never Node or raw IPC.
 */

import { contextBridge, ipcRenderer } from "electron";
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
   * Native "open files" dialog. `locale` is the renderer's active interface
   * language; the main process falls back to the OS locale when it is omitted.
   */
  pickPdf: (locale?: string): Promise<string[]> =>
    ipcRenderer.invoke("dialog:pickPdf", locale),
  pickOutputDir: (locale?: string): Promise<string | null> =>
    ipcRenderer.invoke("dialog:pickOutputDir", locale),
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

  platform: process.platform,
};

function subscribe<T>(channel: string, cb: (payload: T) => void): () => void {
  const handler = (_event: unknown, payload: T) => cb(payload);
  ipcRenderer.on(channel, handler);
  return () => ipcRenderer.removeListener(channel, handler);
}

contextBridge.exposeInMainWorld("pdfxlate", api);

export type PdfxlateApi = typeof api;
