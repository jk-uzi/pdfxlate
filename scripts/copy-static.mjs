/**
 * Copies non-TypeScript renderer assets (HTML, CSS) into the build output.
 * TypeScript is compiled by tsc; these files are passed through verbatim,
 * except that index.html has the app mark inlined into it.
 *
 * The mark is inlined rather than referenced with `<img src="icon.svg">`: the
 * page loads from a `file://` URL, where the origin is opaque, so CSP's
 * `'self'` does not match a relative path and the image is refused outright
 * (the same reason `frame-src file:` is needed for the PDF preview). Inlining
 * is not a subresource fetch, so no CSP directive has to be widened.
 */

import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const src = path.join(root, "src", "renderer");
const dest = path.join(root, "out", "renderer");

/** Verbatim copies. `icon.svg` is handled separately, as an inline. */
const files = ["style.css"];

/**
 * Files that earlier builds copied here but no longer should.
 *
 * `out/` is not cleaned between builds, so a file dropped from this script
 * would linger and keep being served from a stale copy.
 */
const retired = ["icon.svg"];

/** The placeholder in index.html that the mark replaces. */
const MARKER = "<!-- icon-inline -->";

/**
 * Strip the XML declaration and comments from the source SVG.
 *
 * Comments are dropped because the file's own header comment would otherwise
 * land inside the button, and an XML declaration is not valid in HTML.
 */
function inlineSvg(svg) {
  return svg
    .replace(/<\?xml[^?]*\?>/g, "")
    .replace(/<!--[\s\S]*?-->/g, "")
    .trim();
}

/**
 * Reject a stylesheet whose comment delimiters do not pair up.
 *
 * A single missing `/*` turns the rest of a comment into a garbage selector,
 * and the browser then discards whichever rule follows — silently, with no
 * console error, no failed build and no failed test. That happened: the
 * save-a-copy chip lost its positioning and its `opacity: 0` from one dropped
 * character, so it sat in the wrong corner and never hid, and it was only
 * noticed by eye.
 *
 * The check is deliberately about balance rather than full validity: it costs
 * nothing, catches the failure mode that actually bit, and cannot reject valid
 * CSS. `style.css` is the only stylesheet, and it goes through here, so this is
 * the one place that sees every one of them.
 */
function assertCommentsBalanced(css, name) {
  const opens = (css.match(/\/\*/g) || []).length;
  const closes = (css.match(/\*\//g) || []).length;
  if (opens !== closes) {
    throw new Error(
      `${name}: ${opens} "/*" but ${closes} "*/" — a comment is unclosed, which ` +
        `silently discards the rule that follows it`,
    );
  }
  // A line that looks like comment body outside a comment is the same fault.
  // Matched precisely: `* {` is the universal selector and perfectly legal, so
  // a bare "starts with a star" test would fail on it.
  const withoutComments = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const orphan = withoutComments.match(/(^|\n)\s*\*[ \t]+[^{\s,][^\n]*/);
  if (orphan) {
    throw new Error(
      `${name}: a line starts with "*" outside any comment (${JSON.stringify(
        orphan[0].trim().slice(0, 40),
      )}) — missing "/*"?`,
    );
  }
}

await mkdir(dest, { recursive: true });

for (const file of retired) {
  // `force: true` makes this a no-op when the file is already gone.
  await rm(path.join(dest, file), { force: true });
}

for (const file of files) {
  const content = await readFile(path.join(src, file));
  if (file.endsWith(".css")) assertCommentsBalanced(content.toString("utf8"), file);
  await writeFile(path.join(dest, file), content);
  console.log(`copied ${file} -> out/renderer/${file}`);
}

const svg = inlineSvg(await readFile(path.join(src, "icon.svg"), "utf8"));
const html = await readFile(path.join(src, "index.html"), "utf8");

if (!html.includes(MARKER)) {
  // Fail loudly: a silent miss would ship a button with no mark in it.
  throw new Error(`index.html is missing the ${MARKER} placeholder`);
}

// The mark is sized by the attributes added here, so the source's own
// width/height are removed rather than left to collide with them — duplicate
// attributes on one element are a parsing hazard, not a style question.
const sized = svg
  .replace(/<svg\b[^>]*>/, (tag) => {
    const withoutSize = tag.replace(/\s(width|height)="[^"]*"/g, "");
    return withoutSize.replace(
      /<svg\b/,
      '<svg class="titlebar__mark" width="22" height="22" aria-hidden="true" focusable="false"',
    );
  });

await writeFile(path.join(dest, "index.html"), html.replace(MARKER, sized));
console.log("inlined icon.svg -> out/renderer/index.html");
