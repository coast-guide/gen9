import { Alert02Icon, Cancel01Icon, CheckmarkCircle02Icon, HelpCircleIcon, Tick02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import Link from "next/link";

import { Spinner } from "@/components/ui/spinner";
import type { Action, Step, Todo } from "@/lib/agent";
import { actionWords, MEMORY_FILE, reasonIn } from "@/lib/approvals";
import { answersIn } from "@/lib/questions";
import { cn } from "@/lib/utils";

/** What a tool call did, in words: "Searched the web: postgres 18", "Read /notes.md". */
export function describeStep(step: Step): string {
  const args = (step.args ?? {}) as Record<string, unknown>;
  // Denied in a chat set to "Ask before acting" (gen9-agent's approvals.py)
  if (step.status === "declined") return `You declined: ${actionWords({ name: step.name, args })}`;
  const text = (value: unknown) => (typeof value === "string" ? value : "");
  switch (step.name) {
    case "web_search":
      return text(args.query) ? `Searched the web: ${text(args.query)}` : "Searched the web";
    case "web_open":
      return `Opened ${text(args.url)}`;
    case "web_find":
      return `Looked for “${text(args.pattern)}” in ${text(args.url)}`;
    case "ask_user": {
      // Questions to the person (gen9-agent's questions.py): the first, and how many more
      const asked = Array.isArray(args.questions) ? (args.questions as { question?: unknown }[]) : [];
      const first = text(asked[0]?.question);
      const more = asked.length > 1 ? ` (and ${asked.length - 1} more)` : "";
      return first ? `Asked you: ${first}${more}` : "Asked you a question";
    }
    case "task": {
      // A subagent the agent's definition declares is named; the general-purpose one is "a helper"
      const who = text(args.subagent_type);
      const helper = who && who !== "general-purpose" ? `the ${who.replaceAll("-", " ")}` : "a helper";
      return `Asked ${helper}: ${text(args.description).slice(0, 120)}`;
    }
    // Work started in the background (gen9-agent's background.py), each task a chat of its own
    case "start_async_task":
      return `Started in the background: ${text(args.description).split("\n")[0].slice(0, 120)}`;
    case "check_async_task":
      return "Checked a background task";
    case "update_async_task":
      return "Gave a background task new instructions";
    case "cancel_async_task":
      return "Stopped a background task";
    case "list_async_tasks":
      return "Looked at the background tasks";
    // The person's earlier chats (gen9-agent's past_chats.py)
    case "search_past_chats":
      return text(args.query) ? `Searched your past chats: ${text(args.query)}` : "Searched your past chats";
    case "recent_chats":
      return "Looked at your recent chats";
    case "read_file": {
      const path = text(args.file_path);
      if (path === MEMORY_FILE) return "Read your memory";
      // A built-in skill's instructions (gen9-agent's skills.py): /skills/<name>/SKILL.md
      const skill = path.match(/^\/skills\/([a-z0-9-]+)\/SKILL\.md$/)?.[1];
      if (skill) return `Used the ${skill.replaceAll("-", " ")} skill`;
      // A skill of one of the person's plugins (gen9-agent's plugin_skills.py): /plugins/<name>/SKILL.md
      const theirs = path.match(/^\/plugins\/([^/]+)\/SKILL\.md$/)?.[1];
      if (theirs) return `Used the ${theirs.replaceAll("-", " ")} skill${step.plugin ? ` from ${step.plugin}` : ""}`;
      return `Read ${path}`;
    }
    case "write_file":
    case "edit_file":
      if (text(args.file_path) === MEMORY_FILE) return "Updated your memory";
      return `${step.name === "write_file" ? "Wrote" : "Edited"} ${text(args.file_path)}`;
    case "ls":
      return `Listed ${text(args.path) || "files"}`;
    case "execute":
      // A command in the chat's environment (gen9-agent's environments.py)
      return `Ran: ${text(args.command).slice(0, 120)}`;
    case "glob":
    case "grep":
      return `Searched files for ${text(args.pattern)}`;
    default: {
      // A connector's tool (gen9-agent's connectors.py): <connector>__<tool>
      const [connector, tool] = step.name.split("__");
      if (tool) return `Used ${connector}: ${tool.replaceAll("_", " ")}`;
      return step.name.replaceAll("_", " ");
    }
  }
}

function StepIcon({ status, asking, working }: { status: Step["status"]; asking: boolean; working: boolean }) {
  // A question, or an action waiting for Allow or Deny, waits for the person: no spinner
  if (status === "running" && asking) return <HugeiconsIcon icon={HelpCircleIcon} strokeWidth={2} className="size-3.5 text-foreground" aria-label="Waiting for you" />;
  // Still "running" in a turn that ended (stopped, or failed): it never finished, and no spinner says otherwise (P2-K1)
  if (status === "running" && !working) return <span className="mx-1 inline-block h-0.5 w-1.5 rounded-full bg-muted-foreground" role="img" aria-label="Didn’t finish" />;
  if (status === "running") return <Spinner className="size-3.5 text-muted-foreground" aria-label="Running" />;
  if (status === "error") return <HugeiconsIcon icon={Alert02Icon} strokeWidth={2} className="size-3.5 text-destructive" aria-label="Failed" />;
  if (status === "declined") return <HugeiconsIcon icon={Cancel01Icon} strokeWidth={2} className="size-3.5 text-muted-foreground" aria-label="Declined" />;
  return <HugeiconsIcon icon={Tick02Icon} strokeWidth={2} className="size-3.5 text-muted-foreground" aria-label="Done" />;
}

// The same call: its tool and its arguments, whatever their keys' order
const callKey = (name: string, args: unknown): string =>
  `${name}:${JSON.stringify(args ?? {}, (_, value: unknown) =>
    value && typeof value === "object" && !Array.isArray(value)
      ? Object.fromEntries(Object.entries(value as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b)))
      : value,
  )}`;

/** Steps in words; a step an approval holds reads "Waiting for you: …", as its card reads "Gen9
 *  wants to …", not "Used …" for what hasn't happened (manual-e2e.md, P3-D4). */
export function stepWords(step: Step, awaiting: Pick<Action, "name" | "args">[] = []): { text: string; waits: boolean } {
  const waits = step.status === "running" && awaiting.some((a) => callKey(a.name, a.args) === callKey(step.name, step.args));
  return waits ? { text: `Waiting for you: ${actionWords({ name: step.name, args: step.args as Record<string, unknown> })}`, waits } : { text: describeStep(step), waits };
}

/** The folded line over a finished turn's steps: "Used 3 tools and a plan", or "Made a plan" */
export function stepsSummary(tools: number, plan: boolean): string {
  if (!tools) return plan ? "Made a plan" : "";
  return `Used ${tools} tool${tools === 1 ? "" : "s"}${plan ? " and a plan" : ""}`;
}

export function Activity({
  steps,
  todos,
  working,
  waiting = false,
  awaiting = [],
}: {
  steps: Step[];
  todos: Todo[];
  working: boolean;
  // The calls an approval holds (its requests' actions)
  awaiting?: Pick<Action, "name" | "args">[];
  // The run waits for the person (a question or an approval): what is still running waits too
  waiting?: boolean;
}) {
  const shown = steps.filter((s) => s.name !== "write_todos"); // the plan below shows those
  if (!shown.length && !todos.length) return null;
  const list = (
    <div className="mt-2 space-y-3">
      {todos.length > 0 && (
        <ol className="space-y-1 text-sm" aria-label="Plan">
          {todos.map((todo, i) => (
            <li key={i} className="flex items-start gap-2">
              {todo.status === "completed" ? (
                <HugeiconsIcon icon={CheckmarkCircle02Icon} strokeWidth={2} className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-label="Done" />
              ) : todo.status === "in_progress" && working && !waiting ? (
                <Spinner className="mt-0.5 size-4 shrink-0 text-foreground" aria-label="In progress" />
              ) : (
                <span className="mt-1 size-3 shrink-0 rounded-full border border-muted-foreground/60" aria-label="To do" />
              )}
              <span className={cn(todo.status === "completed" && "text-muted-foreground line-through")}>{todo.content}</span>
            </li>
          ))}
        </ol>
      )}
      {shown.length > 0 && (
        <ul className="space-y-1 text-sm text-muted-foreground" aria-label="Tools used">
          {shown.map((step) => {
            const words = stepWords(step, awaiting);
            return (
              <li key={step.id} className="flex items-start gap-2">
                {/* Its words say it waits: the icon's label would say it twice */}
                <span className="mt-0.5 shrink-0" aria-hidden={words.waits || undefined}>
                  <StepIcon status={step.status} asking={step.name === "ask_user" || waiting} working={working} />
                </span>
                <span className="min-w-0 break-words">
                  {words.text}
                  {step.name === "ask_user" && answersIn(step.output).length > 0 && (
                    <span className="block text-foreground">You answered: {answersIn(step.output).join("; ")}</span>
                  )}
                  {step.task && (
                    <Link href={`/chat/${step.task}`} className="block text-foreground underline-offset-4 hover:underline">
                      Open its chat
                    </Link>
                  )}
                  {step.status === "declined" && reasonIn(step.output) && (
                    <span className="block text-foreground">Your reason: {reasonIn(step.output)}</span>
                  )}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
  if (working) return <div className="mb-3">{list}</div>;
  return (
    <details className="group mb-3 text-sm">
      <summary className="cursor-pointer text-muted-foreground select-none hover:text-foreground">
        {shown.length === 1 && !todos.length ? stepWords(shown[0], awaiting).text : stepsSummary(shown.length, todos.length > 0)}
      </summary>
      {list}
    </details>
  );
}
