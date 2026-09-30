import type { Metadata } from "next";

import { NewTask } from "@/components/scheduled/new-task";
import { TaskRow } from "@/components/scheduled/task-row";
import { agentJson, type ScheduledTask } from "@/lib/agent";
import { requireSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "Scheduled" };

export default async function ScheduledPage() {
  const session = await requireSession("/scheduled");
  const tasks = await agentJson<ScheduledTask[]>(session, "/v1/tasks");
  return (
    <main className="mx-auto w-full max-w-2xl px-5 pt-8 pb-16 sm:px-8 lg:pt-14">
      <h1 className="text-headline font-semibold">Scheduled</h1>
      <p className="mt-1 text-muted-foreground">Gen9 runs these on its own, each time in a new chat.</p>
      <div className="mt-8 divide-y rounded-2xl border bg-card">
        {tasks.length > 0 && (
          <ul aria-label="Scheduled tasks" className="divide-y">
            {tasks.map((task) => (
              <li key={task.id}>
                <TaskRow task={task} />
              </li>
            ))}
          </ul>
        )}
        <NewTask empty={tasks.length === 0} />
      </div>
    </main>
  );
}
