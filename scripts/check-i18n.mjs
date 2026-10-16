/**
 * Guards the renderer translation tables against drift.
 *
 * The real `src/renderer/i18n.ts` is transpiled and imported at runtime with
 * the project's own TypeScript compiler, so the checks always run against the
 * shipping tables and need no build step.
 *
 * Three checks, all fatal:
 *
 * 1. Key parity — every key in one locale exists in the other. Delegated to
 *    the module's own `auditKeys()` so the source of truth stays in one place.
 * 2. Placeholder parity — a key's `{name}` placeholders are the same set in
 *    both locales. Catches a translation that silently drops a substitution.
 * 3. Non-empty values — no key maps to an empty or whitespace-only string.
 *
 * Exits 0 with a one-line summary, or 1 with the offending keys.
 */

import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const modulePath = path.join(root, "src", "renderer", "i18n.ts");

const ts = require("typescript");

const source = readFileSync(modulePath, "utf8");
const js = ts.transpileModule(source, {
  fileName: modulePath,
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;

const i18n = await import(
  "data:text/javascript;base64," + Buffer.from(js).toString("base64")
);

const { LOCALES, auditKeys, messagesFor } = i18n;

/** `{name}` placeholders in a message, as a sorted, de-duplicated list. */
function placeholders(text) {
  return [...new Set([...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]))].sort();
}

const problems = [];

// 1. Key parity.
for (const { locale, key } of auditKeys()) {
  const other = LOCALES.find((l) => l !== locale);
  problems.push(`key parity: "${locale}" is missing "${key}" (present in "${other}")`);
}

// 2. Placeholder parity and 3. non-empty values, key by key.
if (LOCALES.length < 2) {
  console.error(`i18n check failed: expected at least two locales, got ${LOCALES.length}`);
  process.exit(1);
}
const tables = Object.fromEntries(LOCALES.map((l) => [l, messagesFor(l)]));
const reference = LOCALES[0];

for (const key of Object.keys(tables[reference])) {
  for (const locale of LOCALES) {
    const text = tables[locale][key];
    if (text === undefined) continue; // already reported by key parity

    if (text.trim() === "") {
      problems.push(`empty value: "${key}" is empty in "${locale}"`);
    }

    const expected = placeholders(tables[reference][key]);
    const actual = placeholders(text);
    const missing = expected.filter((p) => !actual.includes(p));
    const extra = actual.filter((p) => !expected.includes(p));
    if (missing.length || extra.length) {
      const detail = [
        missing.length ? `missing ${missing.map((p) => `{${p}}`).join(", ")}` : "",
        extra.length ? `unexpected ${extra.map((p) => `{${p}}`).join(", ")}` : "",
      ]
        .filter(Boolean)
        .join("; ");
      problems.push(
        `placeholder parity: "${key}" in "${locale}" ${detail} (vs "${reference}")`,
      );
    }
  }
}

if (problems.length) {
  console.error(`i18n check failed: ${problems.length} problem(s)`);
  for (const problem of problems) console.error(`  - ${problem}`);
  process.exit(1);
}

const count = Object.keys(tables[reference]).length;
console.log(`i18n check: ${count} keys, ${LOCALES.join("/")} in sync`);
