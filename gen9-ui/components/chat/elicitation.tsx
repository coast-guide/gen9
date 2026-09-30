"use client";

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import type { ElicitationRequest } from "@/lib/agent";
import { addressParts, contentOf, type Field, fieldsOf, isWebAddress } from "@/lib/elicitation";

export type ElicitationAnswer = { action: "accept" | "decline" | "cancel"; content?: Record<string, unknown> };

const connectorOf = (toolName: string) => toolName.split("__")[0];
const toolOf = (toolName: string) => (toolName.split("__")[1] ?? toolName).replaceAll("_", " ");

function FormField({ field, value, set }: { field: Field; value: string | boolean | string[]; set: (v: string | boolean | string[]) => void }) {
  const id = `field-${field.name}`;
  const hint = field.description && <span className="text-muted-foreground">{field.description}</span>;
  if (field.kind === "boolean") {
    return (
      <label className="flex items-center gap-2 text-sm">
        <input id={id} type="checkbox" checked={value === true} onChange={(e) => set(e.target.checked)} className="size-4" />
        <span className="font-medium">{field.label}</span>
        {hint}
      </label>
    );
  }
  if (field.kind === "choices") {
    const chosen = Array.isArray(value) ? value : [];
    return (
      <fieldset className="grid gap-1.5 text-sm">
        <legend className="font-medium">
          {field.label}
          {field.required && <span aria-hidden> *</span>}
        </legend>
        {hint}
        {field.choices.map((c) => (
          <label key={c.value} className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={chosen.includes(c.value)}
              onChange={(e) => set(e.target.checked ? [...chosen, c.value] : chosen.filter((x) => x !== c.value))}
              className="size-4"
            />
            {c.label}
          </label>
        ))}
      </fieldset>
    );
  }
  if (field.kind === "choice") {
    return (
      <label className="grid gap-1.5 text-sm">
        <span className="font-medium">
          {field.label}
          {field.required && <span aria-hidden> *</span>}
        </span>
        {hint}
        <select id={id} value={String(value)} required={field.required} onChange={(e) => set(e.target.value)} className="h-9 rounded-full border bg-background px-3 text-sm pointer-coarse:h-11">
          {!field.required && <option value="">—</option>}
          {field.required && !value && <option value="">Choose…</option>}
          {field.choices.map((c) => (
            <option key={c.value} value={c.value}>
              {c.label}
            </option>
          ))}
        </select>
      </label>
    );
  }
  const type = { email: "email", uri: "url", date: "date", datetime: "datetime-local", number: "number", integer: "number", text: "text" }[field.kind];
  return (
    <label className="grid gap-1.5 text-sm">
      <span className="font-medium">
        {field.label}
        {field.required && <span aria-hidden> *</span>}
      </span>
      {hint}
      <Input
        id={id}
        type={type}
        value={String(value)}
        required={field.required}
        step={field.kind === "integer" ? 1 : field.kind === "number" ? "any" : undefined}
        min={field.kind === "number" || field.kind === "integer" ? field.min : undefined}
        max={field.kind === "number" || field.kind === "integer" ? field.max : undefined}
        minLength={field.kind === "text" ? field.min : undefined}
        maxLength={field.kind === "text" ? field.max : undefined}
        onChange={(e) => set(e.target.value)}
        autoComplete="off"
      />
    </label>
  );
}

/**
 * A connector's server asking the person mid-call (MCP elicitation; docs/design/screens/chat.md). It
 * says which server asks. A form is built from the server's flat schema and can be reviewed before
 * Send; an address is shown in full with its host highlighted, and opens only when the person
 * chooses Open. Decline and Cancel answer every request at once.
 */
