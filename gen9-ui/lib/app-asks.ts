/**
 * How an MCP App's View reaches the person, when it can act on its own at any moment (P5-C1): its
 * asks and its messages must not take a decision the person didn't make.
 */

/**
 * How long a View's ask must have been where it is before its Allow or Open acts: a click or a key
 * meant for what was there a moment before isn't a decision. Chromium's permission prompts do the
 * same, over the double-click interval (ui/views/input_event_activation_protector.h: "inputs too
 * close to when the view/widget was shown").
 */
export const SETTLE_MS = 500;

export const settled = (since: number, now: number) => now - since >= SETTLE_MS;

/** A View's message would replace a draft of the person's: they are asked first. */
export const replacesDraft = (draft: string, text: string) => draft.trim() !== "" && draft !== text;

/** The text of a `ui/message`'s content: its text blocks, one per line. */
export function messageText(content: unknown): string {
  const blocks = Array.isArray(content) ? content : [content];
  return blocks
    .flatMap((b) => (b && typeof b === "object" && b.type === "text" && typeof b.text === "string" ? [b.text] : []))
    .join("\n");
}
