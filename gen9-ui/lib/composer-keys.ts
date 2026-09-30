/**
 * Whether a keydown in the composer sends the message: Enter without Shift, and not the Enter that
 * confirms an input method's conversion (Japanese, Chinese, Korean). Browsers mark that one
 * `isComposing`, but Safari fired it after `compositionend`, with `isComposing` false and
 * `keyCode` 229 ("processed by the IME", UI Events), until WebKit fixed the order in April 2026
 * (bugs 165004 and 311717): so a 229 counts as composing too.
 */
export function sends(key: { key: string; shiftKey: boolean; isComposing: boolean; keyCode: number }): boolean {
  return key.key === "Enter" && !key.shiftKey && !key.isComposing && key.keyCode !== 229;
}
