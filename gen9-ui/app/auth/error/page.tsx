import type { Metadata } from "next";
import Link from "next/link";

import { Mark } from "@/components/brand/logo";
import { buttonVariants } from "@/components/ui/button";

export const metadata: Metadata = { title: "Sign-in problem" };

const REASONS: Record<string, { title: string; body: string }> = {
  state: {
    title: "This sign-in link doesn’t match your browser.",
    body: "It was opened in a different browser or tab from the one that started signing in. Start again here.",
  },
  expired: { title: "That sign-in took too long.", body: "Sign-in links last 10 minutes. Start again." },
  exchange: {
    title: "We couldn’t finish signing you in.",
    body: "The sign-in service didn’t accept the response. Try again; if it keeps happening, contact your admin.",
  },
  temporarily_unavailable: { title: "Sign-in is unavailable right now.", body: "Try again in a minute." },
};

export default async function AuthError({ searchParams }: PageProps<"/auth/error">) {
  const { reason } = await searchParams;
  const { title, body } = REASONS[String(reason)] ?? {
    title: "Sign-in didn’t complete.",
    body: "Start again. If it keeps happening, contact your admin.",
  };
  return (
    <main className="mx-auto flex min-h-dvh max-w-sm flex-col justify-center px-6 pt-safe pb-safe">
      <Mark className="size-9" />
      <h1 className="mt-8 text-title font-semibold">{title}</h1>
      <p className="mt-3 text-muted-foreground">{body}</p>
      <div className="mt-8 grid gap-2.5">
        <a href="/auth/login" className={buttonVariants({ size: "lg" })}>
          Sign in
        </a>
        <Link href="/" className={buttonVariants({ size: "lg", variant: "ghost" })}>
          Go to the home page
        </Link>
      </div>
    </main>
  );
}
