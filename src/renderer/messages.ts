/**
 * Inline message slots.
 *
 * The provider detail, the two add rows and the agents panel each own one
 * element that shows the last validation result. They all behave the same way:
 * remember a message key (plus parameters and whether it is an error), paint
 * it, and repaint on a locale switch. This factory is that single
 * implementation; `app.ts` only names the element and the translator.
 *
 * The translator is a parameter rather than an import so the slot can be
 * tested without the locale tables or a running UI.
 */

export type Translate = (
  key: string,
  params?: Record<string, string | number>,
) => string;

export interface MessageSlot {
  /** Paint the remembered message, if any, or clear the element. */
  paint(): void;
  /**
   * Remember and paint a message. A null key clears the slot. `error` picks
   * the red variant.
   */
  set(key: string | null, params?: Record<string, string>, error?: boolean): void;
}

interface MessageState {
  key: string;
  params?: Record<string, string>;
  error: boolean;
}

export function messageSlot(id: string, translate: Translate): MessageSlot {
  let state: MessageState | null = null;

  const paint = (): void => {
    const msg = document.getElementById(id);
    if (!msg) return;
    if (!state) {
      msg.textContent = "";
      msg.className = "msg";
      return;
    }
    msg.textContent = translate(state.key, state.params);
    msg.className = state.error ? "msg msg--error" : "msg";
  };

  return {
    paint,
    set(key, params, error = false) {
      state = key ? { key, params, error } : null;
      paint();
    },
  };
}
