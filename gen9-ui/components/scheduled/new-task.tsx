"use client";

import { useState } from "react";

import { TaskForm } from "@/components/scheduled/task-form";
import { Button } from "@/components/ui/button";

/** "New task", opening the form in place. */
export function NewTask({ empty }: { empty: boolean }) {
  const [open, setOpen] = useState(false);
  if (open) return <TaskForm onDone={() => setOpen(false)} />;
  return (
    <div className="grid gap-2 px-4 py-4 sm:px-5">
      {empty && (
        <p className="text-sm text-muted-foreground">
          Nothing scheduled. For example: every weekday at 9, a brief on what changed in your field.
        </p>
      )}
      <Button type="button" variant={empty ? "default" : "ghost"} className="w-fit" onClick={() => setOpen(true)}>
        New task
      </Button>
    </div>
  );
}
