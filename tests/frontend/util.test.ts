import { afterEach, describe, expect, it } from "vitest";

import {
  baseName,
  clampSidebarWidth,
  cleanError,
  cssEscape,
  formatContextBadge,
  languageLabel,
  parsePages,
  spreadPages,
  truncateSample,
} from "../../src/renderer/util.js";
import { setLocale } from "../../src/renderer/i18n.js";

const originalWidth = window.innerWidth;

function setWindowWidth(width: number): void {
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    value: width,
  });
}

afterEach(() => {
  setWindowWidth(originalWidth);
  setLocale("en", false);
});

describe("clampSidebarWidth", () => {
  it("keeps widths inside the fixed bounds", () => {
    setWindowWidth(1280);
    expect(clampSidebarWidth(100)).toBe(240);
    expect(clampSidebarWidth(340)).toBe(340);
    expect(clampSidebarWidth(700)).toBe(600);
  });

  it("also caps at half the window on a narrow window", () => {
    setWindowWidth(1000);
    expect(clampSidebarWidth(700)).toBe(500);
    expect(clampSidebarWidth(240)).toBe(240);
  });

  it("rounds to whole pixels", () => {
    setWindowWidth(1280);
    expect(clampSidebarWidth(341.6)).toBe(342);
  });
});

describe("baseName", () => {
  it("handles both separators and bare names", () => {
    expect(baseName("C:\\docs\\paper.pdf")).toBe("paper.pdf");
    expect(baseName("/home/me/paper.pdf")).toBe("paper.pdf");
    expect(baseName("paper.pdf")).toBe("paper.pdf");
  });
});

describe("spreadPages", () => {
  it("pairs a page with the next one", () => {
    expect(spreadPages({ page: 2, pages: 10 })).toEqual([2, 3]);
  });

  it("shows one page at the end of an odd-length document", () => {
    expect(spreadPages({ page: 10, pages: 10 })).toEqual([10]);
    expect(spreadPages({ page: 3, pages: 3 })).toEqual([3]);
  });

  it("keeps the pair when the page count is unknown", () => {
    expect(spreadPages({ page: 1, pages: 0 })).toEqual([1, 2]);
  });
});

describe("parsePages", () => {
  it("returns undefined for an empty spec", () => {
    expect(parsePages("")).toBeUndefined();
    expect(parsePages("   ")).toBeUndefined();
  });

  it("converts ranges and single pages to 0-based indexes", () => {
    expect(parsePages("1-3,5")).toEqual([0, 1, 2, 4]);
    expect(parsePages("2, 4")).toEqual([1, 3]);
    expect(parsePages("4-4")).toEqual([3]);
  });
});

describe("formatContextBadge", () => {
  it("uses K below a million and M above", () => {
    expect(formatContextBadge(999)).toBe("999");
    expect(formatContextBadge(1000)).toBe("1K");
    expect(formatContextBadge(128_000)).toBe("128K");
    expect(formatContextBadge(1_000_000)).toBe("1M");
    expect(formatContextBadge(1_500_000)).toBe("1.5M");
  });
});

describe("cleanError", () => {
  it("keeps only the last concrete error", () => {
    expect(
      cleanError(
        "Error: Error invoking remote method 'sidecar:request': Error: ValueError: bad",
      ),
    ).toBe("ValueError: bad");
  });

  it("keeps an exception type that contains the word Error", () => {
    expect(cleanError("TypeError: boom")).toBe("TypeError: boom");
    expect(cleanError(new Error("boom"))).toBe("boom");
  });

  it("leaves an unprefixed message alone", () => {
    expect(cleanError("plain failure")).toBe("plain failure");
  });
});

describe("truncateSample", () => {
  it("collapses whitespace", () => {
    expect(truncateSample("  hello \n\n world  ")).toBe("hello world");
  });

  it("adds an ellipsis only past the limit", () => {
    const eighty = "x".repeat(80);
    expect(truncateSample(eighty)).toBe(eighty);
    expect(truncateSample(`${eighty}y`)).toBe(`${eighty}…`);
  });
});

describe("cssEscape", () => {
  it("escapes quotes and backslashes", () => {
    expect(cssEscape('a"b\\c')).toBe('a\\"b\\\\c');
    expect(cssEscape("plain")).toBe("plain");
  });
});

describe("languageLabel", () => {
  it("uses the interface language", () => {
    setLocale("en", false);
    expect(languageLabel("en")).toBe("English");
    setLocale("zh", false);
    expect(languageLabel("en")).toBe("英语");
  });

  it("falls back to the code for an unknown tag", () => {
    expect(languageLabel("not a language")).toBe("not a language");
  });
});
