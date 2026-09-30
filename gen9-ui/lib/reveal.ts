/**
 * Characters that change how text reads, or don't show at all, made visible where the person
 * decides on it (an approval: docs/plans/manual-e2e.md, P5-C9). A command holding a right-to-left
 * override reads in another order than it runs ("Trojan Source", CVE-2021-42574), and a
 * zero-width space hides between two words. Unicode's UTS #55 asks for directional formatting
 * characters to be shown prominently and invisible ones to be made visible.
 *
 * Hidden here: control characters but tab and newline (Cc), format characters (Cf: bidirectional
 * embeddings, overrides, isolates and marks, zero-width characters, the byte order mark, tag
 * characters), and the line and paragraph separators (Zl, Zp).
 */
const HIDDEN = /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/u;
const HIDDEN_ALL = /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/gu;

export type Piece = { text: string } | { hidden: string; code: string };

const shown = (ch: string) => ch === "\n" || ch === "\t";

/** "U+202E", for a hidden character. */
export const codeOf = (ch: string) => `U+${ch.codePointAt(0)!.toString(16).toUpperCase().padStart(4, "0")}`;

/** Whether `text` has any character that would be hidden. */
export function hasHidden(text: string): boolean {
  for (const ch of text.match(HIDDEN_ALL) ?? []) if (!shown(ch)) return true;
  return false;
}

/** `text` as visible runs and hidden characters, in order. */
export function reveal(text: string): Piece[] {
  const pieces: Piece[] = [];
  let run = "";
  for (const ch of text) {
    if (HIDDEN.test(ch) && !shown(ch)) {
      if (run) pieces.push({ text: run });
      run = "";
      pieces.push({ hidden: ch, code: codeOf(ch) });
    } else run += ch;
  }
  if (run) pieces.push({ text: run });
  return pieces;
}
