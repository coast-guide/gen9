/** Only same-origin, absolute paths are allowed as post-login destinations (no open redirects). */
export function safeReturnTo(value: string | null | undefined, fallback = "/chat"): string {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) return fallback;
  if (value.startsWith("/auth/")) return fallback;
  try {
    const url = new URL(value, "http://gen9.invalid");
    return url.origin === "http://gen9.invalid" ? `${url.pathname}${url.search}${url.hash}` : fallback;
  } catch {
    return fallback;
  }
}
