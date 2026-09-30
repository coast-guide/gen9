import type { FieldSchema, FormSchema } from "@/lib/agent";

export type Choice = { value: string; label: string };
/** One field of a server's form, as the card renders it. */
export type Field = {
  name: string;
  label: string;
  description?: string;
  kind: "text" | "email" | "uri" | "date" | "datetime" | "number" | "integer" | "boolean" | "choice" | "choices";
  required: boolean;
  choices: Choice[];
  min?: number;
  max?: number;
  initial: string | boolean | string[];
};

type Options = { enum?: string[]; oneOf?: { const: string; title?: string }[]; anyOf?: { const: string; title?: string }[] };

const choicesOf = (schema: Options | undefined): Choice[] => {
  if (!schema) return [];
  if (schema.enum) return schema.enum.map((v) => ({ value: v, label: v }));
  const options = schema.oneOf ?? schema.anyOf ?? [];
  return options.map((o) => ({ value: o.const, label: o.title ?? o.const }));
};

function kindOf(schema: FieldSchema): Field["kind"] {
  if (schema.type === "array") return "choices";
  if (schema.type === "boolean") return "boolean";
  if (schema.type === "integer") return "integer";
  if (schema.type === "number") return "number";
  if (choicesOf(schema).length) return "choice";
  switch (schema.format) {
    case "email":
      return "email";
    case "uri":
      return "uri";
    case "date":
      return "date";
    case "date-time":
      return "datetime";
    default:
      return "text";
  }
}

/**
 * The fields of a server's form, from its flat schema (MCP limits forms to primitive fields), in
 * `order` when given (the server's order, which the stored request's JSON loses).
 */
export function fieldsOf(schema: FormSchema, order?: string[]): Field[] {
  const required = new Set(schema.required ?? []);
  const properties = schema.properties ?? {};
  const names = [...(order ?? []).filter((n) => n in properties), ...Object.keys(properties).filter((n) => !(order ?? []).includes(n))];
  return names.map((name) => [name, properties[name]] as const).map(([name, field]) => {
    const kind = kindOf(field);
    const choices = kind === "choices" ? choicesOf(field.items) : choicesOf(field);
    const initial =
      kind === "boolean" ? field.default === true : kind === "choices" ? (Array.isArray(field.default) ? (field.default as string[]) : []) : field.default == null ? "" : String(field.default);
    return {
      name,
      label: field.title ?? name,
      description: field.description,
      kind,
      required: required.has(name),
      choices,
      min: kind === "choices" ? field.minItems : kind === "text" ? field.minLength : field.minimum,
      max: kind === "choices" ? field.maxItems : kind === "text" ? field.maxLength : field.maximum,
      initial,
    };
  });
}

/** What the person entered, typed as the schema says; empty optional fields are left out. */
export function contentOf(fields: Field[], values: Record<string, string | boolean | string[]>): Record<string, string | number | boolean | string[]> {
  const content: Record<string, string | number | boolean | string[]> = {};
  for (const field of fields) {
    const value = values[field.name];
    if (field.kind === "boolean") content[field.name] = value === true;
    else if (field.kind === "choices") {
      if (Array.isArray(value) && (value.length || field.required)) content[field.name] = value;
    } else if (typeof value === "string" && value.trim() !== "") {
      content[field.name] = field.kind === "number" || field.kind === "integer" ? Number(value) : value.trim();
    }
  }
  return content;
}

/**
 * An address split so its host can stand out (MCP asks clients to show the full URL and highlight
 * its domain), and whether the host is punycode, which can imitate another name.
 */
/** Whether a server's address is a web page (http or https): the only kind the card opens (M9, U1). */
export function isWebAddress(url: string): boolean {
  try {
    return /^https?:$/.test(new URL(url).protocol);
  } catch {
    return false;
  }
}

export function addressParts(url: string): { before: string; host: string; after: string; punycode: boolean } {
  try {
    const parsed = new URL(url);
    const at = url.indexOf(parsed.host);
    if (at < 0) return { before: "", host: parsed.host, after: url, punycode: /(^|\.)xn--/.test(parsed.hostname) };
    return { before: url.slice(0, at), host: parsed.host, after: url.slice(at + parsed.host.length), punycode: /(^|\.)xn--/.test(parsed.hostname) };
  } catch {
    return { before: url, host: "", after: "", punycode: false };
  }
}
