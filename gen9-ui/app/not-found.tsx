import type { Metadata } from "next";
import Link from "next/link";

import { Mark } from "@/components/brand/logo";
import { buttonVariants } from "@/components/ui/button";

export const metadata: Metadata = { title: "Not found" };

// Any address Gen9 doesn't have, and a chat that was deleted or isn't yours (the chat page's
// notFound()): the same words for both, so a chat's existence isn't given away
export default function NotFound() {
  return (
    <main className="mx-auto flex min-h-dvh max-w-sm flex-col justify-center px-6 pt-safe pb-safe">
      <Mark className="size-9" />
      <h1 className="mt-8 text-headline font-semibold">There’s nothing here.</h1>
      <p className="mt-3 text-muted-foreground">
        The link may be mistyped, or what it pointed to was deleted.
      </p>
      <div className="mt-8 grid gap-2.5">
        <Link href="/chat" className={buttonVariants({ size: "lg" })}>
          Go to your chats
        </Link>
        <Link href="/" className={buttonVariants({ size: "lg", variant: "ghost" })}>
          Go to the home page
        </Link>
      </div>
    </main>
  );
}
