"use client";

// Before the MCP Apps SDK, which brings Zod: its eval check must not run (lib/zod-jitless.ts)
import "@/lib/zod-jitless";
import { AppBridge, PostMessageTransport } from "@modelcontextprotocol/ext-apps/app-bridge";
import { useTheme } from "next-themes";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import type { StepApp } from "@/lib/agent";
import { messageText, settled } from "@/lib/app-asks";
import { addressParts } from "@/lib/elicitation";
import { refusal } from "@/lib/refusal";

const MIN_HEIGHT = 120;
const MAX_HEIGHT = 900;
const PROXY_READY_MS = 15_000;
const HOST = { name: "Gen9", version: "1.0.0" };

/** What the View asks the person before Gen9 does it. */
type Asked = { kind: "tool"; name: string } | { kind: "link"; url: string } | { kind: "message"; text: string };
/** `at`: when it last appeared or moved (its yes waits until it has settled); `focus`: whether it
 * takes the focus, only from the View itself. */
type Ask = Asked & { answer: (yes: boolean) => void; at: number; focus: boolean };

/** Where a View's message goes: the composer, for the person to send. `false` when it would replace
 * their draft and `replace` isn't set; `focus` moves the focus there, only from the View itself. */
export type ToComposer = (text: string, how: { from: string; replace: boolean; focus: boolean }) => boolean;

type ResourceRead = { contents: { text?: string; blob?: string; _meta?: { ui?: { csp?: Record<string, unknown> } } }[]; sandbox: string };

async function readResource(connectorId: string, uri: string): Promise<ResourceRead> {
  const response = await fetch(`/api/connectors/${connectorId}/app/resource`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ uri }),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(refusal(body.detail, "Couldn't load this app."));
  return body as ResourceRead;
}

const htmlOf = (content: ResourceRead["contents"][number]) =>
  content.text ?? new TextDecoder().decode(Uint8Array.from(atob(content.blob ?? ""), (c) => c.charCodeAt(0)));

/**
 * A connector tool's View (MCP Apps), under its step: the server's HTML in a sandbox on another
 * origin (the sandbox service), spoken to through the official host SDK (`AppBridge`) with no MCP
 * client of its own. What the View asks for goes through Gen9: its server's tools and resources
 * (gen9-agent, under the connector's policy), a link to open, a message for the composer.
 */
