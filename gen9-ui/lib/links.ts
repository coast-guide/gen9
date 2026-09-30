// Parameters that only track the click (search tools add `utm_source=openai`, some sites `trk`):
// removed from links in answers, which then open the page itself
const TRACKING = /^(utm_[a-z]+|trk|fbclid|gclid|mc_eid)$/i;

/**
 * The site a link really goes to, when its text doesn't say so: text that names another site
 * ("bbc.co.uk" going to another host), or a host in punycode, which can imitate a name
 * (P5-C9). Null when the text already names it, or isn't a site's name or address.
 */
export function realSite(text: string, href: string | undefined): string | null {
  let host: string;
  try {
    const url = new URL(href ?? "");
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    host = url.hostname.toLowerCase().replace(/^www\./, "");
  } catch {
    return null;
  }
  if (host.split(".").some((label) => label.startsWith("xn--"))) return host;
  const named = text.trim().match(/^(?:https?:\/\/)?(?:www\.)?((?:[a-z0-9-]+\.)+[a-z]{2,24})(?:[/:?#]\S*)?$/i)?.[1]?.toLowerCase();
  if (!named) return null;
  return named === host || host.endsWith(`.${named}`) ? null : host;
}

/** The link without its tracking parameters; anything that isn't an http(s) URL is left alone. */
export function cleanLink(href: string | undefined): string | undefined {
  if (!href) return href;
  let url: URL;
  try {
    url = new URL(href);
  } catch {
    return href;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return href;
  for (const key of [...url.searchParams.keys()]) if (TRACKING.test(key)) url.searchParams.delete(key);
  return url.toString();
}
