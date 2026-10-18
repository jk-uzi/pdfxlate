/** Application entry point: window lifecycle, IPC wiring, sidecar control. */

import { app, BrowserWindow, clipboard, dialog, ipcMain, Menu, shell } from "electron";
import { existsSync } from "node:fs";
import { copyFile, readdir } from "node:fs/promises";
import os from "node:os";
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
 * Where the engine writes its output, relative to each source document.
 *
 * Duplicated from `python/pdfxlate/pdf/pipeline.py` on purpose: this process has
 * to *list* that folder, and importing Python is not an option. Keep the two in
 * step — the constant is named the same there.
 */
const TRANSLATED_SUBDIR = "translated";

/** One row handed to the renderer's documents list. */
interface PdfEntry {
  path: string;
  translated: boolean;
}

/**
 * Native dialog strings owned by the main process.
 *
 * Deliberately duplicated from the renderer's i18n table rather than imported
 * from it: this half of the app compiles as CommonJS (tsconfig.node.json)
 * while the renderer is native ESM, and the renderer module also touches
 * browser-only globals (`document`, `localStorage`, `navigator`). Importing it
 * here would drag a browser-only dependency into the main process, so the few
 * strings this process shows are kept locally. Keys mirror the renderer's
 * `dialog.*` entries so the two tables stay easy to compare by eye.
 */
const DIALOG_STRINGS: Record<
  Locale,
  {
    selectPdf: string;
    pickFolder: string;
    saveAs: string;
    engineErrorTitle: string;
    engineErrorHint: string;
  }
