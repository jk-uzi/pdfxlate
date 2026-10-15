/**
 * Copies non-TypeScript renderer assets (HTML, CSS) into the build output.
 * TypeScript is compiled by tsc; these files are passed through verbatim.
 */

import { copyFile, mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const src = path.join(root, "src", "renderer");
const dest = path.join(root, "out", "renderer");

const files = ["index.html", "style.css"];

await mkdir(dest, { recursive: true });
for (const file of files) {
  await copyFile(path.join(src, file), path.join(dest, file));
  console.log(`copied ${file} -> out/renderer/${file}`);
}