export function AppView({ app, onMessage }: { app: StepApp; onMessage: ToComposer }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const bridge = useRef<AppBridge | null>(null);
  const message = useRef(onMessage);
  const [height, setHeight] = useState(MIN_HEIGHT);
  const [problem, setProblem] = useState<string | null>(null);
  const [ask, setAsk] = useState<Ask | null>(null);
  const { resolvedTheme } = useTheme();
  const theme: "dark" | "light" = resolvedTheme === "dark" ? "dark" : "light";
  const themeNow = useRef(theme);

  useEffect(() => {
    message.current = onMessage;
  }, [onMessage]);

  useEffect(() => {
    const iframe = frame.current;
    if (!iframe) return;
    let stopped = false;
    let ready = false;
    let host: AppBridge | null = null;
    let open: ((yes: boolean) => void) | null = null;
    // The View may ask at any moment, not only when clicked (P5-C1): the focus stays where the person
    // is unless they are in this View, so keys typed elsewhere can't answer it
    const inView = () => document.activeElement === iframe;
    const confirm = (asked: Asked) =>
      new Promise<boolean>((resolve) => {
        // A new ask ends the one before, unanswered: the button about to be pressed is gone
        open?.(false);
        const answer = (yes: boolean) => {
          if (open === answer) open = null;
          setAsk((now) => (now?.answer === answer ? null : now));
          resolve(yes);
        };
        open = answer;
        setAsk({ ...asked, answer, at: performance.now(), focus: inView() });
      });

    (async () => {
      const resource = await readResource(app.connector_id, app.resource_uri);
      const [content] = resource.contents;
      if (!content) throw new Error("The server sent no app.");
      const csp = content._meta?.ui?.csp;
      // The proxy says it's ready once loaded on its origin; listen before loading it
      const proxied = new Promise<void>((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error("Couldn't reach the app sandbox.")), PROXY_READY_MS);
        const listen = (event: MessageEvent) => {
          if (event.source === iframe.contentWindow && event.origin === resource.sandbox && event.data?.method === "ui/notifications/sandbox-proxy-ready") {
            clearTimeout(timer);
            window.removeEventListener("message", listen);
            resolve();
          }
        };
        window.addEventListener("message", listen);
      });
      iframe.src = `${resource.sandbox}/?csp=${encodeURIComponent(JSON.stringify(csp ?? {}))}`;
      await proxied;
      if (stopped) return;

      host = new AppBridge(
        null,
        HOST,
        { openLinks: {}, serverTools: {}, serverResources: {}, logging: {} },
        {
          hostContext: {
            theme: themeNow.current,
            platform: "web",
            displayMode: "inline",
            availableDisplayModes: ["inline"],
            containerDimensions: { maxHeight: MAX_HEIGHT },
            locale: navigator.language,
            timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
          },
        },
      );
      bridge.current = host;
      // Every handler before connect: the View may ask as soon as it has initialized
      host.oncalltool = async (params) => {
        const call = (allowed: boolean) =>
          fetch(`/api/connectors/${app.connector_id}/app/call`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: params.name, arguments: params.arguments ?? {}, allowed }),
          });
        let response = await call(false);
        if (response.status === 409) {
          if (!(await confirm({ kind: "tool", name: params.name }))) throw new Error(`The person didn't allow ${params.name}.`);
          response = await call(true);
        }
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(refusal(body.detail, `${params.name} failed.`));
        return body;
      };
      host.onreadresource = async ({ uri }) => ({ contents: (await readResource(app.connector_id, uri)).contents }) as never;
      host.onopenlink = async ({ url }) => {
        if (!/^https?:$/.test(new URL(url).protocol)) throw new Error("Only web addresses open.");
        if (!(await confirm({ kind: "link", url }))) throw new Error("Link opening denied by user");
        return {};
      };
      // Into the composer for the person to send, never sent by the View; over their draft only if
      // they agree (the spec's "Host MAY request user consent")
      host.onmessage = async ({ content }) => {
        const text = messageText(content);
        if (!text.trim()) throw new Error("Invalid message format");
        const how = { from: app.connector, focus: inView() };
        if (message.current(text, { ...how, replace: false })) return {};
        if (!(await confirm({ kind: "message", text }))) throw new Error("Message sending denied");
        message.current(text, { ...how, replace: true });
        return {};
      };
      host.onsizechange = async ({ height: wanted }) => {
        if (wanted === undefined) return;
        setHeight(Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, Math.ceil(wanted))));
        // Resizing moves the ask under the View: it settles again, so the View can't slide its
        // Allow under a click on its way elsewhere
        setAsk((now) => now && { ...now, at: performance.now() });
      };
      host.onrequestdisplaymode = async () => ({ mode: "inline" });
      host.onloggingmessage = (params) => console.debug(`[${app.connector}'s app]`, params.data);
      const initialized = new Promise<void>((resolve) => {
        host!.oninitialized = () => resolve();
      });
      await host.connect(new PostMessageTransport(iframe.contentWindow!, iframe.contentWindow!));
      await host.sendSandboxResourceReady({ html: htmlOf(content), csp });
      await initialized;
      if (stopped) return;
      ready = true;
      await host.sendToolInput({ arguments: app.input ?? {} });
      await host.sendToolResult(app.result as never);
    })().catch((error: Error) => {
      if (!stopped) setProblem(error.message || "Couldn't load this app.");
    });

    return () => {
      stopped = true;
      bridge.current = null;
      if (host && ready) void host.teardownResource({}).catch(() => {}).finally(() => void host?.close());
      else void host?.close();
    };
  }, [app]);

  // The page's theme, as the View's host context
  useEffect(() => {
    themeNow.current = theme;
    Promise.resolve(bridge.current?.sendHostContextChange({ theme })).catch(() => {});
  }, [theme]);

  return (
    <figure className="mb-3 overflow-hidden rounded-xl border bg-card" aria-label={`${app.connector}'s app`}>
      <figcaption className="border-b px-3 py-1.5 text-xs text-muted-foreground">
        App from <span className="font-medium text-foreground">{app.connector}</span>, not made by Gen9
      </figcaption>
      {problem && (
        <p role="alert" className="px-3 py-2 text-sm text-muted-foreground">
          {problem}
        </p>
      )}
      <iframe
        ref={frame}
        title={`${app.connector}'s app`}
        sandbox="allow-scripts allow-same-origin allow-forms"
        className={problem ? "hidden" : "block w-full"}
        style={{ height }}
      />
      {ask && <AskBar connector={app.connector} ask={ask} />}
      {/* Said when an ask doesn't take the focus: the person is elsewhere */}
      <p className="sr-only" aria-live="polite">
        {ask && !ask.focus ? `${app.connector}’s app is asking you something, under the app.` : ""}
      </p>
    </figure>
  );
}

