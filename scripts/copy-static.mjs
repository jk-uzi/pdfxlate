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

await mkdir(dest, { recursive: true });

for (const file of retired) {
  // `force: true` makes this a no-op when the file is already gone.
  await rm(path.join(dest, file), { force: true });
}

for (const file of files) {
  await writeFile(path.join(dest, file), await readFile(path.join(src, file)));
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
