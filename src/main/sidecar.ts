/**
 * Manages the Python sidecar process and the newline-delimited JSON protocol.
 *
 * Requests are correlated by id; events (progress, done) are pushed to
 * listeners. The child is killed when the app quits so no orphan Python
 * process survives the window closing.
 */

import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { existsSync } from "node:fs";
import { createInterface, type Interface } from "node:readline";
import path from "node:path";

export interface SidecarError {
  type: string;
  message: string;
  traceback?: string | null;
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
  result: { outputs: Array<{ mono: string; dual: string }> };
}

export interface ReadyEvent {
  event: "ready";
  version: string;
}

export type SidecarEvent = ProgressEvent | DoneEvent | ReadyEvent;

/** Where the engine lives and how to launch it. */
export interface Launch {
  command: string;
  args: string[];
  cwd: string;
}

type Pending = {
  resolve: (value: unknown) => void;
  reject: (err: Error) => void;
};

export class Sidecar {
  private child: ChildProcessWithoutNullStreams | null = null;
  private reader: Interface | null = null;
  private nextId = 1;
  private pending = new Map<number, Pending>();
  private eventListeners = new Set<(event: SidecarEvent) => void>();
  private logListeners = new Set<(line: string) => void>();

  constructor(
    private readonly command: string,
    private readonly args: string[],
    private readonly cwd: string,
  ) {}

  /**
   * Locate the engine, with or without extra leading arguments.
   *
   * `extraArgs` is how the same binary serves two roles: the JSON sidecar the
   * app talks to, and the MCP server an agent talks to (`--mcp`). Sharing this
   * one resolution is deliberate — if the two disagreed about where the engine
   * lives, the config the app told the user to paste would point somewhere the
   * app itself does not use.
   */
  static resolveLaunch(
    repoRoot: string,
    configDir: string,
    isDev: boolean,
    extraArgs: string[] = [],
  ): Launch {
    const args = [...extraArgs, "--config-dir", configDir];

    const bundled = process.platform === "win32" ? "pdfxlate-sidecar.exe" : "pdfxlate-sidecar";
    const bundledPath = path.join(process.resourcesPath ?? "", "sidecar", bundled);
    if (!isDev && existsSync(bundledPath)) {
      return { command: bundledPath, args, cwd: path.dirname(bundledPath) };
    }

    const pythonDir = path.join(repoRoot, "python");
    const venvPython =
      process.platform === "win32"
        ? path.join(pythonDir, ".venv", "Scripts", "python.exe")
        : path.join(pythonDir, ".venv", "bin", "python");

    if (existsSync(venvPython)) {
      return {
        command: venvPython,
        args: ["-m", "pdfxlate.sidecar", ...args],
        cwd: pythonDir,
      };
    }

    // Neither location exists. Report both so the cause is obvious rather than
    // surfacing as a bare spawn ENOENT.
    throw new Error(
      "Translation engine not found. Looked for:\n" +
        `  1. bundled binary: ${bundledPath}${existsSync(bundledPath) ? "" : "  (missing)"}\n` +
        `  2. dev virtualenv: ${venvPython}  (missing)\n\n` +
        "For a source checkout run:\n" +
        "  cd python && python -m venv .venv && .venv/bin/pip install -e .\n" +
        "For a packaged build, run: npm run build && ./scripts/build.sh",
    );
  }

  /**
   * Locate the sidecar executable.
   *
   * Packaged builds ship a PyInstaller onedir binary under resources/sidecar;
   * development runs the module from the local virtualenv.
   */
  static resolve(repoRoot: string, configDir: string, isDev: boolean): Sidecar {
    const launch = Sidecar.resolveLaunch(repoRoot, configDir, isDev);
    return new Sidecar(launch.command, launch.args, launch.cwd);
  }

  start(): Promise<ReadyEvent> {
    return new Promise((resolve, reject) => {
      const child = spawn(this.command, this.args, {
        cwd: this.cwd,
        stdio: ["pipe", "pipe", "pipe"],
        env: { ...process.env, PYTHONUNBUFFERED: "1", PYTHONIOENCODING: "utf-8" },
      });
      this.child = child;

      let settled = false;
      child.on("error", (err) => {
        if (!settled) {
          settled = true;
          reject(err);
        }
      });
      child.on("exit", (code) => {
        const err = new Error(`sidecar exited with code ${code}`);
        for (const [, p] of this.pending) p.reject(err);
        this.pending.clear();
        if (!settled) {
          settled = true;
          reject(err);
        }
      });

      this.reader = createInterface({ input: child.stdout });
      this.reader.on("line", (line) => this.handleLine(line));

      // stderr carries logs; forward them so they are visible in the terminal.
      const errReader = createInterface({ input: child.stderr });
      errReader.on("line", (line) => {
        for (const listener of this.logListeners) listener(line);
      });

      this.onEvent((event) => {
        if (event.event === "ready" && !settled) {
          settled = true;
          resolve(event);
        }
      });
    });
  }

  private handleLine(line: string): void {
    const trimmed = line.trim();
    if (!trimmed) return;
    let message: Record<string, unknown>;
    try {
      message = JSON.parse(trimmed);
    } catch {
      // A stray non-JSON line on stdout is a bug worth surfacing.
      for (const listener of this.logListeners) listener(`[sidecar stdout] ${trimmed}`);
      return;
    }

    if ("id" in message && typeof message.id === "number") {
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      if (message.ok) {
        pending.resolve(message.result);
      } else {
        const error = message.error as SidecarError;
        pending.reject(new Error(error?.message ?? "sidecar request failed"));
      }
      return;
    }

    if ("event" in message) {
      const event = message as unknown as SidecarEvent;
      for (const listener of this.eventListeners) listener(event);
    }
  }

  /** Send a request and await its correlated response. */
  request<T = unknown>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    if (!this.child || !this.child.stdin.writable) {
      return Promise.reject(new Error("sidecar is not running"));
    }
    const id = this.nextId++;
    const payload = JSON.stringify({ id, method, params });
    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, { resolve: resolve as (v: unknown) => void, reject });
      this.child!.stdin.write(payload + "\n");
    });
  }

  onEvent(listener: (event: SidecarEvent) => void): () => void {
    this.eventListeners.add(listener);
    return () => this.eventListeners.delete(listener);
  }

  onLog(listener: (line: string) => void): () => void {
    this.logListeners.add(listener);
    return () => this.logListeners.delete(listener);
  }

  stop(): void {
    if (this.child) {
      this.child.kill();
      this.child = null;
    }
    this.reader?.close();
    this.reader = null;
  }
}
