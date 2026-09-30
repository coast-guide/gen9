/** The longest message a chat takes (gen9-agent's RunIn.message, api/threads.py). */
export const MAX_MESSAGE_CHARS = 8000;

/**
 * What the composer says about a message's length, as GOV.UK's character count does: nothing until
 * 90% of the limit, then how many characters are left, then how many too many. Nothing is cut off:
 * a browser's maxlength would drop the end of a long paste without a word.
 */
export function lengthNote(length: number): { text: string; over: boolean } | null {
  if (length < MAX_MESSAGE_CHARS * 0.9) return null;
  const left = MAX_MESSAGE_CHARS - length;
  const count = (n: number) => `${n.toLocaleString("en")} character${n === 1 ? "" : "s"}`;
  return left >= 0 ? { text: `${count(left)} left`, over: false } : { text: `${count(-left)} too many`, over: true };
}
