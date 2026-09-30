"use client";

import { useId, useState, useTransition } from "react";
import { toast } from "sonner";

import { setControls } from "@/app/(app)/settings/actions";
import { Switch } from "@/components/ui/switch";
import type { Controls } from "@/lib/agent";

/**
 * One of what the person lets Gen9 do with their chats (gen9-agent's /v1/me/controls), as a row
 * with a switch that saves at once (docs/design/screens/settings.md, "Memory").
 */
export function ControlSwitch({ control, value, label, hint }: { control: keyof Controls; value: boolean; label: string; hint: string }) {
  const [on, setOn] = useState(value);
  const [pending, start] = useTransition();
  const id = useId();
  return (
    <div className="flex items-center gap-4 border-t px-4 py-4 sm:px-5">
      <div className="min-w-0 flex-1 text-sm">
        <label htmlFor={id} className="font-medium">
          {label}
        </label>
        <p id={`${id}-hint`} className="text-muted-foreground">
          {hint}
        </p>
      </div>
      <Switch
        id={id}
        aria-describedby={`${id}-hint`}
        checked={on}
        disabled={pending}
        onCheckedChange={(next) => {
          setOn(next);
          start(async () => {
            const result = await setControls({ [control]: next });
            if (result?.error) {
              setOn(!next);
              toast.error(result.error);
            } else toast.success("Saved.");
          });
        }}
      />
    </div>
  );
}
