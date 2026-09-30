/**
 * Content-Security-Policy violation reports, in either format browsers send (MDN, "report-to"):
 * the Reporting API's `application/reports+json` (a list of `{type: "csp-violation", body}`) and
 * `report-uri`'s older `application/csp-report` (`{"csp-report": {...}}`). Each becomes one log line
 * naming the directive, what it blocked and on which page, by origin and path only: an address's
 * query can carry a person's data, and the report comes from their browser.
 */

export const MAX_REPORT_BYTES = 64 * 1024;

type Fields = { directive: string; blocked: string; page: string };

const text = (value: unknown) => (typeof value === "string" ? value : "");
// Anyone may post here (browsers send reports without cookies), so what reaches the log is only
// what a report can honestly hold: a directive's name, and no control characters that would start
// a line of its own (M9, U2)
const directive = (value: string) => (/^[a-z][a-z-]{0,39}$/.test(value) ? value : "");
const oneLine = (value: string) => value.replace(/[\u0000-\u001f\u007f-\u009f]/g, "?");

/** An address as origin and path, or the keyword a browser reports (inline, eval, data). */
function where(value: string): string {
  if (!value) return "(none)";
  try {
    const url = new URL(value);
    if (url.protocol === "data:" || url.protocol === "blob:") return url.protocol.slice(0, -1);
    return `${url.origin}${url.pathname}`;
  } catch {
    // "inline", "eval", "wasm-eval", a bare scheme
    return oneLine(value.slice(0, 40));
  }
}

function page(value: string): string {
  try {
    return new URL(value).pathname;
  } catch {
    return "(unknown page)";
  }
}

/** The violations a report body holds, whatever its format; none for anything else. */
export function violations(contentType: string, body: unknown): Fields[] {
  if (contentType.includes("application/reports+json") && Array.isArray(body)) {
    return body
      .filter((report) => report && typeof report === "object" && (report as { type?: unknown }).type === "csp-violation")
      .map((report) => {
        const b = ((report as { body?: unknown }).body ?? {}) as Record<string, unknown>;
        return { directive: directive(text(b.effectiveDirective)), blocked: where(text(b.blockedURL)), page: page(text(b.documentURL)) };
      });
  }
  const legacy = (body as { "csp-report"?: unknown } | null)?.["csp-report"];
  if (legacy && typeof legacy === "object") {
    const b = legacy as Record<string, unknown>;
    return [
      {
        directive: directive(text(b["effective-directive"]) || text(b["violated-directive"]).split(" ")[0]),
        blocked: where(text(b["blocked-uri"])),
        page: page(text(b["document-uri"])),
      },
    ];
  }
  return [];
}

/** One line per violation, as the server logs it. */
export function line(v: Fields): string {
  return `[csp] ${v.directive || "(directive?)"} blocked ${v.blocked} on ${v.page}`;
}
