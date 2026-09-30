"use client";

import { useState, useTransition } from "react";

import { addConnector, keepConnectorTools, removeConnector, searchDirectory, setConnectorPolicy, signInConnector } from "@/app/(app)/settings/actions";
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
import type { Connector, ConnectorPolicy, DirectoryEntry } from "@/lib/agent";
import { connectorName } from "@/lib/directory";

const POLICIES: { value: ConnectorPolicy; label: string }[] = [
  { value: "ask", label: "Ask every time" },
  { value: "changes", label: "Ask before changes" },
  { value: "never", label: "Don’t ask" },
];

const host = (url: string) => {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
};

function ConnectorRow({ connector }: { connector: Connector }) {
  const [problem, setProblem] = useState<string | null>(null);
  const [pending, start] = useTransition();
  const [confirm, setConfirm] = useState(false);
  const count = connector.tools.length;
  const needsSignIn = connector.status !== "ready";
  const signIn = () =>
    start(async () => {
      const result = await signInConnector(connector.id);
      if (result && "authorizeUrl" in result) window.location.assign(result.authorizeUrl);
      else setProblem(result?.error ?? null);
    });
  return (
    <div className="grid gap-3 px-4 py-4 sm:px-5">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
        <div className="min-w-0 flex-1 basis-48">
          <p className="text-sm font-medium">
            {connector.name}
            {connector.plugin && <span className="font-normal text-muted-foreground"> · From {connector.plugin}</span>}
          </p>
          {needsSignIn && (
            <p className="text-sm text-muted-foreground">
              {connector.status === "reconnect"
                ? "Its sign-in has lapsed. Sign in again to use it."
                : `Sign in at ${host(connector.url)} to use it.`}
            </p>
          )}
          {connector.changed.length > 0 && <ChangedTools connector={connector} pending={pending} start={start} onProblem={setProblem} />}
          <details className="mt-0.5 text-sm text-muted-foreground">
            <summary className="cursor-pointer select-none hover:text-foreground">
              {host(connector.url)} · {count} {count === 1 ? "tool" : "tools"}
            </summary>
            <ul className="mt-2 grid gap-1">
              {connector.tools.map((tool) => (
                <li key={tool.name}>
                  <span className="font-medium text-foreground">{tool.name.replaceAll("_", " ")}</span>
                  {tool.read_only && <span> · reads only</span>}
                  {tool.description && <span className="block">{tool.description}</span>}
                </li>
              ))}
            </ul>
          </details>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {needsSignIn && (
            <Button type="button" size="sm" disabled={pending} onClick={signIn}>
              {pending && <Spinner aria-hidden />}
              {connector.status === "reconnect" ? "Reconnect" : "Sign in"}
            </Button>
          )}
          <select
            aria-label={`When Gen9 asks before using ${connector.name}`}
            value={connector.policy}
            disabled={pending}
            onChange={(e) =>
              start(async () => {
                const result = await setConnectorPolicy(connector.id, e.target.value);
                setProblem(result?.error ?? null);
              })
            }
            className="h-9 rounded-full border bg-background px-3 text-sm pointer-coarse:h-11"
          >
            {POLICIES.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
          {/* A plugin's connector goes with the plugin (Settings > Plugins) */}
          {!connector.plugin && (
            <Button type="button" variant="ghost" size="sm" disabled={pending} onClick={() => setConfirm(true)}>
              Remove
            </Button>
          )}
        </div>
      </div>
      {problem && (
        <p role="alert" className="text-sm text-destructive">
          {problem}
        </p>
      )}
      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove {connector.name}?</AlertDialogTitle>
            <AlertDialogDescription>Gen9 stops using its tools, and its token is deleted.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() =>
                start(async () => {
                  const result = await removeConnector(connector.id);
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

/**
 * Tools the server added or changed since the person kept them (P5-C4): Gen9 holds them back from
 * the agent until the person looks, as a server changing what a tool says could make Gen9 do
 * something else (a "rug pull"). What each says now and said before, and a way to use them.
 */
function ChangedTools({
  connector,
  pending,
  start,
  onProblem,
}: {
  connector: Connector;
  pending: boolean;
  start: (action: () => Promise<void>) => void;
  onProblem: (problem: string | null) => void;
}) {
  const n = connector.changed.length;
  return (
    <div className="mt-2 grid gap-2 rounded-xl border p-3 text-sm" role="group" aria-label={`${connector.name}’s changed tools`}>
      <p>
        <span className="font-medium">
          {n === 1 ? "A tool" : `${n} tools`} changed since you connected it.
        </span>{" "}
        Gen9 won’t use {n === 1 ? "it" : "them"} until you look. If you don’t recognise a change, remove the connector.
      </p>
      <ul className="grid gap-2">
        {connector.changed.map((tool) => (
          <li key={tool.name}>
            <span className="font-medium">{tool.name.replaceAll("_", " ")}</span>
            <span className="text-muted-foreground"> · {tool.was === null ? "new" : "changed"}</span>
            <span className="block">Now: {tool.description || "(no description)"}</span>
            {tool.was !== null && <span className="block text-muted-foreground">Before: {tool.was || "(no description)"}</span>}
          </li>
        ))}
      </ul>
      <div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={pending}
          onClick={() =>
            start(async () => {
              const result = await keepConnectorTools(
                connector.id,
                connector.changed.map((t) => t.pin),
              );
              onProblem(result?.error ?? null);
            })
          }
        >
          {n === 1 ? "Use it as it is now" : "Use them as they are now"}
        </Button>
      </div>
    </div>
  );
}

/** What "Add" from the directory fills in. */
type FromDirectory = { entry: DirectoryEntry };

function AddConnector({ onDone, from }: { onDone: () => void; from?: FromDirectory }) {
  const [name, setName] = useState(from ? connectorName(from.entry) : "");
  const [url, setUrl] = useState(from?.entry.url ?? "");
  const [token, setToken] = useState("");
  const header = from?.entry.header ?? undefined;
  const [problem, setProblem] = useState<string | null>(null);
  const [pending, start] = useTransition();
  return (
    <form
      aria-label="Add a connector"
      className="grid gap-3 px-4 py-4 sm:px-5"
      onSubmit={(e) => {
        e.preventDefault();
        setProblem(null);
        start(async () => {
          const result = await addConnector({ name: name.trim(), url: url.trim(), token: token.trim() || undefined, header });
          // The server needs sign-in: over to it, and back to Settings when done
          if (result && "authorizeUrl" in result) window.location.assign(result.authorizeUrl);
          else if (result?.error) setProblem(result.error);
          else onDone();
        });
      }}
    >
      {from && (
        <p className="text-sm text-muted-foreground">
          From the MCP Registry: {from.entry.title ?? from.entry.name}. Not reviewed by Gen9: add it only if you trust who runs it.
        </p>
      )}
      <label className="grid gap-1.5 text-sm">
        <span className="font-medium">Name</span>
        <Input value={name} onChange={(e) => setName(e.target.value.toLowerCase())} maxLength={32} required placeholder="deepwiki" />
        <span className="text-muted-foreground">Lowercase letters, digits and hyphens.</span>
      </label>
      <label className="grid gap-1.5 text-sm">
        <span className="font-medium">Server URL</span>
        <Input value={url} onChange={(e) => setUrl(e.target.value)} type="url" required placeholder="https://mcp.example.com/mcp" />
      </label>
      <label className="grid gap-1.5 text-sm">
        <span className="font-medium">
          {header ? (
            <>
              {from?.entry.header_description ?? "Token"} <span className="font-normal text-muted-foreground">(sent as {header})</span>
            </>
          ) : (
            <>
              Token <span className="font-normal text-muted-foreground">(optional)</span>
            </>
          )}
        </span>
        <Input value={token} onChange={(e) => setToken(e.target.value)} type="password" autoComplete="off" required={Boolean(header)} />
        <span className="text-muted-foreground">
          Sent with each call, and kept encrypted. It isn’t shown again. A server that needs you to sign in sends you there instead.
        </span>
      </label>
      <div className="flex flex-wrap items-center justify-end gap-3">
        {problem && (
          <p role="alert" className="mr-auto text-sm text-destructive">
            {problem}
          </p>
        )}
        <Button type="button" variant="ghost" disabled={pending} onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending}>
          {pending && <Spinner aria-hidden />}
          {pending ? "Connecting" : "Add"}
        </Button>
      </div>
    </form>
  );
}

/** Search Gen9's copy of the MCP Registry; "Add" fills in the add form. */
function Directory({ onAdd, onClose }: { onAdd: (from: FromDirectory) => void; onClose: () => void }) {
  const [words, setWords] = useState("");
  const [found, setFound] = useState<DirectoryEntry[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [pending, start] = useTransition();
  const search = () =>
    start(async () => {
      const result = await searchDirectory(words.trim());
      if ("error" in result) setProblem(result.error);
      else {
        setProblem(null);
        setFound(result);
      }
    });
  return (
    <section aria-label="Connector directory" className="grid gap-3 px-4 py-4 sm:px-5">
      <form
        role="search"
        className="flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          search();
        }}
      >
        <label className="sr-only" htmlFor="directory-search">
          Search the MCP Registry
        </label>
        <Input id="directory-search" value={words} onChange={(e) => setWords(e.target.value)} placeholder="Search the MCP Registry" className="min-w-0 flex-1 basis-48" />
        <Button type="submit" size="sm" disabled={pending}>
          {pending && <Spinner aria-hidden />}
          Search
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={onClose}>
          Close
        </Button>
      </form>
      <p className="text-sm text-muted-foreground">
        Remote servers listed in the MCP Registry, as their publishers describe them. Gen9 hasn’t reviewed them.
      </p>
      {problem && (
        <p role="alert" className="text-sm text-destructive">
          {problem}
        </p>
      )}
      {found && (
        <ul aria-label="Directory results" className="grid gap-2">
          {found.length === 0 && <li className="text-sm text-muted-foreground">Nothing matches. Try other words.</li>}
          {found.map((entry) => (
            <li key={entry.name} className="flex flex-wrap items-start gap-x-4 gap-y-2 rounded-2xl border px-3 py-3">
              <div className="min-w-0 flex-1 basis-48 text-sm">
                <p className="font-medium">
                  {entry.title ?? entry.name}
                  {entry.status === "deprecated" && <span className="font-normal text-muted-foreground"> · deprecated</span>}
                </p>
                <p className="text-muted-foreground">
                  {host(entry.url)} · {entry.name}
                  {entry.header && " · needs a key"}
                </p>
                {entry.description && <p className="mt-1">{entry.description}</p>}
                {entry.repository_url && (
                  <a href={entry.repository_url} target="_blank" rel="noopener noreferrer nofollow" className="mt-1 inline-block underline underline-offset-4">
                    Its repository
                  </a>
                )}
              </div>
              <Button type="button" size="sm" variant="outline" onClick={() => onAdd({ entry })} aria-label={`Add ${entry.title ?? entry.name}`}>
                Add
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Settings > Connectors (docs/design/screens/settings.md, "Connectors"). */
export function ConnectorSettings({
  connectors,
  notice,
}: {
  connectors: Connector[];
  notice?: { ok: boolean; message: string } | null;
}) {
  const [adding, setAdding] = useState<false | { from?: FromDirectory }>(false);
  const [browsing, setBrowsing] = useState(false);
  return (
    <>
      {notice && (
        <p role={notice.ok ? "status" : "alert"} className={`px-4 pt-4 text-sm sm:px-5 ${notice.ok ? "text-foreground" : "text-destructive"}`}>
          {notice.message}
        </p>
      )}
      {connectors.map((connector) => (
        <ConnectorRow key={connector.id} connector={connector} />
      ))}
      {adding ? (
        <AddConnector key={adding.from?.entry.name ?? "new"} from={adding.from} onDone={() => setAdding(false)} />
      ) : browsing ? (
        <Directory
          onClose={() => setBrowsing(false)}
          onAdd={(from) => {
            setBrowsing(false);
            setAdding({ from });
          }}
        />
      ) : (
        <div className="flex flex-wrap gap-2 px-4 py-3 sm:px-5">
          <Button type="button" variant="ghost" size="sm" onClick={() => setAdding({})}>
            Add a connector
          </Button>
          <Button type="button" variant="ghost" size="sm" onClick={() => setBrowsing(true)}>
            Browse the directory
          </Button>
        </div>
      )}
    </>
  );
}
