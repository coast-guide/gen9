"use client";

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import type { ApprovalRequest } from "@/lib/agent";
import { actionWords, changesOf } from "@/lib/approvals";
import { hasHidden, reveal } from "@/lib/reveal";

export type Decision = { type: "approve" } | { type: "reject"; message?: string };

const MAX_REASON = 2000;

/** Text as it will run: each character that is invisible or changes how text reads shown where it
 * is, as its code point (lib/reveal.ts; P5-C9). */
function Revealed({ text }: { text: string }) {
  return (
    <>
      {reveal(text).map((piece, i) =>
        "text" in piece ? (
          <span key={i}>{piece.text}</span>
        ) : (
          <mark key={i} title={`Hidden character ${piece.code}`} className="mx-0.5 rounded border border-destructive/60 bg-transparent px-0.5 text-[0.7rem] text-destructive">
            {piece.code}
          </mark>
        ),
      )}
    </>
  );
}

/** What an action is given, in full: a connector's arguments, one per line when they fit. */
const argumentsOf = (args: unknown) => JSON.stringify(args ?? {}, null, 2);

/** Every text an action is given, however deep in its arguments. */
const textsOf = (value: unknown): string[] =>
  typeof value === "string" ? [value] : Array.isArray(value) ? value.flatMap(textsOf) : value && typeof value === "object" ? Object.values(value).flatMap(textsOf) : [];

/**
 * An action waiting for Allow or Deny, in a chat set to "Ask before acting"
 * (docs/design/screens/chat.md, "Approvals and the permission mode"). One card per action; `decide`
 * sends the decision and returns what went wrong, or null.
 */
export function ApprovalCard({
  request,
  decide,
  takeFocus,
}: {
  request: ApprovalRequest;
  decide: (decisions: Decision[]) => Promise<string | null>;
  takeFocus: boolean;
}) {
  const [denying, setDenying] = useState(false);
  const [reason, setReason] = useState("");
  const [sending, setSending] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const card = useRef<HTMLElement>(null);
  const reasonField = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (takeFocus) card.current?.querySelector<HTMLElement>("button")?.focus();
  }, [takeFocus]);
  useEffect(() => {
    if (denying) reasonField.current?.focus();
  }, [denying]);

  async function send(decision: Decision) {
    setSending(true);
    setProblem(null);
    // One decision per action: the same for all of them (the card lists every action it covers)
    const wrong = await decide(request.action_requests.map(() => decision));
    if (wrong) {
      setProblem(wrong);
      setSending(false);
    }
  }

  const [first] = request.action_requests;
  // Anything the person decides on that holds hidden characters: said above the details
  const hidden = request.action_requests.some((action) => textsOf(action.args).some(hasHidden));
  return (
    <section ref={card} aria-label="Gen9 needs your approval" className="mb-4 grid gap-4 rounded-2xl border bg-card p-4 shadow-sm sm:p-5">
      <p className="text-sm font-medium">Gen9 wants to {first ? actionWords(first) : "act"}</p>
      {hidden && (
        <p className="text-sm text-destructive">
          This holds characters that are invisible or change how text reads, each shown as its code (U+…). Check what it does before you allow it.
        </p>
      )}
      {request.action_requests.map((action, i) => {
        const { added, removed } = changesOf(action);
        return (
          <dl key={i} className="grid gap-1.5 text-sm">
            {added.length > 0 && (
              <div>
                <dt className="text-muted-foreground">Adds</dt>
                <dd className="whitespace-pre-wrap">
                  <Revealed text={added.join("\n")} />
                </dd>
              </div>
            )}
            {removed.length > 0 && (
              <div>
                <dt className="text-muted-foreground">Removes</dt>
                <dd className="whitespace-pre-wrap line-through decoration-muted-foreground">
                  <Revealed text={removed.join("\n")} />
                </dd>
              </div>
            )}
            {action.name === "execute" && (
              <div>
                <dt className="text-muted-foreground">Command</dt>
                <dd dir="ltr" className="whitespace-pre-wrap break-words font-mono text-xs">
                  <Revealed text={String((action.args as { command?: unknown })?.command ?? "")} />
                </dd>
              </div>
            )}
            {action.name !== "execute" && !added.length && !removed.length && (
              <div>
                <dt className="text-muted-foreground">With</dt>
                {/* In full, however long: what isn't shown can't be allowed knowingly */}
                <dd dir="ltr" className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-lg border bg-muted/40 p-2 font-mono text-xs">
                  <Revealed text={argumentsOf(action.args)} />
                </dd>
              </div>
            )}
          </dl>
        );
      })}
      {denying ? (
        <form
          className="grid gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            void send({ type: "reject", ...(reason.trim() ? { message: reason.trim() } : {}) });
          }}
        >
          <label htmlFor={`reason-${request.id}`} className="text-sm">
            What should Gen9 do instead? <span className="text-muted-foreground">(optional)</span>
          </label>
          <Textarea
            id={`reason-${request.id}`}
            ref={reasonField}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            maxLength={MAX_REASON}
            rows={2}
            className="min-h-11"
            disabled={sending}
          />
          <div className="flex flex-wrap items-center justify-end gap-3">
            {problem && (
              <p role="alert" className="mr-auto text-sm text-destructive">
                {problem}
              </p>
            )}
            <Button type="button" variant="ghost" disabled={sending} onClick={() => setDenying(false)}>
              Back
            </Button>
            <Button type="submit" disabled={sending}>
              {sending && <Spinner aria-hidden />}
              Deny
            </Button>
          </div>
        </form>
      ) : (
        <div className="flex flex-wrap items-center justify-end gap-3">
          {problem && (
            <p role="alert" className="mr-auto text-sm text-destructive">
              {problem}
            </p>
          )}
          <Button type="button" variant="ghost" disabled={sending} onClick={() => setDenying(true)}>
            Deny
          </Button>
          <Button type="button" disabled={sending} onClick={() => void send({ type: "approve" })}>
            {sending && <Spinner aria-hidden />}
            Allow
          </Button>
        </div>
      )}
    </section>
  );
}