> = {
  en: {
    selectPdf: "Select PDF files",
    pickFolder: "Choose a folder of PDFs",
    saveAs: "Save a copy as",
    engineErrorTitle: "Could not start the translation engine",
    engineErrorHint: "If running from source, make sure the Python environment is set up:",
  },
  zh: {
    selectPdf: "选择 PDF 文件",
    pickFolder: "选择包含 PDF 的文件夹",
    saveAs: "另存为",
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

  // `show: false` until the page has something to draw, so the window does not
  // flash an empty white rectangle while the PDF viewer and fonts settle.
  //
  // Two triggers, and both are needed. `ready-to-show` is the documented one,
  // but with `titleBarStyle: "hidden"` plus `titleBarOverlay` it is frequently
  // NOT emitted in this Electron version — measured: it never fired in 3 of 5
  // launches, leaving a window that existed, rendered correctly, and was never
  // shown. `did-finish-load` does fire in that configuration, so it is the
  // reliable trigger and `ready-to-show` is kept as the faster path when it
  // works. Whichever arrives first shows the window; the other is a no-op.
  let shown = false;
  const reveal = (): void => {
    if (shown) return;
    shown = true;
    mainWindow?.show();
  };
  mainWindow.once("ready-to-show", reveal);
  mainWindow.webContents.once("did-finish-load", reveal);
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

/**
 * Pick an unused path in `dir` for `name`.
 *
 * Dropping a file into a folder must never silently replace what is already
 * there, so a collision gets a ` (2)`, ` (3)` … suffix. The extension is kept
 * at the end so the result is still recognised as a PDF.
 */
function uniquePath(dir: string, name: string): string {
  const ext = path.extname(name);
  const stem = path.basename(name, ext);
  let candidate = path.join(dir, name);
  for (let n = 2; existsSync(candidate); n++) {
    candidate = path.join(dir, `${stem} (${n})${ext}`);
  }
  return candidate;
}

/**
 * Agents the app knows how to write an MCP entry for.
 *
 * Resolved here rather than in the renderer because only the main process knows
 * the platform paths (`app.getPath`), and because on Windows the environment
 * variables are what actually locate these directories.
 *
 * `target` names both the file format and where the entry goes inside it, and
 * must match the keys `pdfxlate.mcp_config` understands. They are not uniform:
 * ZCode nests its servers under `mcp.servers` and wants a transport type, and
 * Codex uses TOML. That is exactly why each one is declared explicitly rather
 * than generated from a template.
 *
 * Only agents whose config file already exists are offered. A missing file
 * means the agent is not installed (or has never run), and creating one for a
 * program that is not there would be a guess.
 */
interface McpAgent {
  id: string;
  /** Dotted key inside the file, or "toml"; see pdfxlate.mcp_config. */
  target: string;
  path: string;
}

function mcpAgents(): McpAgent[] {
  const home = os.homedir();
  const appData = app.getPath("appData");
  const candidates: McpAgent[] = [
    {
      id: "claude-desktop",
      target: "mcpServers",
      path: path.join(appData, "Claude", "claude_desktop_config.json"),
    },
    { id: "claude-code", target: "mcpServers", path: path.join(home, ".claude.json") },
    { id: "codex", target: "toml", path: path.join(home, ".codex", "config.toml") },
    { id: "workbuddy", target: "mcpServers", path: path.join(home, ".workbuddy", "mcp.json") },
    {
      id: "zcode",
      target: "mcp.servers",
      path: path.join(home, ".zcode", "cli", "config.json"),
    },
  ];
  return candidates.filter((agent) => existsSync(agent.path));
}

/**
 * The command an agent should run to reach this install's MCP server.
 *
 * `--mcp` is what turns the engine binary into the MCP server. For a packaged
 * install that flag is the only way in: an onedir bundle is the interpreter
 * with one entry point baked in, so there is no `python -m` to offer an agent.
 */
function mcpLaunch(): { command: string; args: string[] } {
  const launch = Sidecar.resolveLaunch(repoRoot, app.getPath("userData"), isDev, ["--mcp"]);
  return { command: launch.command, args: launch.args };
}

function registerIpc(): void {
  /** The command and arguments an agent should be given for this install. */
  ipcMain.handle("app:mcpLaunch", async () => mcpLaunch());

  /** Agents whose config file exists, so the UI can offer to write into them. */
  ipcMain.handle("app:mcpAgents", async () => mcpAgents());

  /**
   * Copy text to the system clipboard.
   *
   * Not `navigator.clipboard` in the renderer: that requires a secure context
   * and the app is served from `file://`, where the call can simply be refused.
   */
  ipcMain.handle("clipboard:copy", async (_event, text: string) => {
    clipboard.writeText(text);
  });

  ipcMain.handle("sidecar:request", async (_event, method: string, params: unknown) => {
    if (!sidecar) throw new Error("sidecar is not running");
    return sidecar.request(method, (params ?? {}) as Record<string, unknown>);
  });

  // Lets a late-loading renderer learn the current engine state instead of
  // depending on having caught the one-shot ready event.
  ipcMain.handle("sidecar:state", async () => engineState);

  /**
   * PDFs in a folder, plus the ones the engine wrote into its `translated/`
   * subfolder.
   *
   * Non-recursive apart from that one known subfolder: the list is a flat set
   * of things you might open, and walking every subfolder would make it
   * unbounded. Outputs are included so a finished translation is findable
   * again after a restart — they are marked rather than silently mixed in, so
   * the UI can keep them out of the next job (translating a translation is
   * never what anyone means).
   *
   * A missing folder is an ordinary outcome (it was deleted since it was
   * remembered), so it is reported as a message the UI can show rather than as
   * an opaque throw.
   */
  ipcMain.handle("app:listPdfs", async (_event, dir: string) => {
    if (typeof dir !== "string" || !dir.trim()) throw new Error("no folder given");
    if (!existsSync(dir)) throw new Error(`folder no longer exists: ${dir}`);

    const scan = async (target: string, translated: boolean): Promise<PdfEntry[]> => {
      let entries;
      try {
        entries = await readdir(target, { withFileTypes: true });
      } catch {
        // The translated subfolder need not exist, and a race with a deletion
        // should not fail the whole listing.
        return [];
      }
      return entries
        .filter((e) => e.isFile() && e.name.toLowerCase().endsWith(".pdf"))
        .map((e) => path.join(target, e.name))
        .sort((a, b) => a.localeCompare(b))
        .map((file) => ({ path: file, translated }));
    };

    return [
      ...(await scan(dir, false)),
      ...(await scan(path.join(dir, TRANSLATED_SUBDIR), true)),
    ];
  });

  ipcMain.handle("dialog:pickPdf", async (_event, locale?: string) => {
    const result = await dialog.showOpenDialog(mainWindow!, {
      title: DIALOG_STRINGS[resolveLocale(locale)].selectPdf,
      properties: ["openFile", "multiSelections"],
      filters: [{ name: "PDF", extensions: ["pdf"] }],
    });
    return result.canceled ? [] : result.filePaths;
  });

  /** Choose a folder of PDFs to browse. */
  ipcMain.handle("dialog:pickFolder", async (_event, locale?: string) => {
    const result = await dialog.showOpenDialog(mainWindow!, {
      title: DIALOG_STRINGS[resolveLocale(locale)].pickFolder,
      properties: ["openDirectory"],
    });
    return result.canceled ? null : result.filePaths[0];
  });

  /**
   * Copy a dropped PDF into a folder, without ever overwriting.
   *
   * A drop carries an absolute path, but the renderer cannot move bytes, so the
   * copy happens here. If the source already sits in the destination there is
   * nothing to do and the source path is returned unchanged — that keeps
   * dropping a file that is already in the folder from producing a duplicate.
   */
  ipcMain.handle("app:importPdf", async (_event, source: string, destDir: string) => {
    if (typeof source !== "string" || !source.trim()) throw new Error("no source file given");
    if (typeof destDir !== "string" || !destDir.trim()) throw new Error("no destination folder given");
    if (!existsSync(source)) throw new Error(`file no longer exists: ${source}`);
    if (path.dirname(path.resolve(source)) === path.resolve(destDir)) return source;
    if (!existsSync(destDir)) throw new Error(`folder no longer exists: ${destDir}`);
    const target = uniquePath(destDir, path.basename(source));
    await copyFile(source, target);
    return target;
  });

  /**
   * Copy one file to a location the user picks.
   *
   * The copy happens here rather than by handing the renderer a path, because
   * the renderer is sandboxed and has no filesystem access. Returns the chosen
   * path, or null when the dialog was cancelled — a cancelled save is a normal
   * outcome, not an error.
   */
  ipcMain.handle(
    "dialog:saveAs",
    async (_event, source: string, suggestedName: string, locale?: string) => {
      const strings = DIALOG_STRINGS[resolveLocale(locale)];
      const result = await dialog.showSaveDialog(mainWindow!, {
        title: strings.saveAs,
        defaultPath: path.join(path.dirname(source), suggestedName),
        filters: [{ name: "PDF", extensions: ["pdf"] }],
      });
      if (result.canceled || !result.filePath) return null;
      // Overwrites an existing file, which is safe to do silently: the save
      // dialog has already asked the user to confirm it.
      await copyFile(source, result.filePath);
      return result.filePath;
    },
  );

  ipcMain.handle("shell:reveal", async (_event, target: string) => {
    shell.showItemInFolder(target);
  });

  ipcMain.handle("shell:openPath", async (_event, target: string) => {
    await shell.openPath(target);
  });

  /**
   * Open a documentation or install page in the user's browser.
   *
   * The renderer is sandboxed and its CSP forbids navigation, so links to an
   * agent's own site have to go through this process. Only `https:` is
   * accepted: this handler takes a URL from page code, and handing the OS a
   * `file:` or custom-scheme URL would turn a help link into a way to launch
   * something arbitrary.
   */
  ipcMain.handle("shell:openExternal", async (_event, target: string) => {
    let url: URL;
    try {
      url = new URL(target);
    } catch {
      throw new Error(`not a URL: ${target}`);
    }
    if (url.protocol !== "https:") {
      throw new Error(`refusing to open a non-https URL: ${target}`);
    }
    await shell.openExternal(url.href);
  });
}

// One instance per user. Two copies would share the same config, cache and
// outputs, and two sidecars would race on the same sqlite file. The second
// launch focuses the window that is already open instead.
const singleInstance = app.requestSingleInstanceLock();

if (!singleInstance) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  });

  app.whenReady().then(async () => {
    // Must match `appId` in electron-builder.yml: Windows uses it for taskbar
    // grouping and for the notification identity.
    if (process.platform === "win32") app.setAppUserModelId("com.pdfxlate.app");

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
      // This fires before the renderer can report a locale, so the language
      // can only come from the OS setting Electron exposes.
      const startupStrings = DIALOG_STRINGS[resolveLocale()];
      const pip =
        process.platform === "win32" ? ".venv\\Scripts\\pip" : ".venv/bin/pip";
      dialog.showErrorBox(
        startupStrings.engineErrorTitle,
        `${message}\n\n${startupStrings.engineErrorHint}\n` +
          `cd python && python -m venv .venv && ${pip} install -e .`,
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
}
