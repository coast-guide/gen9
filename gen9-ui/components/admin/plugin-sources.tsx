"use client";

import { MoreHorizontalIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useRouter } from "next/navigation";
import { useEffect, useState, useTransition } from "react";
import { toast } from "sonner";

import {
  addPluginSource,
  removePluginSource,
  readPluginFile,
  setPluginAvailability,
  syncPluginSource,
} from "@/app/(app)/admin/plugins/actions";
import { RelativeTime } from "@/components/relative-time";
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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { sizeOf } from "@/components/chat/files";
import type { AdminPlugin, PluginAvailability, PluginFileContent, PluginSource } from "@/lib/agent";

const REFRESH_MS = 2000;

/** Refreshes the page's server data while `active`: a source is syncing. */
function useRefreshWhile(active: boolean) {
  const router = useRouter();
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => router.refresh(), REFRESH_MS);
    return () => clearInterval(timer);
  }, [active, router]);
}

const plural = (n: number, one: string) => `${n} ${one}${n === 1 ? "" : "s"}`;

export function SourceRow({ source }: { source: PluginSource }) {
  const [pending, start] = useTransition();
  // Sync now: the synced_at it started from; the row says "Syncing…" until that changes
  const [since, setSince] = useState<string | null | undefined>(undefined);
  const [confirm, setConfirm] = useState(false);
  const syncing = source.status === "pending" || (since !== undefined && source.synced_at === since);
  useRefreshWhile(syncing);
  const name = source.name ?? source.url;
  return (
    <li className="flex items-start gap-3 px-4 py-3.5 wrap-break-word sm:px-5">
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{name}</p>
        <p className="truncate text-sm text-muted-foreground">
          {source.url}
          {source.ref ? ` · ${source.ref}` : ""}
        </p>
        <p className="mt-1 text-sm" aria-live="polite">
          {syncing ? (
            <span className="inline-flex items-center gap-2 text-muted-foreground">
              <Spinner className="size-3.5" /> Syncing…
            </span>
          ) : source.status === "failed" ? (
            <>
              <span className="block text-destructive">Couldn’t sync: {source.error}</span>
              {/* What it synced before stays: say how old it is (P3-D5) */}
              <span className="block text-muted-foreground">
                {source.succeeded_at ? (
                  <>
                    Last synced <RelativeTime ms={Date.parse(source.succeeded_at)} /> · {plural(source.plugins, "plugin")}
                  </>
                ) : (
                  "Never synced"
                )}
              </span>
            </>
          ) : (
            <span className="text-muted-foreground">
              Synced {source.synced_at ? <RelativeTime ms={Date.parse(source.synced_at)} /> : ""} · {plural(source.plugins, "plugin")}
              {source.error ? <span className="text-destructive"> · {source.error}</span> : null}
            </span>
          )}
        </p>
      </div>
      <DropdownMenu>
        <DropdownMenuTrigger render={<Button variant="ghost" size="icon" disabled={pending} aria-label={`Actions for ${name}`} />}>
          <HugeiconsIcon icon={MoreHorizontalIcon} strokeWidth={1.8} className="size-5" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-48">
          <DropdownMenuItem
            disabled={syncing}
            onClick={() =>
              start(async () => {
                const result = await syncPluginSource(source.id);
                if (result.ok) setSince(source.synced_at);
                else toast.error(result.message);
              })
            }
          >
            Sync now
          </DropdownMenuItem>
          <DropdownMenuItem variant="destructive" onClick={() => setConfirm(true)}>
            Remove…
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove {name}?</AlertDialogTitle>
            <AlertDialogDescription>
              {source.plugins === 1 ? "Its plugin goes" : `Its ${plural(source.plugins, "plugin")} go`} too, for everyone. You can add the
              repository again later.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() =>
                start(async () => {
                  const result = await removePluginSource(source.id);
                  if (result.ok) toast.success(result.message);
                  else toast.error(result.message);
                })
              }
            >
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </li>
  );
}

