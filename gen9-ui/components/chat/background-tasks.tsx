"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { ApprovalCard, type Decision } from "@/components/chat/approval";
import { type ElicitationAnswer, ElicitationCard } from "@/components/chat/elicitation";
import { QuestionCard } from "@/components/chat/question";
import { RetryCard } from "@/components/chat/retry";
import type { BackgroundTask, InputRequest } from "@/lib/agent";
import { RUN_WORDS, unfinished } from "@/lib/status-words";
import { refusal } from "@/lib/refusal";

const REFRESH_MS = 3000;

/**
 * The tasks this chat's agent started in the background (gen9-agent's background.py;
 * docs/design/screens/chat.md, "In the background"): each opens its own chat. While one is
 * unfinished, or its end not yet told to the chat, the list refreshes itself; `after` changes
 * when the chat's own run ends, which may have started one. `onTold`: a task's notice reached
 * the chat, as a run of its own for the chat to follow.
 */
export function BackgroundTaskList({
  threadId,
  initial,
  after,
  onTold,
}: {
  threadId: string;
  initial: BackgroundTask[];
  after: number;
  onTold: () => void;
}) {
  const [tasks, setTasks] = useState(initial);
  const busy = tasks.some((t) => unfinished(t.status) || !t.told);
  const told = tasks.filter((t) => t.told).length;
  const toldBefore = useRef(told);
  useEffect(() => {
    if (told > toldBefore.current) onTold();
    toldBefore.current = told;
  }, [told, onTold]);
  const [refreshed, setRefreshed] = useState(0);
  /** Answer what a task's run asks, at that run (a question, Allow or Deny, a form, Retry). */
  async function answer(
    task: BackgroundTask,
    request: InputRequest,
    body: { answers: string[] } | { decisions: Decision[] } | { responses: Record<string, ElicitationAnswer> } | { retry: true },
  ): Promise<string | null> {
    const response = await fetch(`/api/threads/${task.id}/runs/${task.run_id}/inputs/${request.id}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).catch(() => null);
    if (response?.ok || response?.status === 409) {
      setRefreshed((n) => n + 1);
      return null;
    }
    if (response?.status === 422) return refusal((await response.json().catch(() => ({})))?.detail, "Check your answers.");
    return "Couldn’t send your answer. Try again.";
  }
  useEffect(() => {
    let stopped = false;
    const load = async () => {
      const response = await fetch(`/api/threads/${threadId}/tasks`).catch(() => null);
      if (!stopped && response?.ok) setTasks(await response.json());
    };
    void load();
    if (!busy) return () => void (stopped = true);
    const timer = setInterval(load, REFRESH_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [threadId, busy, after, refreshed]);
  if (!tasks.length) return null;
  return (
    <section aria-label="In the background" className="mb-3 rounded-2xl border px-3 py-2 text-sm">
      <h2 className="text-xs font-medium text-muted-foreground">In the background</h2>
      <ul className="mt-1 grid gap-1">
        {tasks.map((task) => (
          <li key={task.id} className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
            {unfinished(task.status) && task.status !== "waiting" && <span className="size-1.5 shrink-0 animate-pulse rounded-full bg-leaf" aria-hidden />}
            <Link href={`/chat/${task.id}`} className="min-w-0 truncate underline-offset-4 hover:underline">
              {task.title}
            </Link>
            <span className={task.status === "waiting" ? "shrink-0 font-medium" : "shrink-0 text-muted-foreground"}>· {RUN_WORDS[task.status ?? ""] ?? "Starting"}</span>
            {/* What it waits for, answered here as in its own chat (Anthropic's multiagent sessions cross-post these) */}
            {task.status === "waiting" && task.requests.length > 0 && (
              <div className="basis-full" aria-label={`${task.title} asks`}>
                {task.requests.map((request) =>
                  request.kind === "question" ? (
                    <QuestionCard key={request.id} request={request} answer={(answers) => answer(task, request, { answers })} takeFocus={false} />
                  ) : request.kind === "approval" ? (
                    <ApprovalCard key={request.id} request={request} decide={(decisions) => answer(task, request, { decisions })} takeFocus={false} />
                  ) : request.kind === "elicitation" ? (
                    <ElicitationCard key={request.id} request={request} respond={(responses) => answer(task, request, { responses })} takeFocus={false} />
                  ) : (
                    <RetryCard key={request.id} request={request} retry={() => answer(task, request, { retry: true })} takeFocus={false} />
                  ),
                )}
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
