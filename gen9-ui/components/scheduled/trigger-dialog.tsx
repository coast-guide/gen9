"use client";

import { useState, useTransition } from "react";
import { toast } from "sonner";

import { makeTrigger, revokeTrigger } from "@/app/(app)/scheduled/actions";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Input } from "@/components/ui/input";
import type { ScheduledTask } from "@/lib/agent";

/** A task's API trigger: its address, and a token made (shown once), made again or revoked. */
export function TriggerDialog({ task, onClose }: { task: ScheduledTask; onClose: () => void }) {
  const [token, setToken] = useState<string | null>(null);
  const [pending, start] = useTransition();
  const make = () =>
    start(async () => {
      const result = await makeTrigger(task.id);
      if (result.ok) setToken(result.token);
      else toast.error(result.message);
    });
  const revoke = () =>
    start(async () => {
      const result = await revokeTrigger(task.id);
      if (result.ok) {
        toast.success(result.message);
        setToken(null);
      } else toast.error(result.message);
    });
  const curl = `curl -X POST ${task.fire_url} \\\n  -H "Authorization: Bearer ${token ?? "<token>"}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"text": "…"}'`;
  return (
    <AlertDialog defaultOpen onOpenChange={(open) => !open && onClose()}>
      <AlertDialogContent className="sm:max-w-lg">
        <AlertDialogHeader>
          <AlertDialogTitle>API trigger for {task.name}</AlertDialogTitle>
          <AlertDialogDescription>
            POST here with its token to run it now, in a new chat. Text you send reaches the run as data; the task’s message says what to do with
            it.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <label className="grid gap-1.5 text-sm">
          <span className="font-medium">Address</span>
          <Input readOnly value={task.fire_url} onFocus={(e) => e.target.select()} />
        </label>
        {token && (
          <div className="grid gap-2 text-sm">
            <label className="grid gap-1.5">
              <span className="font-medium">Token</span>
              <div className="flex gap-2">
                <Input id={`${task.id}-token`} readOnly value={token} onFocus={(e) => e.target.select()} className="font-mono" />
                <Button type="button" variant="ghost" onClick={() => navigator.clipboard.writeText(token).then(() => toast.success("Copied."))}>
                  Copy
                </Button>
              </div>
            </label>
            <p className="text-muted-foreground">Gen9 keeps only a fingerprint of it: copy it now.</p>
            {/* Focusable, so a keyboard can scroll it */}
            <pre tabIndex={0} aria-label="A curl example" className="overflow-x-auto rounded-xl bg-muted p-3 text-xs">
              {curl}
            </pre>
          </div>
        )}
        <div className="flex flex-wrap gap-2">
          <Button type="button" disabled={pending} onClick={make}>
            {task.has_trigger || token ? "Make a new token" : "Make a token"}
          </Button>
          {(task.has_trigger || token) && (
            <Button type="button" variant="ghost" disabled={pending} onClick={revoke}>
              Revoke
            </Button>
          )}
        </div>
        {(task.has_trigger || token) && !token && (
          <p className="text-sm text-muted-foreground">A new token stops the current one working.</p>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel>Done</AlertDialogCancel>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
