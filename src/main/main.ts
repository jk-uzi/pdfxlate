/** Application entry point: window lifecycle, IPC wiring, sidecar control. */

import { app, BrowserWindow, dialog, ipcMain, shell } from "electron";
import path from "node:path";

import { Sidecar, type SidecarEvent } from "./sidecar";

const isDev = process.argv.includes("--dev");

// Compiled as CommonJS, so __dirname is the out/main directory.
const currentDir = __dirname;
const repoRoot = path.resolve(currentDir, "..", "..");

let mainWindow: BrowserWindow | null = null;
let sidecar: Sidecar | null = null;

/**
 * Engine state is kept here, not only pushed as an event: the renderer may
 * finish loading after the sidecar is already up, and a one-shot event would
 * be lost, leaving the UI stuck on "starting…".
 */
type EngineState =
  | { ok: boolean; pending: boolean; version?: string; error?: string }
  | null;

let engineState: EngineState = null;

function broadcast(channel: string, payload: unknown): void {
  mainWindow?.webContents.send(channel, payload);
}

async function startSidecar(): Promise<Sidecar> {
  // Keep config, cache and downloaded assets inside the OS-appropriate
  // per-user application data directory.
  const configDir = app.getPath("userData");
  const instance = Sidecar.resolve(repoRoot, configDir, isDev);
  instance.onLog((line) => broadcast("sidecar:log", line));
  instance.onEvent((event: SidecarEvent) => broadcast("sidecar:event", event));
  const ready = await instance.start();
  return instance;
}

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1100,
    height: 780,
    minWidth: 820,
    minHeight: 600,
    title: "pdfxlate",
    show: false,
    webPreferences: {
      preload: path.join(currentDir, "..", "preload", "preload.js"),
      // Renderer runs untrusted page code: keep Node out of it and expose
      // only the explicit API surface defined in the preload script.
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  mainWindow.once("ready-to-show", () => mainWindow?.show());
  mainWindow.on("closed", () => {
    mainWindow = null;
  });

  void mainWindow.loadFile(path.join(currentDir, "..", "renderer", "index.html"));

  if (isDev) mainWindow.webContents.openDevTools({ mode: "detach" });
}

function registerIpc(): void {
  ipcMain.handle("sidecar:request", async (_event, method: string, params: unknown) => {
    if (!sidecar) throw new Error("sidecar is not running");
    return sidecar.request(method, (params ?? {}) as Record<string, unknown>);
  });

  // Lets a late-loading renderer learn the current engine state instead of
  // depending on having caught the one-shot ready event.
  ipcMain.handle("sidecar:state", async () => engineState);

  ipcMain.handle("dialog:pickPdf", async () => {
    const result = await dialog.showOpenDialog(mainWindow!, {
      title: "Select PDF files",
      properties: ["openFile", "multiSelections"],
      filters: [{ name: "PDF", extensions: ["pdf"] }],
    });
    return result.canceled ? [] : result.filePaths;
  });

  ipcMain.handle("dialog:pickOutputDir", async () => {
    const result = await dialog.showOpenDialog(mainWindow!, {
      title: "Select output directory",
      properties: ["openDirectory", "createDirectory"],
    });
    return result.canceled ? null : result.filePaths[0];
  });

  ipcMain.handle("shell:reveal", async (_event, target: string) => {
    shell.showItemInFolder(target);
  });

  ipcMain.handle("shell:openPath", async (_event, target: string) => {
    await shell.openPath(target);
  });
}

app.whenReady().then(async () => {
  registerIpc();
  createWindow();
  engineState = { ok: false, pending: true };
  try {
    sidecar = await startSidecar();
    engineState = { ok: true, pending: false };
    broadcast("sidecar:ready", { ok: true });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    engineState = { ok: false, pending: false, error: message };
    broadcast("sidecar:ready", { ok: false, error: message });
    dialog.showErrorBox(
      "Could not start the translation engine",
      `${message}\n\nIf running from source, make sure the Python environment is set up:\n` +
        `cd python && python -m venv .venv && .venv/bin/pip install -e .`,
    );
  }

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  sidecar?.stop();
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => sidecar?.stop());
