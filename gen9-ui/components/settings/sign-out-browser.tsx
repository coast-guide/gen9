"use client";

import { useTransition } from "react";
import { toast } from "sonner";

import { signOutBrowser, signOutOtherBrowsers } from "@/app/(app)/settings/actions";
import { Button } from "@/components/ui/button";

/** Signs one other browser out; no confirmation, since that browser can simply sign in again. */
export function SignOutBrowser({ id, name }: { id: string; name: string }) {
  const [pending, start] = useTransition();
  return (
    <Button
      variant="ghost"
      className="text-destructive hover:text-destructive"
      disabled={pending}
      aria-label={`Sign out ${name}`}
      onClick={() =>
        start(async () => {
          try {
            await signOutBrowser(id);
            toast.success(`Signed out ${name}.`);
          } catch {
            toast.error(`Couldn’t sign out ${name}. Try again.`);
          }
        })
      }
    >
      Sign out
    </Button>
  );
}

export function SignOutOtherBrowsers({ count }: { count: number }) {
  const [pending, start] = useTransition();
  return (
    <Button
      variant="outline"
      disabled={pending}
      onClick={() =>
        start(async () => {
          try {
            await signOutOtherBrowsers();
            toast.success(count === 1 ? "Signed out 1 other session." : `Signed out ${count} other sessions.`);
          } catch {
            toast.error("Couldn’t sign out the other sessions. Try again.");
          }
        })
      }
    >
      Sign out other sessions
    </Button>
  );
}
