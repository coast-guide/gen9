"use client";

import { useTransition } from "react";
import { toast } from "sonner";

import { removeApp } from "@/app/(app)/settings/actions";
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

/** Takes back an app's access, after asking: it's signed out and must ask again to come back. */
export function RemoveApp({ clientId, name }: { clientId: string; name: string }) {
  const [pending, start] = useTransition();
  return (
    <AlertDialog>
      <AlertDialogTrigger render={<Button variant="ghost" className="text-destructive hover:text-destructive" aria-label={`Remove access for ${name}`} />}>
        Remove access
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Remove access for {name}?</AlertDialogTitle>
          <AlertDialogDescription>It’s signed out of your account, and asks you again if it wants to come back.</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={pending}
            onClick={() =>
              start(async () => {
                const result = await removeApp(clientId);
                if (result.error) toast.error(result.error);
                else toast.success(`Removed access for ${name}.`);
              })
            }
          >
            Remove access
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
