"use client";

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import type { RetryRequest } from "@/lib/agent";

/**
 * A run whose turn failed in a way someone can fix, waiting for Retry (docs/design/screens/chat.md,
 * "Retry"). Retry continues the same answer from where it was; Stop, in the composer, gives up.
 * `retry` returns what went wrong, or null.
 */
export function RetryCard({
  request,
  retry,
  takeFocus,
}: {
  request: RetryRequest;
  retry: () => Promise<string | null>;
  takeFocus: boolean;
}) {
  const [sending, setSending] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const button = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (takeFocus) button.current?.focus();
  }, [takeFocus]);

  return (
    <section aria-label="Gen9 couldn't finish" className="mb-4 grid gap-3 rounded-2xl border bg-card p-4 shadow-sm sm:p-5">
      <p className="text-sm font-medium">Gen9 couldn’t finish</p>
      <p className="text-sm">{request.error}</p>
      <div className="flex flex-wrap items-center justify-end gap-3">
        {problem && (
          <p role="alert" className="mr-auto text-sm text-destructive">
            {problem}
          </p>
        )}
        <Button
          ref={button}
          type="button"
          disabled={sending}
          onClick={async () => {
            setSending(true);
            setProblem(null);
            const wrong = await retry();
            if (wrong) {
              setProblem(wrong);
              setSending(false);
            }
          }}
        >
          {sending && <Spinner aria-hidden />}
          Retry
        </Button>
      </div>
    </section>
  );
}
