"use client";

import { useEffect, useId, useState, useTransition } from "react";

import { addEnvironmentSecret, removeEnvironmentSecret } from "@/app/(app)/settings/actions";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import type { EnvironmentSecret } from "@/lib/agent";
import type { SecretField, SecretRefusal } from "@/lib/secret-refusal";

type Auth = EnvironmentSecret["auth"];
const HOW: { value: Auth; label: string }[] = [
  { value: "bearer", label: "Authorization: Bearer" },
  { value: "header", label: "A header of its own" },
  { value: "basic", label: "Basic (user:password)" },
];

const how = (secret: EnvironmentSecret) =>
  secret.auth === "header" ? `as ${secret.header}` : secret.auth === "basic" ? "as Basic" : "as Bearer";

type Methods = EnvironmentSecret["methods"];
// A command in a chat that acts without asking could use it for whatever this allows (P5-C2)
const FOR: { value: Methods; label: string; short: string }[] = [
  { value: "read", label: "Reading only (GET, HEAD, OPTIONS)", short: "reading only" },
  { value: "all", label: "Reading and changing (POST, PUT, PATCH, DELETE too)", short: "reading and changing" },
];

function SecretRow({ secret }: { secret: EnvironmentSecret }) {
  const [problem, setProblem] = useState<string | null>(null);
  const [pending, start] = useTransition();
  const [confirm, setConfirm] = useState(false);
  return (
    <div className="grid gap-3 px-4 py-4 sm:px-5">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
        <div className="min-w-0 flex-1 basis-48">
          <p className="text-sm font-medium">{secret.name}</p>
          <p className="text-sm text-muted-foreground">
            https://{secret.host}
            {secret.path} · {how(secret)} · {FOR.find((f) => f.value === secret.methods)?.short}
          </p>
        </div>
        <Button type="button" variant="ghost" size="sm" disabled={pending} onClick={() => setConfirm(true)} aria-label={`Remove ${secret.name}`}>
          Remove
        </Button>
      </div>
      {problem && (
        <p role="alert" className="text-sm text-destructive">
          {problem}
        </p>
      )}
      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove {secret.name}?</AlertDialogTitle>
            <AlertDialogDescription>Your chats’ environments stop sending it within seconds, and it’s deleted.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() =>
                start(async () => {
                  const result = await removeEnvironmentSecret(secret.id);
                  setProblem(result?.error ?? null);
                })
              }
            >
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

/** One field of the form: its label, then the control, its hint and, when Gen9 refused it, why (GOV.UK's error
 * message pattern: at the field, named by it with aria-describedby, the field aria-invalid). */
function Field({
  id,
  label,
  hint,
  error,
  children,
}: {
  id: string;
  label: React.ReactNode;
  hint?: React.ReactNode;
  error?: string;
  children: (describedBy: string | undefined, invalid: true | undefined) => React.ReactNode;
}) {
  const describedBy = [error && `${id}-error`, hint && `${id}-hint`].filter(Boolean).join(" ") || undefined;
  return (
    <div className="grid gap-1.5 text-sm">
      <label htmlFor={id} className="font-medium">
        {label}
      </label>
      {error && (
        <span id={`${id}-error`} className="font-medium text-destructive">
          {error}
        </span>
      )}
      {children(describedBy, error ? true : undefined)}
      {hint && (
        <span id={`${id}-hint`} className="text-muted-foreground">
          {hint}
        </span>
      )}
    </div>
  );
}

function AddSecret({ onDone }: { onDone: () => void }) {
  const id = useId();
  const [name, setName] = useState("");
  const [host, setHost] = useState("");
  const [path, setPath] = useState("");
  const [auth, setAuth] = useState<Auth>("bearer");
  const [methods, setMethods] = useState<Methods>("read");
  const [header, setHeader] = useState("");
  const [value, setValue] = useState("");
  const [problem, setProblem] = useState<SecretRefusal | null>(null);
  const [pending, start] = useTransition();
  const at = (field: SecretField) => (problem?.field === field ? problem.error : undefined);
  // The refused field gets focus, so its message is read and it can be fixed at once
  useEffect(() => {
    if (problem?.field) document.getElementById(`${id}-${problem.field}`)?.focus();
  }, [problem, id]);
  return (
    <form
      aria-label="Add a secret"
      className="grid gap-3 px-4 py-4 sm:px-5"
      onSubmit={(e) => {
        e.preventDefault();
        setProblem(null);
        start(async () => {
          const result = await addEnvironmentSecret({
            name: name.trim(),
            host: host.trim().toLowerCase(),
            path: path.trim() || undefined,
            auth,
            header: header.trim() || undefined,
            methods,
            value,
          });
          if (result?.error) setProblem(result);
          else onDone();
        });
      }}
    >
      <Field id={`${id}-name`} label="Name" hint="Lowercase letters, digits and hyphens." error={at("name")}>
        {(describedBy, invalid) => (
          <Input
            id={`${id}-name`}
            value={name}
            onChange={(e) => setName(e.target.value.toLowerCase())}
            maxLength={32}
            required
            placeholder="github"
            aria-describedby={describedBy}
            aria-invalid={invalid}
          />
        )}
      </Field>
      <Field
        id={`${id}-host`}
        label="Host"
        hint="Only https requests to this host get it (*.example.com for its subdomains; a name, not an IP address). Your environments may reach it."
        error={at("host")}
      >
        {(describedBy, invalid) => (
          <Input
            id={`${id}-host`}
            value={host}
            onChange={(e) => setHost(e.target.value)}
            required
            placeholder="api.github.com"
            autoComplete="off"
            aria-describedby={describedBy}
            aria-invalid={invalid}
          />
        )}
      </Field>
      <Field
        id={`${id}-path`}
        label={
          <>
            Path <span className="font-normal text-muted-foreground">(optional)</span>
          </>
        }
        error={at("path")}
      >
        {(describedBy, invalid) => (
          <Input
            id={`${id}-path`}
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="/*"
            autoComplete="off"
            aria-describedby={describedBy}
            aria-invalid={invalid}
          />
        )}
      </Field>
      <Field id={`${id}-auth`} label="Sent as">
        {() => (
          <select
            id={`${id}-auth`}
            value={auth}
            onChange={(e) => setAuth(e.target.value as Auth)}
            className="h-9 rounded-full border bg-background px-3 text-sm pointer-coarse:h-11"
          >
            {HOW.map((h) => (
              <option key={h.value} value={h.value}>
                {h.label}
              </option>
            ))}
          </select>
        )}
      </Field>
      <Field
        id={`${id}-methods`}
        label="Sent for"
        hint="Requests of other kinds go to the host without it. In a chat that acts without asking, a command could use it for anything this allows."
      >
        {(describedBy) => (
          <select
            id={`${id}-methods`}
            value={methods}
            onChange={(e) => setMethods(e.target.value as Methods)}
            aria-describedby={describedBy}
            className="h-9 rounded-full border bg-background px-3 text-sm pointer-coarse:h-11"
          >
            {FOR.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
        )}
      </Field>
      {auth === "header" && (
        <Field id={`${id}-header`} label="Header" error={at("header")}>
          {(describedBy, invalid) => (
            <Input
              id={`${id}-header`}
              value={header}
              onChange={(e) => setHeader(e.target.value)}
              required
              placeholder="X-Api-Key"
              autoComplete="off"
              aria-describedby={describedBy}
              aria-invalid={invalid}
            />
          )}
        </Field>
      )}
      <Field
        id={`${id}-value`}
        label="Value"
        hint="Kept encrypted, and not shown again. It’s added to requests on their way out: code in your environments never sees it, though a server that echoes requests back could show it."
        error={at("value")}
      >
        {(describedBy, invalid) => (
          <Input
            id={`${id}-value`}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            type="password"
            required
            autoComplete="off"
            placeholder={auth === "basic" ? "user:password" : ""}
            aria-describedby={describedBy}
            aria-invalid={invalid}
          />
        )}
      </Field>
      <div className="flex flex-wrap items-center justify-end gap-3">
        {problem && !problem.field && (
          <p role="alert" className="mr-auto text-sm text-destructive">
            {problem.error}
          </p>
        )}
        <Button type="button" variant="ghost" disabled={pending} onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending}>
          {pending && <Spinner aria-hidden />}
          Add
        </Button>
      </div>
    </form>
  );
}

/** A person's environment secrets (gen9-agent's environments.py): added to requests from their
 * chats' environments to a host, never inside them. */
export function EnvironmentSecretSettings({ secrets }: { secrets: EnvironmentSecret[] }) {
  const [adding, setAdding] = useState(false);
  return (
    <>
      {secrets.map((secret) => (
        <SecretRow key={secret.id} secret={secret} />
      ))}
      {adding ? (
        <AddSecret onDone={() => setAdding(false)} />
      ) : (
        <div className="px-4 py-3 sm:px-5">
          <Button type="button" variant="ghost" size="sm" onClick={() => setAdding(true)}>
            Add a secret
          </Button>
        </div>
      )}
    </>
  );
}