export function AddSource() {
  const [url, setUrl] = useState("");
  const [ref, setRef] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [pending, start] = useTransition();
  return (
    <form
      aria-label="Add a source"
      className="grid gap-3 px-4 py-4 sm:grid-cols-[1fr_12rem_auto] sm:items-end sm:px-5"
      onSubmit={(e) => {
        e.preventDefault();
        setProblem(null);
        start(async () => {
          const result = await addPluginSource(url, ref);
          if (!result.ok) return setProblem(result.message);
          toast.success(result.message);
          setUrl("");
          setRef("");
        });
      }}
    >
      <label className="grid gap-1.5 text-sm">
        <span className="font-medium">Repository</span>
        <Input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          required
          type="url"
          placeholder="https://github.com/your-org/plugins"
          autoComplete="off"
          spellCheck={false}
          aria-invalid={problem ? true : undefined}
          aria-describedby={problem ? "add-source-problem" : undefined}
        />
      </label>
      <label className="grid gap-1.5 text-sm">
        <span className="font-medium">Branch or tag</span>
        <Input value={ref} onChange={(e) => setRef(e.target.value)} placeholder="Its default" autoComplete="off" spellCheck={false} />
      </label>
      <Button type="submit" disabled={pending || !url.trim()}>
        {pending ? <Spinner /> : null} Add
      </Button>
      {problem && (
        <p id="add-source-problem" role="alert" className="text-sm text-destructive sm:col-span-3">
          {problem}
        </p>
      )}
    </form>
  );
}

const FORMAT: Record<string, string | undefined> = { codex: "Codex", claude: "Claude Code" };
const AVAILABILITY: { value: PluginAvailability; label: string }[] = [
  { value: "off", label: "Nobody" },
  { value: "available", label: "People who add it" },
  { value: "installed", label: "Everyone" },
];
const STATUS: Record<AdminPlugin["status"], string> = {
  loaded: "",
  rejected: "Couldn’t load",
  unsupported: "Not supported",
  failed: "Couldn’t fetch",
};

