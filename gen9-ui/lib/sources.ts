import type { Message, Source } from "@/lib/agent";
import { cleanLink } from "@/lib/links";

/** `site` names it in a button; `place` (site and path) tells two pages with one title apart. */
/** `found`: false for a page the answer links that none of the turn's searches or pages found,
 * and the provider didn't cite (P5-C9) */
export type AnswerSource = { url: string; title: string | null; site: string; place: string; found?: boolean };
export type AnswerSources = { cited: AnswerSource[]; consulted: AnswerSource[] };

const LINK = /\]\((https?:\/\/[^\s)]+)\)/g;

function site(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function place(url: string): string {
  try {
    const { pathname } = new URL(url);
    return site(url) + (pathname === "/" ? "" : pathname.replace(/\/$/, ""));
  } catch {
    return url;
  }
}

/** The tools that reach the web: a turn with none of them didn't look anything up */
const WEB_TOOLS = new Set(["web_search", "web_open"]);

/**
 * Where a turn's answer came from (docs/design/screens/chat.md, "Sources"): the pages it links or
 * cites first, then the pages its steps opened, then those its searches consulted. Each page
 * once, without tracking parameters. Only for a turn that searched or opened pages (or got the
 * provider's own web citations): a link the answer merely repeats, from the question or from
 * memory, isn't a source.
 */
export function answerSources(turn: Message[]): AnswerSources {
  const steps = turn.flatMap((m) => m.steps ?? []);
  const usedTheWeb =
    turn.some((m) => (m.citations?.length ?? 0) > 0) ||
    steps.some((step) => WEB_TOOLS.has(step.name) || (step.sources?.length ?? 0) > 0);
  if (!usedTheWeb) return { cited: [], consulted: [] };
  const seen = new Map<string, AnswerSource>();
  const add = (list: AnswerSource[], source: Source) => {
    const url = cleanLink(source.url);
    if (!url || !/^https?:\/\//.test(url)) return;
    const known = seen.get(url);
    if (known) {
      known.title ??= source.title ?? null;
      return;
    }
    const entry = { url, title: source.title ?? null, site: site(url), place: place(url) };
    seen.set(url, entry);
    list.push(entry);
  };
  const cited: AnswerSource[] = [];
  const consulted: AnswerSource[] = [];
  for (const message of turn) {
    for (const citation of message.citations ?? []) add(cited, citation);
    for (const match of message.content.matchAll(LINK)) add(cited, { url: match[1] });
  }
  for (const step of steps) {
    const url = (step.args as { url?: unknown } | null)?.url;
    if (step.name === "web_open" && typeof url === "string") add(consulted, { url });
  }
  for (const step of steps) for (const source of step.sources ?? []) add(consulted, source);
  // The pages the turn's tools found or opened, and the provider's citations, by site and path
  const found = new Set<string>();
  for (const message of turn) for (const c of message.citations ?? []) found.add(place(cleanLink(c.url) ?? c.url));
  for (const step of steps) {
    const url = (step.args as { url?: unknown } | null)?.url;
    if (step.name === "web_open" && typeof url === "string") found.add(place(url));
    for (const source of step.sources ?? []) found.add(place(cleanLink(source.url) ?? source.url));
  }
  for (const source of cited) source.found = found.has(source.place);
  return { cited, consulted };
}

/** The answers of the turn that ends at `messages[end]`: back to the question before it. */
export function turnOf(messages: Message[], end: number): Message[] {
  let start = end;
  while (start > 0 && messages[start - 1].role === "assistant") start--;
  return messages.slice(start, end + 1);
}
