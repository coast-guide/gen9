import { CheckmarkCircle02Icon, Tick02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { redirect } from "next/navigation";

import { Mark, Wordmark } from "@/components/brand/logo";
import { buttonVariants } from "@/components/ui/button";
import { getSession } from "@/lib/auth/session";
import { cn } from "@/lib/utils";

const INVITATION = "Give it something to do.";

/** What Gen9 is, in one screen, before anyone signs in (docs/design/screens/home.md). */
export default async function Home() {
  if (await getSession()) redirect("/chat");

  return (
    <div className="flex min-h-dvh flex-col px-safe">
      <header className="pt-safe">
        <div className="mx-auto flex h-16 w-full max-w-6xl items-center px-5 sm:px-8">
          <Wordmark className="h-8" />
        </div>
      </header>

      <main className="mx-auto grid w-full max-w-6xl flex-1 content-start gap-10 px-5 pt-6 pb-10 sm:px-8 lg:grid-cols-12 lg:content-center lg:gap-16 lg:py-16">
        <section className="flex flex-col lg:col-span-5">
          <h1 className="text-display font-semibold text-balance">Get any task done with autonomous agents.</h1>
          <p className="mt-5 max-w-[38ch] text-lg leading-relaxed text-pretty text-muted-foreground">
            Configure your own agents: what they know, the tools they use, and what they may do without asking.
          </p>
          <div className="mt-9 hidden lg:block">
            <p className="font-medium">{INVITATION}</p>
            <div className="mt-4 flex gap-3">
              <SignInActions />
            </div>
          </div>
        </section>

        <section aria-label="Example task" className="lg:col-span-7">
          <Specimen />
        </section>
      </main>

      {/* Phones: primary actions sit in the thumb zone, above the home indicator */}
      <div className="sticky bottom-0 border-t border-border/70 bg-background/85 pb-safe backdrop-blur-md [--safe-min:1rem] lg:hidden">
        <div className="mx-auto grid max-w-md gap-2.5 px-5 pt-4">
          <p className="text-center text-sm font-medium">{INVITATION}</p>
          <SignInActions stacked />
        </div>
      </div>
    </div>
  );
}

function SignInActions({ stacked = false }: { stacked?: boolean }) {
  return (
    <>
      <a href="/auth/login" className={cn(buttonVariants({ size: "lg" }), stacked && "w-full")}>
        Sign in
      </a>
      <a
        href="/auth/login?intent=signup"
        className={cn(buttonVariants({ size: "lg", variant: "outline" }), stacked && "w-full")}
      >
        Create an account
      </a>
    </>
  );
}

const PLAN = ["Read the payments export", "Match it against the refunds sheet", "Draft the note"];
const STEPS = ["Read /work/payments-week-39.csv", "Ran: python match.py", "Used Sheets: read range"];
const MESSAGE = JSON.stringify({ channel: "#finance", text: "7 failed payments from last week have no refund yet. List attached." }, null, 2);

/**
 * One task as the chat shows it, set like a drafting sheet: crop marks frame it. An illustration, so nothing in
 * it acts. Its words are the chat's own (components/chat/activity.tsx, approval.tsx): change them together.
 */
function Specimen() {
  return (
    <figure className="relative mx-auto max-w-xl lg:max-w-none">
      <CropMarks />
      <div className="rounded-3xl border bg-card p-5 shadow-[0_1px_0_0_var(--border),0_24px_48px_-32px_color-mix(in_oklch,var(--foreground)_30%,transparent)] sm:p-7">
        <p className="ml-auto w-fit max-w-[85%] rounded-2xl rounded-br-md bg-secondary px-4 py-2.5 text-[0.9375rem]">
          Check last week’s failed payments against the refunds sheet and draft a note for finance.
        </p>
        <div className="mt-6 flex gap-3">
          <Mark className="mt-0.5 size-6" decorative />
          <div className="min-w-0 flex-1">
            <div className="mb-3 text-sm">
              <p className="text-muted-foreground">
                <span aria-hidden>▾ </span>Used 3 tools and a plan
              </p>
              <div className="mt-2 space-y-3">
                <ol className="space-y-1" aria-label="Plan">
                  {PLAN.map((item) => (
                    <li key={item} className="flex items-start gap-2">
                      <HugeiconsIcon icon={CheckmarkCircle02Icon} strokeWidth={2} className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-label="Done" />
                      <span className="text-muted-foreground line-through">{item}</span>
                    </li>
                  ))}
                </ol>
                <ul className="space-y-1 text-muted-foreground" aria-label="Tools used">
                  {STEPS.map((step) => (
                    <li key={step} className="flex items-start gap-2">
                      <span className="mt-0.5 shrink-0">
                        <HugeiconsIcon icon={Tick02Icon} strokeWidth={2} className="size-3.5 text-muted-foreground" aria-label="Done" />
                      </span>
                      <span className="min-w-0 break-words">{step}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
            <div className="prose-gen9">
              <p>
                7 of 112 failed payments have no refund yet. The list is in <code>unmatched.csv</code>, and the note is ready to post.
              </p>
            </div>
            <div className="mt-5 grid gap-4 rounded-2xl border bg-card p-4 shadow-sm sm:p-5">
              <p className="text-sm font-medium">Gen9 wants to use Slack: post message</p>
              <dl className="grid gap-1.5 text-sm">
                <div>
                  <dt className="text-muted-foreground">With</dt>
                  <dd dir="ltr" className="rounded-lg border bg-muted/40 p-2 font-mono text-xs break-words whitespace-pre-wrap">
                    {MESSAGE}
                  </dd>
                </div>
              </dl>
              {/* Drawn, not live: spans, so neither the keyboard nor a screen reader meets a button that does nothing */}
              <div aria-hidden className="flex flex-wrap items-center justify-end gap-3">
                <span className={buttonVariants({ variant: "ghost" })}>Deny</span>
                <span className={buttonVariants()}>Allow</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </figure>
  );
}

function CropMarks() {
  const mark = "pointer-events-none absolute size-4 border-foreground/25";
  return (
    <div aria-hidden className="hidden sm:block">
      <span className={cn(mark, "-top-4 -left-4 border-t border-l")} />
      <span className={cn(mark, "-top-4 -right-4 border-t border-r")} />
      <span className={cn(mark, "-bottom-4 -left-4 border-b border-l")} />
      <span className={cn(mark, "-right-4 -bottom-4 border-r border-b")} />
    </div>
  );
}
