"use client";

import { MoreHorizontalIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useState, useTransition } from "react";
import { toast } from "sonner";

import {
  type ActionResult,
  deleteUser,
  sendPasswordReset,
  setAdmin,
  setEnabled,
  signOutUser,
  unlockUser,
} from "@/app/(app)/admin/users/actions";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Field, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import type { AdminUser } from "@/lib/agent";

export function UserActions({ user, isSelf, deleting: reopen = false }: { user: AdminUser; isSelf: boolean; deleting?: boolean }) {
  const [pending, start] = useTransition();
  const run = (action: () => Promise<ActionResult>) =>
    start(async () => {
      const result = await action();
      if (result.ok) toast.success(result.message);
      else toast.error(result.message);
    });
  const who = user.email ?? "this user";
  const [deleting, setDeleting] = useState(reopen);
  // A change to someone's access asks first, as Google Workspace (suspend, reactivate), Okta
  // (deactivate) and GitHub (change role) do
  const [changing, setChanging] = useState<AccessChange | null>(null);
  const adminChange: AccessChange = user.is_admin
    ? {
        title: `Remove admin access for ${who}?`,
        description: "They keep their account and chats, and lose Users, Plugins and the audit log at once.",
        confirm: "Remove admin access",
        destructive: true,
        action: () => setAdmin(user.id, false),
      }
    : {
        title: `Make ${who} an admin?`,
        description:
          "They can manage everyone’s accounts and the plugins, and read the audit log, as you can. Admins need a second step: without an authenticator app or a passkey, they’re signed out and set one up at their next sign-in.",
        confirm: "Make admin",
        destructive: false,
        action: () => setAdmin(user.id, true),
      };
  const accountChange: AccessChange = user.enabled
    ? {
        title: `Disable ${who}?`,
        description: "They can’t sign in, they’re signed out on every device, and anything Gen9 is doing for them stops. Their chats stay, and you can enable the account again.",
        confirm: "Disable account",
        destructive: true,
        action: () => setEnabled(user.id, false),
      }
    : {
        title: `Enable ${who}?`,
        description: "They can sign in again.",
        confirm: "Enable account",
        destructive: false,
        action: () => setEnabled(user.id, true),
      };

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger render={<Button variant="ghost" size="icon" disabled={pending} aria-label={`Actions for ${who}`} />}>
          <HugeiconsIcon icon={MoreHorizontalIcon} strokeWidth={1.8} className="size-5" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-56">
          {user.locked && (
            <DropdownMenuItem onClick={() => run(() => unlockUser(user.id))}>Unlock sign-in</DropdownMenuItem>
          )}
          <DropdownMenuItem onClick={() => run(() => sendPasswordReset(user.id))}>Send password reset</DropdownMenuItem>
          <DropdownMenuItem onClick={() => run(() => signOutUser(user.id))}>Sign out everywhere</DropdownMenuItem>
          {!isSelf && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={() => setChanging(adminChange)}>
                {user.is_admin ? "Remove admin access…" : "Make admin…"}
              </DropdownMenuItem>
              <DropdownMenuItem variant={user.enabled ? "destructive" : "default"} onClick={() => setChanging(accountChange)}>
                {user.enabled ? "Disable account…" : "Enable account…"}
              </DropdownMenuItem>
              <DropdownMenuItem variant="destructive" onClick={() => setDeleting(true)}>
                Delete user…
              </DropdownMenuItem>
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
      {deleting && (
        <DeleteUserDialog
          user={user}
          onClose={() => {
            setDeleting(false);
            // Back from signing in again, the address still says which dialog to open: not on a reload
            const url = new URL(window.location.href);
            if (url.searchParams.has("delete")) {
              url.searchParams.delete("delete");
              window.history.replaceState(null, "", url);
            }
          }}
        />
      )}
      {changing && <AccessChangeDialog change={changing} onClose={() => setChanging(null)} />}
    </>
  );
}

type AccessChange = {
  title: string;
  description: string;
  confirm: string;
  destructive: boolean;
  action: () => Promise<ActionResult>;
};

/** Disable or enable an account, grant or remove admin access: what it does, then the admin decides. */
function AccessChangeDialog({ change, onClose }: { change: AccessChange; onClose: () => void }) {
  const [pending, start] = useTransition();
  return (
    <AlertDialog defaultOpen onOpenChange={(open) => !open && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{change.title}</AlertDialogTitle>
          <AlertDialogDescription>{change.description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant={change.destructive ? "destructive" : "default"}
            disabled={pending}
            onClick={() =>
              start(async () => {
                const result = await change.action();
                if (result.ok) toast.success(result.message);
                else toast.error(result.message);
                onClose();
              })
            }
          >
            {change.confirm}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

/**
 * Delete a user: type their email to confirm; needs the admin's sign-in from the last 5 minutes,
 * or signing in again, which comes back to this dialog (?delete=<id>, as Settings' ?delete=1).
 */
function DeleteUserDialog({ user, onClose }: { user: AdminUser; onClose: () => void }) {
  const confirmWith = user.email ?? "delete";
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [needsSignIn, setNeedsSignIn] = useState(false);
  const [pending, start] = useTransition();
  const matches = confirmation.trim().toLowerCase() === confirmWith.toLowerCase();

  return (
    <AlertDialog defaultOpen onOpenChange={(open) => !open && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete {user.email ?? "this user"}?</AlertDialogTitle>
          <AlertDialogDescription>
            This permanently deletes their account and all it holds (chats, memory, scheduled tasks, connectors, sign-in methods), and signs them out everywhere. It can’t be
            undone. Backups made before keep a copy until they’re deleted, and restoring one deletes it again.
          </AlertDialogDescription>
        </AlertDialogHeader>
        {needsSignIn ? (
          <p className="text-sm text-muted-foreground">For your security, sign in again first. You’ll come back to this.</p>
        ) : (
          <Field data-invalid={error ? true : undefined}>
            <FieldLabel htmlFor={`delete-${user.id}`}>
              Type <span className="font-semibold text-foreground">{confirmWith}</span> to confirm
            </FieldLabel>
            <Input
              id={`delete-${user.id}`}
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
            <a
              href={`/auth/login?reauth=1&returnTo=${encodeURIComponent(`/admin/users?q=${encodeURIComponent(user.email ?? "")}&delete=${user.id}`)}`}
              className={buttonVariants()}
            >
              Sign in again
            </a>
          ) : (
            <AlertDialogAction
              variant="destructive"
              disabled={!matches || pending}
              onClick={() =>
                start(async () => {
                  const result = await deleteUser(user.id);
                  if (result.ok) {
                    toast.success(result.message);
                    onClose();
                    return;
                  }
                  setError(result.message);
                  if (result.reauth) setNeedsSignIn(true);
                })
              }
            >
              {pending ? "Deleting…" : "Delete user"}
            </AlertDialogAction>
          )}
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
