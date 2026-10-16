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
 * 4. Referenced keys exist — every `t("...")` call in the renderer and every
 *    `data-i18n*` attribute in the HTML names a key that is actually defined.
 *
 * The fourth exists because the first three all compare the tables *against
 * each other*: delete a key that is still referenced and they stay perfectly
 * happy while the UI renders the raw key as its text. That happened — a key
 * was removed as "no longer used" while two dropdowns still asked for it, and
 * only a manual grep caught it.
 *
 * Exits 0 with a one-line summary, or 1 with the offending keys.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
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

/**
 * 4. Every key the renderer asks for is defined.
 *
 * Scans the renderer sources for `t("...")` and for the `data-i18n*`
 * attributes, then looks each one up. A key that is referenced but missing
 * renders as its own name, which is visible but easy to miss in review — and
 * the parity checks above cannot see it at all, since they only compare the
 * two tables.
 */
const rendererDir = path.join(root, "src", "renderer");

function walk(dir) {
  return readdirSync(dir).flatMap((entry) => {
    const full = path.join(dir, entry);
    return statSync(full).isDirectory() ? walk(full) : [full];
  });
}

const defined = new Set(Object.keys(tables[reference]));
const referenced = new Map(); // key -> where it was seen

for (const file of walk(rendererDir)) {
  if (!/\.(ts|html)$/.test(file) || file.endsWith("i18n.ts")) continue;
  const text = readFileSync(file, "utf8");
  const where = path.relative(root, file);

  // t("key") / t(`key`) with a literal — a dynamic key cannot be checked, and
  // the codebase deliberately uses a fallback for those.
  for (const m of text.matchAll(/\bt\(\s*["'`]([A-Za-z0-9_.-]+)["'`]/g)) {
    referenced.set(m[1], where);
  }
  // data-i18n / -html / -title / -placeholder / -aria-label.
  for (const m of text.matchAll(/data-i18n(?:-[a-z]+)?="([A-Za-z0-9_.-]+)"/g)) {
    referenced.set(m[1], where);
  }
}

const missing = [...referenced].filter(([key]) => !defined.has(key));
if (missing.length) {
  console.error(`i18n check failed: ${missing.length} referenced key(s) not defined`);
  for (const [key, where] of missing) {
    console.error(`  - "${key}" is used in ${where} but is not in the tables`);
  }
  process.exit(1);
}

const count = Object.keys(tables[reference]).length;
console.log(`i18n check: ${count} keys, ${LOCALES.join("/")} in sync`);
