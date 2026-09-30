"use client";

import Link from "next/link";

import { Mark } from "@/components/brand/logo";
import { Button, buttonVariants } from "@/components/ui/button";

// A page that failed on the server, the app's layout included (its session check needs Keycloak
// to refresh a token): Gen9's words instead of the framework's. The error itself stays in the
// server's log, under the digest (Next's error.js convention; `retry` re-fetches the page)
export default function PageError({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  return (
    <main className="mx-auto flex min-h-dvh max-w-sm flex-col justify-center px-6 pt-safe pb-safe">
      <title>Something went wrong – Gen9</title>
      <Mark className="size-9" />
      <h1 className="mt-8 text-headline font-semibold">Gen9 couldn’t load this page.</h1>
      <p className="mt-3 text-muted-foreground">
        Part of Gen9 may be restarting. Try again in a minute; nothing you saved is lost.
      </p>
      <div className="mt-8 grid gap-2.5">
        <Button size="lg" onClick={() => retry()}>
          Try again
        </Button>
        <Link href="/" className={buttonVariants({ size: "lg", variant: "ghost" })}>
          Go to the home page
        </Link>
      </div>
      {error.digest && <p className="mt-6 text-xs text-muted-foreground">Error {error.digest}</p>}
    </main>
  );
}
