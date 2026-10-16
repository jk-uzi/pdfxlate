/** Application entry point: window lifecycle, IPC wiring, sidecar control. */

import { app, BrowserWindow, dialog, ipcMain, Menu, shell } from "electron";
import path from "node:path";

import { Sidecar, type SidecarEvent } from "./sidecar";

const isDev = process.argv.includes("--dev");

// Compiled as CommonJS, so __dirname is the out/main directory.
const currentDir = __dirname;
const repoRoot = path.resolve(currentDir, "..", "..");

/**
 * Height of the app's own title bar, in pixels.
 *
 * Must match `--titlebar-height` in style.css: the window-controls overlay is
 * placed by the OS from this value, so a mismatch leaves the system buttons
 * vertically off-centre from the bar they sit in.
 */
const TITLEBAR_HEIGHT = 50;

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

type Locale = "en" | "zh";

/**
 * Native dialog strings owned by the main process.
 *
 * Deliberately duplicated from the renderer's i18n table rather than imported
 * from it: this half of the app compiles as CommonJS (tsconfig.node.json)
 * while the renderer is native ESM, and the renderer module also touches
 * browser-only globals (`document`, `localStorage`, `navigator`). Importing it
 * here would drag a browser-only dependency into the main process, so the four
 * strings this process shows are kept locally. Keys mirror the renderer's
 * `dialog.*` entries so the two tables stay easy to compare by eye.
 */
const DIALOG_STRINGS: Record<
  Locale,
  {
    selectPdf: string;
    selectOutput: string;
    engineErrorTitle: string;
    engineErrorHint: string;
  }
> = {
  en: {
    selectPdf: "Select PDF files",
    selectOutput: "Select output directory",
    engineErrorTitle: "Could not start the translation engine",
    engineErrorHint: "If running from source, make sure the Python environment is set up:",
  },
  zh: {
    selectPdf: "选择 PDF 文件",
    selectOutput: "选择输出目录",
    engineErrorTitle: "无法启动翻译引擎",
    engineErrorHint: "若从源码运行，请确认 Python 环境已配置：",
  },
};

/**
 * Map an incoming locale tag to a supported locale: anything starting with
 * "zh" (case-insensitive) is Chinese, everything else English.
 *
 * The renderer passes its active locale over IPC. When the tag is missing —
 * an older caller, or a call made before the renderer knows its locale — fall
 * back to the OS locale Electron reports.
 */
function resolveLocale(tag?: string): Locale {
  const value = tag || app.getLocale();
  return value.toLowerCase().startsWith("zh") ? "zh" : "en";
}

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
    width: 1280,
    height: 820,
    // The settings sidebar takes a fixed 340px, so the preview pane needs the
    // extra width to stay usable; below this the PDF page is unreadably small.
    minWidth: 1000,
    minHeight: 640,
    title: "pdfxlate",
    show: false,
    // The app draws its own title bar. `hidden` removes the OS title bar and
    // menu strip; the overlay keeps the native minimise/maximise/close buttons,
    // which the OS still paints in the top-right. Drawing those ourselves would
    // mean reimplementing window controls, hover states, double-click-to-
    // maximise, Aero Snap, per-monitor DPI and their accessibility labels.
    titleBarStyle: "hidden",
    titleBarOverlay: {
      color: "#ffffff",
      symbolColor: "#1f2328",
      height: TITLEBAR_HEIGHT,
    },
    webPreferences: {
      preload: path.join(currentDir, "..", "preload", "preload.js"),
      // Renderer runs untrusted page code: keep Node out of it and expose
      // only the explicit API surface defined in the preload script.
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      // Required by the PDF preview: Chromium's built-in viewer is a plugin,
      // and with this off the frame loads as a blank page. It only ever loads
      // local file:// PDFs the user picked; the renderer's CSP still refuses
      // remote content and inline script.
      plugins: true,
    },
  });

  mainWindow.once("ready-to-show", () => mainWindow?.show());
  mainWindow.on("closed", () => {
    mainWindow = null;
  });

  void mainWindow.loadFile(path.join(currentDir, "..", "renderer", "index.html"));

  if (isDev) {
    mainWindow.webContents.openDevTools({ mode: "detach" });
    registerDevShortcuts(mainWindow);
  }
}

/**
 * Restore the shortcuts that came with Electron's default menu.
 *
 * Dropping the menu (see `app.whenReady`) takes its accelerators with it, which
 * is only a problem while developing: reload and DevTools are how a UI change
 * gets inspected. Text editing shortcuts are Chromium behaviour and unaffected.
 */
function registerDevShortcuts(window: BrowserWindow): void {
  window.webContents.on("before-input-event", (event, input) => {
    if (input.type !== "keyDown") return;
    const key = input.key.toLowerCase();
    const toggleDevTools =
      key === "f12" || (input.control && input.shift && key === "i");
    if (toggleDevTools) {
      event.preventDefault();
      window.webContents.toggleDevTools();
    } else if (input.control && key === "r") {
      event.preventDefault();
      window.webContents.reload();
    }
  });
}

function registerIpc(): void {
  ipcMain.handle("sidecar:request", async (_event, method: string, params: unknown) => {
    if (!sidecar) throw new Error("sidecar is not running");
    return sidecar.request(method, (params ?? {}) as Record<string, unknown>);
  });

  // Lets a late-loading renderer learn the current engine state instead of
  // depending on having caught the one-shot ready event.
  ipcMain.handle("sidecar:state", async () => engineState);

  ipcMain.handle("dialog:pickPdf", async (_event, locale?: string) => {
    const result = await dialog.showOpenDialog(mainWindow!, {
      title: DIALOG_STRINGS[resolveLocale(locale)].selectPdf,
      properties: ["openFile", "multiSelections"],
      filters: [{ name: "PDF", extensions: ["pdf"] }],
    });
    return result.canceled ? [] : result.filePaths;
  });

  ipcMain.handle("dialog:pickOutputDir", async (_event, locale?: string) => {
    const result = await dialog.showOpenDialog(mainWindow!, {
      title: DIALOG_STRINGS[resolveLocale(locale)].selectOutput,
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
  // The app supplies its own title bar, and the default menu's items (File,
  // Edit, View, …) have no counterpart in this UI. Removing it also removes
  // its accelerators; registerDevShortcuts puts the dev ones back.
  Menu.setApplicationMenu(null);

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
    // This fires before the renderer can report a locale, so the language can
    // only come from the OS setting Electron exposes.
    const startupStrings = DIALOG_STRINGS[resolveLocale()];
    dialog.showErrorBox(
      startupStrings.engineErrorTitle,
      `${message}\n\n${startupStrings.engineErrorHint}\n` +
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