export function ElicitationCard({
  request,
  respond,
  takeFocus,
}: {
  request: ElicitationRequest;
  respond: (responses: Record<string, ElicitationAnswer>) => Promise<string | null>;
  takeFocus: boolean;
}) {
  const connector = request.connector ?? connectorOf(request.tool_name);
  const forms = request.requests.flatMap((r) => (r.mode === "form" ? [{ key: r.key, fields: fieldsOf(r.requested_schema, r.order) }] : []));
  const [values, setValues] = useState<Record<string, Record<string, string | boolean | string[]>>>(() =>
    Object.fromEntries(forms.map((f) => [f.key, Object.fromEntries(f.fields.map((x) => [x.name, x.initial]))])),
  );
  const [opened, setOpened] = useState<Record<string, boolean>>({});
  const [sending, setSending] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const card = useRef<HTMLFormElement>(null);

  useEffect(() => {
    if (takeFocus) card.current?.querySelector<HTMLElement>("input, select, button")?.focus();
  }, [takeFocus]);

  async function send(answers: Record<string, ElicitationAnswer>) {
    setSending(true);
    setProblem(null);
    const wrong = await respond(answers);
    if (wrong) {
      setProblem(wrong);
      setSending(false);
    }
  }
  const everyone = (action: "decline" | "cancel") => Object.fromEntries(request.requests.map((r) => [r.key, { action }]));
  const urlsChosen = request.requests.every((r) => r.mode !== "url" || opened[r.key]);

  return (
    <form
      ref={card}
      aria-label={`${connector} asks`}
      className="mb-4 grid gap-4 rounded-2xl border bg-card p-4 shadow-sm sm:p-5"
      onSubmit={(e) => {
        e.preventDefault();
        void send(
          Object.fromEntries(
            request.requests.map((r) => {
              if (r.mode === "url") return [r.key, { action: "accept" }];
              const form = forms.find((f) => f.key === r.key);
              return [r.key, { action: "accept", content: form ? contentOf(form.fields, values[r.key] ?? {}) : {} }];
            }),
          ),
        );
      }}
    >
      <p className="text-sm font-medium">
        {connector} asks <span className="font-normal text-muted-foreground">(while using {toolOf(request.tool_name)})</span>
      </p>
      {request.requests.map((r) =>
        r.mode === "form" ? (
          <fieldset key={r.key} className="grid gap-3">
            <legend className="mb-2 text-sm">{r.message}</legend>
            {(forms.find((f) => f.key === r.key)?.fields ?? []).map((field) => (
              <FormField
                key={field.name}
                field={field}
                value={values[r.key]?.[field.name] ?? field.initial}
                set={(v) => setValues((all) => ({ ...all, [r.key]: { ...all[r.key], [field.name]: v } }))}
              />
            ))}
          </fieldset>
        ) : (
          <div key={r.key} className="grid gap-2 text-sm">
            <p>{r.message}</p>
            <UrlToOpen url={r.url} opened={Boolean(opened[r.key])} open={() => setOpened((o) => ({ ...o, [r.key]: true }))} />
          </div>
        ),
      )}
      {problem && (
        <p role="alert" className="text-sm text-destructive">
          {problem}
        </p>
      )}
      <div className="flex flex-wrap items-center justify-end gap-2">
        <Button type="button" variant="ghost" disabled={sending} onClick={() => void send(everyone("cancel"))}>
          Cancel
        </Button>
        <Button type="button" variant="outline" disabled={sending} onClick={() => void send(everyone("decline"))}>
          Decline
        </Button>
        <Button type="submit" disabled={sending || !urlsChosen}>
          {sending && <Spinner aria-hidden />}
          {request.requests.some((r) => r.mode === "form") ? "Send" : "Done"}
        </Button>
      </div>
    </form>
  );
}

/** An address the server wants opened: in full, its host in bold, opened only by the person. */
function UrlToOpen({ url, opened, open }: { url: string; opened: boolean; open: () => void }) {
  const { before, host, after, punycode } = addressParts(url);
  return (
    <div className="grid gap-2 rounded-xl border px-3 py-2">
      <p className="break-all font-mono text-xs">
        {before}
        <strong className="text-sm text-foreground">{host}</strong>
        {after}
      </p>
      {punycode && <p className="text-xs text-destructive">This address uses characters that can imitate another site’s name. Check it before opening.</p>}
      {!isWebAddress(url) ? (
        // Anything else (javascript:, file:, another app's scheme) would run or launch something
        <p className="text-xs text-destructive">This isn’t a web address, and Gen9 opens only those (http or https). Decline it.</p>
      ) : (
      <div className="flex items-center gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => {
            window.open(url, "_blank", "noopener,noreferrer");
            open();
          }}
        >
          Open {host || "it"}
        </Button>
        {opened && <span className="text-xs text-muted-foreground">Opened in a new tab. Send Done once you’ve finished there.</span>}
      </div>
      )}
    </div>
  );
}
