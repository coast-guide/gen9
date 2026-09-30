"use client";

import { useState, useTransition } from "react";
import { toast } from "sonner";

import { addTask, changeTask, type TaskInput } from "@/app/(app)/scheduled/actions";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import type { ScheduledTask, TaskSchedule } from "@/lib/agent";
import { browserZone, sameZone } from "@/lib/time-zones";

const KINDS: { value: TaskSchedule["kind"]; label: string }[] = [
  { value: "once", label: "Once" },
  { value: "hourly", label: "Every hour" },
  { value: "daily", label: "Every day" },
  { value: "weekdays", label: "Every weekday" },
  { value: "weekly", label: "Every week" },
];
const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const field = "h-9 rounded-full border bg-background px-3 text-sm pointer-coarse:h-11";

function today() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** New task, or Edit: a name, what Gen9 should do, when, the mode its runs keep, and what done looks like. */
export function TaskForm({ task, onDone }: { task?: ScheduledTask; onDone: () => void }) {
  const [name, setName] = useState(task?.name ?? "");
  const [prompt, setPrompt] = useState(task?.prompt ?? "");
  const [kind, setKind] = useState<TaskSchedule["kind"]>(task?.schedule.kind ?? "weekdays");
  const [time, setTime] = useState(task?.schedule.time ?? "09:00");
  const [weekday, setWeekday] = useState(task?.schedule.weekday ?? 0);
  const [date, setDate] = useState(task?.schedule.date ?? today());
  const [mode, setMode] = useState<"ask" | "auto">(task?.permission_mode ?? "ask");
  const [rubric, setRubric] = useState(task?.rubric ?? "");
  const [tries, setTries] = useState(task?.max_iterations ?? 3);
  const [problem, setProblem] = useState<string | null>(null);
  const [pending, start] = useTransition();
  // A task keeps its own time zone; where the browser is now, it can switch to (a person who moved)
  // By IANA's name, as the saved task says it (P3-D8)
  const here = browserZone();
  const [zone, setZone] = useState(task?.time_zone ?? here);
  const id = task?.id ?? "new";
  return (
    <form
      aria-label={task ? `Edit ${task.name}` : "New task"}
      className="grid gap-3 px-4 py-4 sm:px-5"
      onSubmit={(e) => {
        e.preventDefault();
        setProblem(null);
        const input: TaskInput = {
          name: name.trim(),
          prompt: prompt.trim(),
          schedule: { kind, time, ...(kind === "weekly" ? { weekday } : {}), ...(kind === "once" ? { date } : {}) },
          time_zone: zone,
          permission_mode: mode,
          rubric: rubric.trim(),
          max_iterations: tries,
        };
        // A name or a message of spaces passes `required`: said here, as gen9-agent would refuse it
        if (!input.name) return setProblem("Name the task.");
        if (!input.prompt) return setProblem("Say what Gen9 should do.");
        start(async () => {
          const result = task ? await changeTask(task.id, input) : await addTask(input);
          if (!result.ok) return setProblem(result.message);
          toast.success(result.message);
          onDone();
        });
      }}
    >
      <label className="grid gap-1.5 text-sm">
        <span className="font-medium">Name</span>
        <Input id={`${id}-name`} dir="auto" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} required placeholder="Morning brief" />
      </label>
      <label className="grid gap-1.5 text-sm">
        <span className="font-medium">What Gen9 should do</span>
        <textarea
          dir="auto"
          id={`${id}-prompt`}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          maxLength={8000}
          required
          rows={3}
          placeholder="A brief on what changed in my field since yesterday, with sources."
          className="min-h-20 rounded-2xl border bg-background px-3 py-2 text-sm"
        />
      </label>
      <div className="flex flex-wrap items-end gap-3">
        <label className="grid gap-1.5 text-sm">
          <span className="font-medium">When</span>
          <select id={`${id}-kind`} value={kind} onChange={(e) => setKind(e.target.value as TaskSchedule["kind"])} className={field}>
            {KINDS.map((k) => (
              <option key={k.value} value={k.value}>
                {k.label}
              </option>
            ))}
          </select>
        </label>
        {kind === "weekly" && (
          <label className="grid gap-1.5 text-sm">
            <span className="font-medium">Day</span>
            <select id={`${id}-weekday`} value={weekday} onChange={(e) => setWeekday(Number(e.target.value))} className={field}>
              {WEEKDAYS.map((d, i) => (
                <option key={d} value={i}>
                  {d}
                </option>
              ))}
            </select>
          </label>
        )}
        {kind === "once" && (
          <label className="grid gap-1.5 text-sm">
            <span className="font-medium">Date</span>
            <Input id={`${id}-date`} type="date" value={date} onChange={(e) => setDate(e.target.value)} required className="w-auto" />
          </label>
        )}
        {kind === "hourly" ? (
          <label className="grid gap-1.5 text-sm">
            <span className="font-medium">Minutes past the hour</span>
            <Input
              id={`${id}-minute`}
              type="number"
              min={0}
              max={59}
              value={Number(time.slice(3))}
              onChange={(e) => setTime(`00:${String(Math.min(59, Math.max(0, Number(e.target.value) || 0))).padStart(2, "0")}`)}
              required
              className="w-24"
            />
          </label>
        ) : (
          <label className="grid gap-1.5 text-sm">
            <span className="font-medium">Time</span>
            <Input id={`${id}-time`} type="time" value={time} onChange={(e) => setTime(e.target.value)} required className="w-auto" />
          </label>
        )}
      </div>
      <p className="text-sm text-muted-foreground">
        In {zone}.
        {!sameZone(zone, here) && (
          <>
            {" "}
            <button type="button" onClick={() => setZone(here)} className="font-medium text-foreground underline underline-offset-4">
              Use {here}, where you are now
            </button>
          </>
        )}
      </p>
      <label className="grid gap-1.5 text-sm">
        <span className="font-medium">While it runs</span>
        <select id={`${id}-mode`} value={mode} onChange={(e) => setMode(e.target.value as "ask" | "auto")} className={field}>
          <option value="ask">Ask before acting</option>
          <option value="auto">Act, ask when unsure</option>
        </select>
      </label>
      {/* The hint outside the label: inside, it was part of the field's name and read twice (P2-J1) */}
      <div className="grid gap-1.5 text-sm">
        <label className="grid gap-1.5">
          <span className="font-medium">
            Done when <span className="font-normal text-muted-foreground">(optional)</span>
          </span>
          <textarea
            dir="auto"
            id={`${id}-rubric`}
            value={rubric}
            onChange={(e) => setRubric(e.target.value)}
            maxLength={8000}
            rows={3}
            aria-describedby={`${id}-rubric-hint`}
            placeholder={"- Every item has a date\n- Each claim links its source"}
            className="min-h-20 rounded-2xl border bg-background px-3 py-2 text-sm"
          />
        </label>
        <span id={`${id}-rubric-hint`} className="text-muted-foreground">
          Criteria its answer must meet. Gen9 checks each run against them and, if one isn’t met, tries again in the same chat.
        </span>
      </div>
      {rubric.trim() && (
        <label className="grid w-fit gap-1.5 text-sm">
          <span className="font-medium">Tries at most</span>
          <Input
            id={`${id}-tries`}
            type="number"
            min={1}
            max={20}
            value={tries}
            onChange={(e) => setTries(Math.min(20, Math.max(1, Number(e.target.value) || 1)))}
            required
            className="w-24"
          />
        </label>
      )}
      {problem && (
        <p role="alert" className="text-sm text-destructive">
          {problem}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="submit" disabled={pending}>
          {pending && <Spinner aria-hidden />} {task ? "Save" : "Schedule"}
        </Button>
        <Button type="button" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
