/**
 * Run the icon generator with the project's virtualenv interpreter.
 *
 * `scripts/make_icon.py` needs PyMuPDF, which is installed in `python/.venv`,
 * not necessarily in whatever `python` resolves to on PATH. The venv layout
 * differs by platform (`Scripts/python.exe` on Windows, `bin/python`
 * elsewhere), so the path is resolved here rather than baked into an npm
 * script that would only work on one of them.
 */

import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const candidates =
  process.platform === "win32"
    ? [path.join(root, "python", ".venv", "Scripts", "python.exe")]
    : [path.join(root, "python", ".venv", "bin", "python")];

const interpreter = candidates.find((candidate) => existsSync(candidate));
if (!interpreter) {
  console.error(`error: no virtualenv interpreter found at:\n  ${candidates.join("\n  ")}`);
  console.error("create it with: cd python && python -m venv .venv && .venv/bin/pip install -e .");
  process.exit(1);
}

const result = spawnSync(interpreter, [path.join(root, "scripts", "make_icon.py")], {
  stdio: "inherit",
});
process.exit(result.status ?? 1);
