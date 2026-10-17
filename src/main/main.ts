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
    selectOutput: string;
    pickFolder: string;
    saveAs: string;
    engineErrorTitle: string;
    engineErrorHint: string;
  }
> = {
  en: {
    selectPdf: "Select PDF files",
    selectOutput: "Select output directory",
    pickFolder: "Choose a folder of PDFs",
    saveAs: "Save a copy as",
    engineErrorTitle: "Could not start the translation engine",
    engineErrorHint: "If running from source, make sure the Python environment is set up:",
  },
  zh: {
    selectPdf: "选择 PDF 文件",
    selectOutput: "选择输出目录",
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
    // A message hook outlives the window object otherwise, and the callback
    // would keep firing into a destroyed webContents.
    mainWindow?.unhookAllWindowMessages();
    mainWindow = null;
  });

  void mainWindow.loadFile(path.join(currentDir, "..", "renderer", "index.html"));

  registerWheelForwarding(mainWindow);

  if (isDev) {
    mainWindow.webContents.openDevTools({ mode: "detach" });
    registerDevShortcuts(mainWindow);
  }
}

/** `WM_MOUSEWHEEL`, from the Win32 window-message constants. */
const WM_MOUSEWHEEL = 0x020a;

/**
 * Tell the renderer that a wheel event happened anywhere in the window.
 *
 * Scrolling the preview cannot be noticed from the renderer: the PDF viewer is
 * a separate process, and neither its wheel events nor its scroll position reach
 * the page (its `postMessage` interface is not exposed in this build either).
 * The renderer therefore combines a bare "a wheel happened" signal with its own
 * knowledge of whether the pointer is over the preview — it does receive
 * `mouseenter` / `mouseleave` for the iframe element — and reveals the overlay
 * controls only when both hold. The signal carries no position or delta, so the
 * split between "where" and "what" still lives in the renderer.
 *
 * Three sources, and the PDF area needs the one that is easy to miss:
 *
 * - **The viewer's own `webContents`** (see `attachViewerWheelForwarding`) is
 *   the only reliable one over a PDF. The viewer is not an ordinary iframe: it
 *   is Chromium's built-in PDF extension, running as a `remote` WebContents
 *   with its own window. Measured over three consecutive gestures, the viewer
 *   received every wheel while the top-level contents received none. This is
 *   also the only source that works on every platform.
 * - **`input-event` on the window** covers wheels over the ordinary DOM —
 *   sidebar, title bar, and the empty preview pane.
 * - **`WM_MOUSEWHEEL` (Windows)** is a secondary net for the same DOM areas.
 *   It sees the message when the top-level window still holds focus, which is
 *   why it looked sufficient at first: the *first* scroll after opening a
 *   document works, and every one after that goes to the viewer instead.
 *
 * Several sources firing for one gesture only restarts the renderer's 1.6 s
 * hide timer, which is invisible.
 *
 * Twice this was "verified" with synthetic input and still did not work. Both
 * mistakes are worth remembering: `sendInputEvent` on the window injects past
 * the real routing (so it always produces `input-event`), and a single gesture
 * from a clean state cannot reveal a focus-dependent regression. Test with the
 * viewer's contents, repeatedly.
 */
function registerWheelForwarding(window: BrowserWindow): void {
  window.webContents.on("input-event", (_event, input) => {
    if (input.type === "mouseWheel") window.webContents.send("viewer:wheel");
  });
  if (process.platform === "win32") {
    window.hookWindowMessage(WM_MOUSEWHEEL, () => {
      window.webContents.send("viewer:wheel");
    });
  }
}

/**
 * Forward wheels from the PDF viewer, which is a WebContents of its own.
 *
 * Chromium implements the built-in PDF viewer as an extension page loaded into a
 * `remote` WebContents. The OS routes a wheel over the document to *that*
 * window, not to the app's, so neither `input-event` on the window nor
 * `WM_MOUSEWHEEL` sees more than the first gesture — after that the viewer has
 * focus and keeps the rest. Its own `input-event` does fire for every one.
 *
 * `getType()` is the whole test. Filtering on `hostWebContents` would be more
 * precise, but that property is documented as "might own" and can be absent for
 * these guests, and an absent value here means silently forwarding nothing.
 * This app has one window and one viewer, so the type is unambiguous.
 *
 * Registered once for the app rather than per window: the guest is created (and
 * recreated) by the viewer itself, and `web-contents-created` is the only hook
 * that sees each new one. Forwarding is dropped if the window is gone, which is
 * also how a torn-down viewer stops mattering.
 */