/** What a plugin brings, in one line: "2 skills · 1 connector · 1 server not run". */
function brings(plugin: AdminPlugin) {
  const connectors = plugin.mcp_servers.filter((s) => s.connects).length;
  const notRun = plugin.mcp_servers.length - connectors;
  const parts = [
    plugin.skills.length ? plural(plugin.skills.length, "skill") : null,
    connectors ? plural(connectors, "connector") : null,
    notRun ? `${plural(notRun, "server")} not run` : null,
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : "Nothing Gen9 can use";
}

export function PluginRow({ plugin }: { plugin: AdminPlugin }) {
  const [pending, start] = useTransition();
  const [availability, setAvailability] = useState(plugin.availability);
  const name = plugin.title ?? plugin.name;
  const selectId = `availability-${plugin.id}`;
  return (
    <li className="grid gap-2 px-4 py-3.5 wrap-break-word sm:px-5 [&>*]:min-w-0">
      <div className="flex flex-wrap items-start gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1 basis-48">
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm font-medium">
            <span className="truncate">{name}</span>
            {plugin.version && <span className="font-normal text-muted-foreground">{plugin.version}</span>}
            {plugin.format && FORMAT[plugin.format] && <Badge variant="outline">{FORMAT[plugin.format]}</Badge>}
          </p>
          {plugin.status === "loaded" ? (
            <p className="text-sm text-muted-foreground">{brings(plugin)}</p>
          ) : (
            <p className="text-sm text-destructive">
              {STATUS[plugin.status]}: {plugin.reason}
            </p>
          )}
        </div>
        {plugin.status === "loaded" && (
          <label htmlFor={selectId} className="grid gap-1 text-sm">
            <span className="sr-only">Who can use {name}</span>
            <select
              id={selectId}
              value={availability}
              disabled={pending}
              onChange={(e) => {
                const next = e.target.value as PluginAvailability;
                const before = availability;
                setAvailability(next);
                start(async () => {
                  const result = await setPluginAvailability(plugin.id, next, plugin.fingerprint);
                  if (result.ok) toast.success(result.message);
                  else {
                    setAvailability(before);
                    toast.error(result.message);
                  }
                });
              }}
              className="h-9 rounded-full border bg-background px-3 text-sm pointer-coarse:h-11"
            >
              {AVAILABILITY.map((a) => (
                <option key={a.value} value={a.value}>
                  {a.label}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {plugin.changed && (
        <div role="group" aria-label={`${name} changed`} className="grid gap-2 rounded-xl border p-3 text-sm">
          <p>
            <span className="font-medium">It changed since you chose who can use it</span>
            {plugin.commit && <span className="text-muted-foreground"> (now at commit {plugin.commit.slice(0, 7)})</span>}. Nobody gets it until you
            look at what it says now (Details, and its repository at that commit).
          </p>
          <div>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={pending}
              onClick={() =>
                start(async () => {
                  const result = await setPluginAvailability(plugin.id, availability, plugin.fingerprint);
                  if (result.ok) toast.success("People can have it again.");
                  else toast.error(result.message);
                })
              }
            >
              Let people have it again
            </Button>
          </div>
        </div>
      )}
      {(plugin.status === "loaded" || plugin.skipped.length > 0) && (
        <details className="text-sm">
          <summary className="w-fit cursor-pointer rounded-full text-muted-foreground hover:text-foreground">Details</summary>
          <div className="mt-2 grid gap-3 [&>*]:min-w-0">
            {plugin.description && <p>{plugin.description}</p>}
            {plugin.skills.length > 0 && (
              <div>
                <p className="font-medium">Skills</p>
                <ul className="mt-1 grid gap-1 [&>*]:min-w-0">
                  {plugin.skills.map((s) => (
                    <li key={s.name}>
                      <span className="font-mono text-xs">{s.name}</span>
                      <span className="text-muted-foreground"> · {s.description}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {plugin.files.length > 0 && (
              <div>
                <p className="font-medium">Files Gen9 keeps</p>
                <p className="text-muted-foreground">What its skills tell Gen9 to do: read them before people get it.</p>
                <ul className="mt-1 grid gap-1 [&>*]:min-w-0">
                  {plugin.files.map((f) => (
                    <PluginFile key={f.path} pluginId={plugin.id} path={f.path} size={f.size} />
                  ))}
                </ul>
              </div>
            )}
            {plugin.mcp_servers.length > 0 && (
              <div>
                <p className="font-medium">MCP servers</p>
                <ul className="mt-1 grid gap-1 [&>*]:min-w-0">
                  {plugin.mcp_servers.map((s) => (
                    <li key={s.name}>
                      <span className="font-mono text-xs">{s.name}</span>
                      <span className="text-muted-foreground">
                        {" "}
                        · {s.connects ? `Connects to ${s.url}` : "Not run: it runs on a computer, and Gen9 runs no plugin’s programs"}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {plugin.skipped.length > 0 && (
              <div>
                <p className="font-medium">Skipped</p>
                <ul className="mt-1 grid gap-1 text-muted-foreground">
                  {plugin.skipped.map((s) => (
                    <li key={s.what}>
                      <span className="font-mono text-xs text-foreground">{s.what}</span> · {s.why}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {plugin.notes.length > 0 && (
              <div>
                <p className="font-medium">Notes</p>
                <ul className="mt-1 grid gap-1 text-muted-foreground">
                  {plugin.notes.map((n) => (
                    <li key={n}>{n}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </details>
      )}
    </li>
  );
}

/** One of a plugin's kept files, read when opened (P5-C4: an admin reads what a skill says before
 * people get it). */
function PluginFile({ pluginId, path, size }: { pluginId: string; path: string; size: number }) {
  const [shown, setShown] = useState<PluginFileContent | { error: string } | null>(null);
  const [pending, start] = useTransition();
  return (
    <li>
      <details
        onToggle={(e) => {
          if ((e.currentTarget as HTMLDetailsElement).open && !shown)
            start(async () => {
              setShown(await readPluginFile(pluginId, path));
            });
        }}
      >
        <summary className="w-fit cursor-pointer">
          <span className="font-mono text-xs">{path}</span>
          <span className="text-muted-foreground"> · {sizeOf(size)}</span>
        </summary>
        {pending && <Spinner className="mt-1 size-4" aria-label="Reading" />}
        {shown &&
          ("error" in shown ? (
            <p role="alert" className="mt-1 text-destructive">
              {shown.error}
            </p>
          ) : shown.text === null ? (
            <p className="mt-1 text-muted-foreground">Not text: read it in its repository.</p>
          ) : (
            <>
              <pre className="mt-1 max-h-80 overflow-auto rounded-lg border bg-muted/40 p-3 font-mono text-xs whitespace-pre-wrap">{shown.text}</pre>
              {shown.cut && <p className="mt-1 text-muted-foreground">Only its start is shown: read the rest in its repository.</p>}
            </>
          ))}
      </details>
    </li>
  );
}