/** What the View wants Gen9 to do for it, for the person to allow or not. It takes the focus only
 * from the View itself, and then onto itself, not its Allow: a key typed a moment later isn't a
 * decision. Its yes acts only once it has settled (SETTLE_MS). */
function AskBar({ connector, ask }: { connector: string; ask: Ask }) {
  const bar = useRef<HTMLDivElement>(null);
  const { answer, focus } = ask;
  useEffect(() => {
    if (focus) bar.current?.focus();
  }, [answer, focus]);
  const yes = (act?: () => void) => () => {
    if (!settled(ask.at, performance.now())) return;
    act?.();
    ask.answer(true);
  };
  const group = { ref: bar, role: "group", tabIndex: -1, "aria-label": `${connector}'s app asks` };
  const no = (label: string) => (
    <Button type="button" size="sm" variant="outline" onClick={() => ask.answer(false)}>
      {label}
    </Button>
  );
  if (ask.kind === "tool") {
    return (
      <div {...group} className="flex flex-wrap items-center gap-2 border-t px-3 py-2 text-sm outline-hidden">
        <span className="min-w-0 flex-1">
          {connector}’s app wants to use <span className="font-medium">{ask.name}</span>.
        </span>
        <Button type="button" size="sm" onClick={yes()}>
          Allow
        </Button>
        {no("Deny")}
      </div>
    );
  }
  if (ask.kind === "message") {
    return (
      <div {...group} className="grid gap-2 border-t px-3 py-2 text-sm outline-hidden">
        <span>{connector}’s app wants to replace your message with:</span>
        <blockquote className="max-h-32 overflow-y-auto border-l-2 pl-3 whitespace-pre-wrap text-muted-foreground">{ask.text}</blockquote>
        <div className="flex gap-2">
          <Button type="button" size="sm" onClick={yes()}>
            Replace mine
          </Button>
          {no("Keep mine")}
        </div>
      </div>
    );
  }
  const { before, host, after, punycode } = addressParts(ask.url);
  return (
    <div {...group} className="grid gap-2 border-t px-3 py-2 text-sm outline-hidden">
      <span>{connector}’s app wants to open:</span>
      <p className="break-all font-mono text-xs">
        {before}
        <strong className="text-sm text-foreground">{host}</strong>
        {after}
      </p>
      {punycode && <p className="text-xs text-destructive">This address uses characters that can imitate another site’s name. Check it before opening.</p>}
      <div className="flex gap-2">
        {/* In the click itself, so the browser lets the tab open */}
        <Button type="button" size="sm" onClick={yes(() => window.open(ask.url, "_blank", "noopener,noreferrer"))}>
          Open {host || "it"}
        </Button>
        {no("Cancel")}
      </div>
    </div>
  );
}
