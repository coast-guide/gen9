"use client";

import { useId, useState, useTransition } from "react";
import { toast } from "sonner";

import { clearMemory, saveMemory } from "@/app/(app)/settings/actions";
import { Markdown } from "@/components/chat/markdown";
import { RelativeTime } from "@/components/relative-time";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Field, FieldError, FieldLabel } from "@/components/ui/field";
import { Textarea } from "@/components/ui/textarea";

// gen9-agent's MAX_MEMORY_CHARS
const MAX = 16_000;

/**
 * What Gen9 remembers about the person: read, edit, clear (docs/design/screens/settings.md,
 * "Memory"). The agent keeps it as one Markdown file (gen9-agent's memory.py).
 */
export function MemorySettings({ content, updatedAtMs }: { content: string; updatedAtMs: number | null }) {
  const [editing, setEditing] = useState(false);
  // What's shown: the saved text at once, before the refreshed page brings it back as `content`
  const [current, setCurrent] = useState(content);
  const [draft, setDraft] = useState(content);
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();
  const fieldId = useId();

  if (editing) {
    return (
      <form
        className="grid gap-3 px-4 py-4 sm:px-5"
        onSubmit={(event) => {
          event.preventDefault();
          start(async () => {
            const result = await saveMemory(draft);
            if (result?.error) return setError(result.error);
            setCurrent(draft.trim());
            setEditing(false);
            toast.success("Memory saved.");
          });
        }}
      >
        <Field data-invalid={error ? true : undefined}>
          <FieldLabel htmlFor={fieldId}>What Gen9 remembers</FieldLabel>
          <Textarea
            id={fieldId}
            value={draft}
            maxLength={MAX}
            rows={8}
            autoFocus
            className="font-mono text-sm"
            aria-describedby={`${fieldId}-count`}
            aria-invalid={error ? true : undefined}
            onChange={(event) => {
              setDraft(event.target.value);
              setError(null);
            }}
          />
          <p id={`${fieldId}-count`} aria-live="polite" className="text-xs text-muted-foreground">
            {draft.length.toLocaleString()} of {MAX.toLocaleString()} characters
          </p>
          {error && <FieldError>{error}</FieldError>}
        </Field>
        <div className="flex justify-end gap-2">
          <Button
            type="button"
            variant="ghost"
            disabled={pending}
            onClick={() => {
              setDraft(current);
              setError(null);
              setEditing(false);
            }}
          >
            Cancel
          </Button>
          <Button type="submit" disabled={pending}>
            Save
          </Button>
        </div>
      </form>
    );
  }

  return (
    <div className="grid gap-3 px-4 py-4 sm:px-5">
      {current ? (
        <div className="text-sm">
          <Markdown>{current}</Markdown>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">Nothing yet. Tell Gen9 to remember something in a chat, or add it here.</p>
      )}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">{updatedAtMs && <>Updated <RelativeTime ms={updatedAtMs} /></>}</p>
        <div className="flex gap-2">
          <Button
            variant="outline"
            onClick={() => {
              setDraft(current);
              setEditing(true);
            }}
          >
            {current ? "Edit" : "Add"}
          </Button>
          {current && (
            <AlertDialog>
              <AlertDialogTrigger render={<Button variant="outline" className="text-destructive hover:text-destructive" />}>
                Clear
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Clear everything Gen9 remembers about you?</AlertDialogTitle>
                  <AlertDialogDescription>Gen9 won’t know any of it, in new chats or ones you’ve already started. You can’t undo this.</AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction
                    variant="destructive"
                    disabled={pending}
                    onClick={() =>
                      start(async () => {
                        const result = await clearMemory();
                        if (result?.error) return void toast.error(result.error);
                        setCurrent("");
                        toast.success("Memory cleared.");
                      })
                    }
                  >
                    Clear memory
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          )}
        </div>
      </div>
    </div>
  );
}
