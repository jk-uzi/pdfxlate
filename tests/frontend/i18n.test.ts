import { beforeEach, describe, expect, it } from "vitest";

import {
  auditKeys,
  getLocale,
  onLocaleChange,
  setLocale,
  t,
} from "../../src/renderer/i18n.js";

beforeEach(() => {
  setLocale("en", false);
});

describe("translation tables", () => {
  it("covers the same keys in both locales", () => {
    expect(auditKeys()).toEqual([]);
  });
});

describe("t", () => {
  it("interpolates parameters", () => {
    expect(t("mcp.copyFailed", { error: "boom" })).toContain("boom");
  });

  it("falls back to the key when it is missing", () => {
    expect(t("missing.key")).toBe("missing.key");
  });

  it("uses the explicit fallback when given", () => {
    expect(t("missing.key", undefined, "raw")).toBe("raw");
  });
});

describe("locale changes", () => {
  it("notifies listeners and can be unsubscribed", () => {
    const seen: string[] = [];
    const off = onLocaleChange((locale) => seen.push(locale));

    setLocale("zh", false);
    off();
    setLocale("en", false);

    expect(seen).toEqual(["zh"]);
    expect(getLocale()).toBe("en");
  });
});
