import { beforeEach, describe, expect, it } from "vitest";

import { messageSlot } from "../../src/renderer/messages.js";

beforeEach(() => {
  document.body.innerHTML = '<div id="slot" class="msg"></div>';
});

describe("messageSlot", () => {
  it("paints the translated message", () => {
    const slot = messageSlot("slot", (key, params) =>
      params ? `${key}:${params.detail}` : key,
    );

    slot.set("model.saveFailed", { detail: "boom" });

    const msg = document.getElementById("slot")!;
    expect(msg.textContent).toBe("model.saveFailed:boom");
    expect(msg.className).toBe("msg");
  });

  it("uses the error variant when asked", () => {
    const slot = messageSlot("slot", (key) => key);

    slot.set("model.saveFailed", undefined, true);

    expect(document.getElementById("slot")!.className).toBe("msg msg--error");
  });

  it("clears the element for a null key", () => {
    const slot = messageSlot("slot", (key) => key);
    slot.set("some.key");

    slot.set(null);

    const msg = document.getElementById("slot")!;
    expect(msg.textContent).toBe("");
    expect(msg.className).toBe("msg");
  });

  it("repaints the remembered message for a new locale", () => {
    let locale = "en";
    const slot = messageSlot("slot", (key) => `${locale}:${key}`);
    slot.set("some.key");

    locale = "zh";
    slot.paint();

    expect(document.getElementById("slot")!.textContent).toBe("zh:some.key");
  });

  it("does nothing when the element is absent", () => {
    document.body.innerHTML = "";
    const slot = messageSlot("slot", (key) => key);

    expect(() => slot.set("some.key")).not.toThrow();
    expect(() => slot.paint()).not.toThrow();
  });
});
