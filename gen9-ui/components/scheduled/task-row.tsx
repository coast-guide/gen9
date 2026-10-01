"use client";

import { MoreHorizontalIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, useTransition } from "react";
import { toast } from "sonner";

import { deleteTask, pauseTask, runTask, type TaskResult } from "@/app/(app)/scheduled/actions";
import { RelativeTime } from "@/components/relative-time";
import { TaskForm } from "@/components/scheduled/task-form";
import { TriggerDialog } from "@/components/scheduled/trigger-dialog";
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
import type { ScheduledTask } from "@/lib/agent";
import { RUN_WORDS } from "@/lib/status-words";

const MODES = { ask: "Ask before acting", auto: "Act, ask when unsure" };

/** A run's state, and with a rubric its verdict (gen9-agent's outcomes.py). */
function runWords(r: ScheduledTask["runs"][number]) {
  if (r.status !== "success") return RUN_WORDS[r.status ?? ""] ?? "Starting";
  if (r.checking) return "Checking against its rubric";
  const tries = r.graded > 1 ? ` in ${r.graded} tries` : "";
  if (r.outcome === "satisfied") return `Done · meets its rubric${tries}`;
  if (r.outcome === "needs_revision") return `Done · short of its rubric${tries}`;
  if (r.outcome === "failed") return "Done · its rubric doesn’t apply";
  return "Done";
}
const REFRESH_MS = 3000;

export function TaskRow({ task }: { task: ScheduledTask }) {
  const router = useRouter();
  const [pending, start] = useTransition();
  const [editing, setEditing] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [trigger, setTrigger] = useState(false);
  // A run starting or working: the page refreshes itself until it's done or needs the person
  // After Run now, the firing's Activity makes the run a moment later: look until it shows up (30 s at most)
  const [awaiting, setAwaiting] = useState<number | null>(null); // how many runs it had
  useEffect(() => {
    if (awaiting === null) return;
    const timer = setTimeout(() => setAwaiting(null), 30_000);
    return () => clearTimeout(timer);
  }, [awaiting]);
  const busy =
    task.runs.some((r) => r.status === "queued" || r.status === "running" || r.checking) ||
    (awaiting !== null && task.runs.length <= awaiting);
  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(() => router.refresh(), REFRESH_MS);
    return () => clearInterval(timer);
  }, [busy, router]);
  // Its next run due: a look a few seconds after, so the run shows up without a reload (then `busy` follows it)
  const nextAt = task.next_at ? Date.parse(task.next_at) : null;
  useEffect(() => {
    if (nextAt === null) return;
    const wait = nextAt - Date.now() + 5_000;
    if (wait < 0 || wait > 6 * 3_600_000) return;
    const timer = setTimeout(() => router.refresh(), wait);
    return () => clearTimeout(timer);
  }, [nextAt, router]);
  const run = (action: () => Promise<TaskResult>) =>
    start(async () => {
      const result = await action();
      if (result.ok) toast.success(result.message);
      else toast.error(result.message);
    });
  if (editing) return <TaskForm task={task} onDone={() => setEditing(false)} />;
  return (
    <div className="flex items-start gap-3 px-4 py-4 sm:px-5">
      <div className="min-w-0 flex-1 text-sm">
        <p className="flex flex-wrap items-center gap-2 font-medium">
          {task.name}
          {task.status === "paused" && <Badge variant="secondary">Paused</Badge>}
          {task.status === "done" && <Badge variant="outline">Done</Badge>}
          {task.has_trigger && <Badge variant="outline">API trigger on</Badge>}
        </p>
        <p className="text-muted-foreground">
          {task.schedule_words} · {MODES[task.permission_mode]}
          {task.rubric && ` · Checked against a rubric, ${task.max_iterations} ${task.max_iterations === 1 ? "try" : "tries"} at most`}
        </p>
        {task.next_at && (
          <p className="text-muted-foreground">
            Next: <RelativeTime ms={Date.parse(task.next_at)} tense="future" />
          </p>
        )}
        {(task.runs.length > 0 || task.skipped > 0) && (
          <ul aria-label={`Runs of ${task.name}`} className="mt-1.5 grid gap-1">
            {task.runs.map((r) => (
              <li key={r.thread_id}>
                <Link href={`/chat/${r.thread_id}`} className="underline-offset-4 hover:underline">
                  <RelativeTime ms={Date.parse(r.created_at)} />
                </Link>
                <span className={r.status === "waiting" ? "font-medium" : "text-muted-foreground"}> · {runWords(r)}</span>
              </li>
            ))}
            {task.skipped > 0 && (
              <li className="text-muted-foreground">
                {task.skipped} skipped: the last run was still going
              </li>
            )}
          </ul>
        )}
      </div>
      <DropdownMenu>
        <DropdownMenuTrigger render={<Button variant="ghost" size="icon" disabled={pending} aria-label={`Actions for ${task.name}`} />}>
          <HugeiconsIcon icon={MoreHorizontalIcon} strokeWidth={1.8} className="size-5" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-44">
          <DropdownMenuItem
            onClick={() => {
              setAwaiting(task.runs.length);
              run(() => runTask(task.id));
            }}
          >
            Run now
          </DropdownMenuItem>
          {task.schedule.kind !== "once" && (
            <DropdownMenuItem onClick={() => run(() => pauseTask(task.id, task.status !== "paused"))}>
              {task.status === "paused" ? "Resume" : "Pause"}
            </DropdownMenuItem>
          )}
          <DropdownMenuItem onClick={() => setEditing(true)}>Edit</DropdownMenuItem>
          <DropdownMenuItem onClick={() => setTrigger(true)}>API trigger…</DropdownMenuItem>
          <DropdownMenuItem variant="destructive" onClick={() => setConfirm(true)}>
            Delete…
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {trigger && <TriggerDialog task={task} onClose={() => setTrigger(false)} />}
      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {task.name}?</AlertDialogTitle>
            <AlertDialogDescription>It stops running. The chats it made stay.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => run(() => deleteTask(task.id))}>
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
