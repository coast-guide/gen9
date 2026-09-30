import type { Metadata } from "next";
import Link from "next/link";

import { NeedsAdmin } from "@/components/admin/needs-admin";
import { LocalDate } from "@/components/local-date";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { orNotAdmin } from "@/lib/admin-access";
import { agentJson, type AuditEvent } from "@/lib/agent";
import { whatHappened, whoActed } from "@/lib/audit-words";
import { requireSession } from "@/lib/auth/session";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Audit log" };

const PAGE = 50;

export default async function AuditPage({ searchParams }: PageProps<"/admin/audit">) {
  const session = await requireSession("/admin/audit");
  if (!session.isAdmin) {
    return <NeedsAdmin />;
  }

  const params = await searchParams;
  const refused = params.show === "refused";
  const before = /^\d+$/.test(String(params.before ?? "")) ? String(params.before) : "";
  const events = await orNotAdmin(
    agentJson<AuditEvent[]>(
      session,
      `/v1/admin/audit?limit=${PAGE}${refused ? "&outcome=denied" : ""}${before ? `&before=${before}` : ""}`,
    ),
  );
  if (!events) return <NeedsAdmin demoted />;
  const show = refused ? "&show=refused" : "";

  return (
    <main className="mx-auto w-full max-w-4xl px-5 pt-8 pb-16 sm:px-8 lg:pt-14">
      <h1 className="text-headline font-semibold">Audit log</h1>
      <p className="mt-1 max-w-2xl text-muted-foreground">
        Who did what: admins’ changes, people’s security settings, and access Gen9 refused. Nobody can change or delete it.
      </p>

      <nav aria-label="Show" className="mt-6 flex flex-wrap gap-2">
        <Link href="/admin/audit" aria-current={refused ? undefined : "page"} className={buttonVariants({ variant: refused ? "ghost" : "secondary", size: "sm" })}>
          Everything
        </Link>
        <Link
          href="/admin/audit?show=refused"
          aria-current={refused ? "page" : undefined}
          className={buttonVariants({ variant: refused ? "secondary" : "ghost", size: "sm" })}
        >
          Refused access
        </Link>
      </nav>

      {events.length === 0 ? (
        <Empty className="mt-8 rounded-2xl border">
          <EmptyHeader>
            <EmptyTitle>{refused ? "No access refused" : "Nothing recorded yet"}</EmptyTitle>
            <EmptyDescription>
              {refused ? "When Gen9 refuses someone, it shows here." : "Admins’ changes and people’s security settings show here."}
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <ol aria-label="Events, newest first" className="mt-6 divide-y rounded-2xl border bg-card">
          {events.map((event) => (
            <li key={event.id} className="grid gap-1 px-4 py-3.5 sm:grid-cols-[11rem_1fr] sm:gap-4 sm:px-5">
              <p className="text-sm text-muted-foreground">
                <LocalDate ms={Date.parse(event.at)} withTime />
              </p>
              <div className="min-w-0">
                <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
                  <span className="font-medium break-all">{whoActed(event)}</span>
                  {event.outcome === "denied" && <Badge variant="destructive">Refused</Badge>}
                </p>
                <p className="text-sm break-words">{whatHappened(event)}</p>
                {event.where && <p className="mt-0.5 font-mono text-xs text-muted-foreground break-all">{event.where}</p>}
              </div>
            </li>
          ))}
        </ol>
      )}

      {(events.length === PAGE || before) && (
        <nav aria-label="Pages" className="mt-4 flex justify-between gap-2">
          {before ? (
            <Link href={`/admin/audit${refused ? "?show=refused" : ""}`} className={cn(buttonVariants({ variant: "ghost", size: "sm" }))}>
              Newest
            </Link>
          ) : (
            <span />
          )}
          {events.length === PAGE && (
            <Link href={`/admin/audit?before=${events.at(-1)?.id}${show}`} className={cn(buttonVariants({ variant: "outline", size: "sm" }))}>
              Older
            </Link>
          )}
        </nav>
      )}
    </main>
  );
}
