const MAX = 60;

/** A chat's name for the browser tab: its title, else its first message, on one line and short
 * enough to read in a tab or the history ("Chat" when it has neither). */
export function chatTitle(text: string | null | undefined): string {
  const line = (text ?? "").replace(/\s+/g, " ").trim();
  if (!line) return "Chat";
  return line.length > MAX ? `${line.slice(0, MAX - 1).trimEnd()}…` : line;
}
