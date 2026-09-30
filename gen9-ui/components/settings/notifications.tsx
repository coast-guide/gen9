"use client";

import { useId, useRef, useState } from "react";
import { toast } from "sonner";

import { setNotifications } from "@/app/(app)/settings/actions";
import type { Notifications } from "@/lib/agent";

const CHOICES: { value: Notifications["email"]; label: string }[] = [
  { value: "all", label: "When a task finishes or needs me" },
  { value: "needs_you", label: "Only when a task needs me" },
  { value: "never", label: "Never" },
];

/** Which emails the person gets about their scheduled tasks (gen9-agent's notices.py). Native
 *  radios, not a select: every choice shows, and a long one wraps at 200% text or on a phone
 *  instead of being cut off (manual-e2e.md, P3-D1). */
export function NotificationSettings({ notifications }: { notifications: Notifications }) {
  const [email, setEmail] = useState(notifications.email);
  const about = useId();
  const saved = useRef(notifications.email); // what Gen9 has
  const wanted = useRef(notifications.email); // the latest choice
  const saving = useRef(false);

  // The latest choice saved, one save at a time. The radios stay enabled meanwhile: disabling
  // them took keyboard focus away after the first arrow key (P3-D1), and Next.js dispatching
  // actions one at a time is, in its docs' words, an implementation detail
  async function save() {
    if (saving.current) return;
    saving.current = true;
    try {
      while (wanted.current !== saved.current) {
        const next = wanted.current;
        const result = await setNotifications(next);
        if (result?.error) {
          wanted.current = saved.current;
          setEmail(saved.current);
          toast.error(result.error);
          return;
        }
        saved.current = next;
      }
      toast.success("Saved.");
    } finally {
      saving.current = false;
    }
  }

  return (
    <fieldset
      aria-describedby={about}
      disabled={!notifications.available}
      className="min-w-0 px-4 py-4 sm:px-5"
    >
      {/* Floated, the legend sits inside the padding like any line (a fieldset puts its own legend
          on its border); it still names the group */}
      <legend className="float-left w-full text-sm font-medium">Email me</legend>
      <p id={about} className="clear-left text-sm text-muted-foreground">
        {notifications.available
          ? "About your scheduled tasks. The email says what happened and links to the chat; the answer stays in Gen9."
          : "This Gen9 doesn’t send emails yet (an admin sets SMTP_URL)."}
      </p>
      <div className="mt-3 grid gap-1">
        {CHOICES.map((c) => (
          <label
            key={c.value}
            className="flex min-h-9 cursor-pointer items-center gap-3 text-sm has-disabled:cursor-default has-disabled:opacity-60 pointer-coarse:min-h-11"
          >
            <input
              type="radio"
              name="email"
              value={c.value}
              checked={email === c.value}
              onChange={() => {
                wanted.current = c.value;
                setEmail(c.value);
                void save();
              }}
              className="size-4 shrink-0 accent-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
            />
            {c.label}
          </label>
        ))}
      </div>
    </fieldset>
  );
}