function attachViewerWheelForwarding(): void {
  app.on("web-contents-created", (_event, contents) => {
    if (contents.getType() !== "remote") return;
    contents.on("input-event", (_e, input) => {
      if (input.type !== "mouseWheel") return;
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send("viewer:wheel");
      }
    });
  });
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
 * Where translated PDFs go when the user has not chosen a directory.
 *
 * Resolved here rather than in Python because the OS may have relocated the
 * Documents folder (Windows lets it point at another drive entirely), and
 * `app.getPath("documents")` is the only thing that knows the real location.
 * A Python-side `~/Documents` guess would silently write somewhere else.
 *
 * This exists to fix a real bug: with the field left blank, the engine wrote
 * its output into its own working directory — the source tree in development,
 * and the application install directory once packaged, where the write can
 * simply fail.
 */
function defaultOutputDir(): string {
  return path.join(app.getPath("documents"), "pdfxlate");
}

/**
 * Fill in the output directory for `translate.start` when the renderer did not.
 *
 * Done at the application boundary so every caller gets it: the renderer shows
 * this path in the UI, but a stale or omitted value must still not fall through
 * to the engine's working directory.
 */
function withDefaultOutput(params: Record<string, unknown>): Record<string, unknown> {
  const chosen = params.output;
  if (typeof chosen === "string" && chosen.trim()) return params;
  return { ...params, output: defaultOutputDir() };
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
    const payload = (params ?? {}) as Record<string, unknown>;
    const resolved = method === "translate.start" ? withDefaultOutput(payload) : payload;
    return sidecar.request(method, resolved);
  });

  // Lets a late-loading renderer learn the current engine state instead of
  // depending on having caught the one-shot ready event.
  ipcMain.handle("sidecar:state", async () => engineState);

  /** The directory shown as the default in the output field. */
  ipcMain.handle("app:defaultOutputDir", async () => defaultOutputDir());

  /**
   * PDF files directly inside a folder, for the sidebar's file list.
   *
   * Non-recursive on purpose: the list is a flat set of things you might open,
   * and walking subfolders would make it unbounded. A missing folder is an
   * ordinary outcome (it was deleted since it was remembered), so it is reported
   * as a message the UI can show rather than as an opaque throw.
   */
  ipcMain.handle("app:listPdfs", async (_event, dir: string) => {
    if (typeof dir !== "string" || !dir.trim()) throw new Error("no folder given");
    if (!existsSync(dir)) throw new Error(`folder no longer exists: ${dir}`);
    const entries = await readdir(dir, { withFileTypes: true });
    return entries
      .filter((e) => e.isFile() && e.name.toLowerCase().endsWith(".pdf"))
      .map((e) => path.join(dir, e.name))
      .sort((a, b) => a.localeCompare(b));
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

  ipcMain.handle("dialog:pickOutputDir", async (_event, locale?: string) => {
    const result = await dialog.showOpenDialog(mainWindow!, {
      title: DIALOG_STRINGS[resolveLocale(locale)].selectOutput,
      properties: ["openDirectory", "createDirectory"],
    });
    return result.canceled ? null : result.filePaths[0];
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
}

app.whenReady().then(async () => {
  // The app supplies its own title bar, and the default menu's items (File,
  // Edit, View, …) have no counterpart in this UI. Removing it also removes
  // its accelerators; registerDevShortcuts puts the dev ones back.
  Menu.setApplicationMenu(null);

  // Before the window: the PDF viewer's WebContents can be created as soon as a
  // document loads, and `web-contents-created` is the only event that sees each
  // one. Missing the first would leave the very first document without wheel
  // forwarding until it was reopened.
  attachViewerWheelForwarding();

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
