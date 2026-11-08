/**
 * Pure renderer helpers.
 *
 * They live outside `app.ts` so tests can import them without running the
 * app's DOM setup. Anything that reads or writes application state belongs in
 * `app.ts`; only self-contained formatting and parsing lives here.
 */

import { getLocale } from "./i18n.js";

const MIN_SIDEBAR = 240;
const MAX_SIDEBAR = 600;

/**
 * Clamp a width to something that leaves the document usable.
 *
 * The upper bound is also a share of the window, since 600px is comfortable at
 * 1280 but would leave almost nothing for the page on a narrow window. The
 * window's minimum width is 1000, so half of it still leaves the preview half.
 */
export function clampSidebarWidth(width: number): number {
  const byWindow = Math.max(MIN_SIDEBAR, Math.floor(window.innerWidth / 2));
  return Math.round(
    Math.min(Math.max(width, MIN_SIDEBAR), Math.min(MAX_SIDEBAR, byWindow)),
  );
}

/** Base name of a path, for display. */
export function baseName(file: string): string {
  return file.split(/[\\/]/).pop() ?? file;
}

/**
 * The pages shown side by side for a given position.
 *
 * Two adjacent pages, except at the end of an odd-length document where there
 * is only one left. Pages are 1-based, matching the preview state.
 */
export function spreadPages(state: { page: number; pages: number }): number[] {
  const first = state.page;
  const second = first + 1;
  if (state.pages > 0 && second > state.pages) return [first];
  return [first, second];
}

/**
 * Parse a page specification like `1-5,8` into 0-based page indexes.
 *
 * Returns undefined when nothing usable was given, which the caller treats as
 * "all pages".
 */
export function parsePages(spec: string): number[] | undefined {
  const trimmed = spec.trim();
  if (!trimmed) return undefined;
  const pages: number[] = [];
  for (const part of trimmed.split(",")) {
    const p = part.trim();
    if (!p) continue;
    if (p.includes("-")) {
      const [a, b] = p.split("-").map((n) => parseInt(n, 10));
      for (let i = a; i <= b; i++) pages.push(i - 1);
    } else {
      pages.push(parseInt(p, 10) - 1);
    }
  }
  return pages.length ? pages : undefined;
}

/** Format a context window as a short badge value: 128K, 1M. */
export function formatContextBadge(tokens: number): string {
  if (tokens >= 1_000_000) {
    const millions = tokens / 1_000_000;
    return `${Number.isInteger(millions) ? millions : millions.toFixed(1)}M`;
  }
  if (tokens >= 1000) return `${Math.round(tokens / 1000)}K`;
  return String(tokens);
}

/**
 * Trim Electron's transport wrapping off an error message.
 *
 * An `ipcRenderer.invoke` rejection arrives as
 * `Error: Error invoking remote method 'sidecar:request': Error: ValueError: …`.
 * Only the last, concrete part is useful to a person looking at a settings
 * panel, and the repeats read like a stack trace.
 */
export function cleanError(err: unknown): string {
  const raw = String(err);
  // Match a wrapper's "Error: " only at the start of a word. A plain
  // `lastIndexOf` would also cut inside "ValueError: ", losing the exception
  // type that makes the message useful.
  let start = -1;
  for (const match of raw.matchAll(/(?:^|[^\w])Error: /g)) {
    start = (match.index ?? 0) + match[0].length;
  }
  return start >= 0 ? raw.slice(start) : raw;
}

/** Trim a model's answer to something that fits in a card. */
export function truncateSample(sample: string): string {
  const text = (sample ?? "").replace(/\s+/g, " ").trim();
  return text.length > 80 ? `${text.slice(0, 80)}…` : text;
}

/**
 * A language's name in the interface language.
 *
 * `Intl.DisplayNames` is used rather than twenty hand-written labels per
 * locale: it is built into the runtime and already knows these names in every
 * UI language, so there is nothing to keep in step. The code is shown as a
 * fallback if it is unavailable or does not recognise one.
 */
export function languageLabel(code: string): string {
  try {
    const names = new Intl.DisplayNames([getLocale() === "zh" ? "zh-CN" : "en"], {
      type: "language",
    });
    return names.of(code) ?? code;
  } catch {
    return code;
  }
}

/** Minimal attribute-selector escape: aliases are user text. */
export function cssEscape(value: string): string {
  return value.replace(/["\\]/g, "\\$&");
}
