// Why gen9-agent refused an environment secret, as Settings shows it: at the field it is about, in Gen9's words
// (the API's 422 names the field in each error's `loc`; found by hand: docs/plans/manual-e2e.md, P2-K2)

export type SecretField = "name" | "host" | "path" | "header" | "value";
export type SecretRefusal = { field?: SecretField; error: string };

const FIELDS: SecretField[] = ["name", "host", "path", "header", "value"];

/** The first refused field in a FastAPI 422 body, with what to change there. */
export function secretRefusal(body: unknown): SecretRefusal {
  const errors = (body as { detail?: unknown })?.detail;
  const first = Array.isArray(errors) ? (errors as { loc?: unknown[]; msg?: string }[]).find((e) => FIELDS.includes(e.loc?.at(-1) as SecretField)) : undefined;
  const field = first?.loc?.at(-1) as SecretField | undefined;
  const msg = first?.msg ?? "";
  switch (field) {
    case "name":
      return { field, error: "Use lowercase letters, digits and hyphens, up to 32 characters." };
    case "host":
      return /IP address/.test(msg)
        ? { field, error: "Use the host's name, not an IP address." }
        : { field, error: "Use a host name like api.github.com, without https:// or a port." };
    case "path":
      return { field, error: "Start the path with /, like /v1/*." };
    case "header":
      return /name the header/.test(msg)
        ? { field, error: "Name the header it goes in, like X-Api-Key." }
        : { field, error: "Use letters, digits and hyphens, like X-Api-Key." };
    case "value":
      return /Basic/.test(msg) ? { field, error: "Basic takes user:password." } : { field, error: "Enter the value." };
    default:
      return { error: "Gen9 couldn’t take that secret. Check each field and try again." };
  }
}
