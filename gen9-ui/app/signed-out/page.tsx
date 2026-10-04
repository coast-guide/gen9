import type { Metadata } from "next";
import Link from "next/link";

import { Mark } from "@/components/brand/logo";
import { buttonVariants } from "@/components/ui/button";

export const metadata: Metadata = { title: "Signed out" };

export default async function SignedOut({ searchParams }: PageProps<"/signed-out">) {
  const { reason } = await searchParams;
  // "deleting": the API answered 202, so the data isn't all gone yet (gen9-agent's deletions.py)
  const deleted = reason === "deleted" || reason === "deleting";
  const [title, text] =
    reason === "deleted"
      ? ["Your account was deleted.", "Your chats, memory, scheduled tasks, connectors and sign-in methods are gone from Gen9."]
      : reason === "deleting"
        ? [
            "Your account is being deleted.",
            "Nobody can sign in to it now. Your chats, memory, scheduled tasks, connectors and sign-in methods are being removed from Gen9, which finishes on its own.",
          ]
        : reason === "everywhere"
          ? [
              "You’re signed out everywhere.",
              "Your Gen9 sessions have ended on every browser and device. A terminal signed in to Gen9 stops within 5 minutes.",
            ]
          : ["You’re signed out.", "Your Gen9 session has ended on this device."];
  return (
    <main className="mx-auto flex min-h-dvh max-w-sm flex-col justify-center px-6 pt-safe pb-safe">
      <Mark className="size-9" />
      <h1 className="mt-8 text-headline font-semibold">{title}</h1>
      <p className="mt-3 text-muted-foreground">{text}</p>
      <div className="mt-8 grid gap-2.5">
        {deleted ? (
          <a href="/auth/login?intent=signup" className={buttonVariants({ size: "lg" })}>
            Create a new account
          </a>
        ) : (
          <a href="/auth/login" className={buttonVariants({ size: "lg" })}>
            Sign in again
          </a>
        )}
        <Link href="/" className={buttonVariants({ size: "lg", variant: "ghost" })}>
          Go to the home page
        </Link>
      </div>
    </main>
  );
}
