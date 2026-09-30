"use client";

import { useState, useTransition } from "react";

import { deleteAccount } from "@/app/(app)/settings/actions";
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
import { Button, buttonVariants } from "@/components/ui/button";
import { Field, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";

/**
 * Delete account: the user types their email, and must have signed in within the last 5 minutes;
 * otherwise the dialog sends them to sign in again and reopens here (?delete=1).
 */
export function DeleteAccount({ email, recentlySignedIn, open }: { email: string; recentlySignedIn: boolean; open: boolean }) {
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [needsSignIn, setNeedsSignIn] = useState(!recentlySignedIn);
  const [pending, start] = useTransition();
  const matches = confirmation.trim().toLowerCase() === email.toLowerCase();

  return (
    <AlertDialog defaultOpen={open}>
      <AlertDialogTrigger render={<Button variant="outline" className="text-destructive hover:text-destructive" />}>
        Delete account
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete your Gen9 account?</AlertDialogTitle>
          <AlertDialogDescription>
            This permanently deletes your account and all it holds: your chats, memory, scheduled tasks, connectors and sign-in methods. It can’t be undone. Backups made before keep a copy until they’re deleted, and restoring one deletes it again.
          </AlertDialogDescription>
        </AlertDialogHeader>

        {needsSignIn ? (
          <p className="text-sm text-muted-foreground">For your security, sign in again first. You’ll come back here.</p>
        ) : (
          <Field data-invalid={error ? true : undefined}>
            <FieldLabel htmlFor="delete-confirmation">
              Type <span className="font-semibold text-foreground">{email}</span> to confirm
            </FieldLabel>
            <Input
              id="delete-confirmation"
              type="email"
              autoComplete="off"
              spellCheck={false}
              value={confirmation}
              onChange={(event) => {
                setConfirmation(event.target.value);
                setError(null);
              }}
              aria-invalid={error ? true : undefined}
            />
            {error && <FieldError>{error}</FieldError>}
          </Field>
        )}

        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          {needsSignIn ? (
            <a href="/auth/login?reauth=1&returnTo=/settings%3Fdelete%3D1" className={buttonVariants()}>
              Sign in again
            </a>
          ) : (
            <AlertDialogAction
              variant="destructive"
              disabled={!matches || pending}
              onClick={() =>
                start(async () => {
                  const result = await deleteAccount(confirmation);
                  if (!result) return; // deleted: the action redirects
                  setError(result.error);
                  if (result.reauth) setNeedsSignIn(true);
                })
              }
            >
              {pending ? "Deleting…" : "Delete account"}
            </AlertDialogAction>
          )}
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
