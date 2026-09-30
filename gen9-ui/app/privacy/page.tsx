import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";

import { Mark } from "@/components/brand/logo";
import { env } from "@/lib/env";

export const metadata: Metadata = { title: "Privacy" };

/**
 * What a person is told about their personal data (GDPR Art. 13), readable before signing up. The
 * organization running Gen9 is the controller: it names itself and its contacts in settings
 * (PRIVACY_*), or points to its own notice (PRIVACY_NOTICE_URL). The rest is what Gen9 itself does,
 * as its README and docs/plans/manual-e2e.md (P4-E5, P5-B3) record it.
 */
export default function Privacy() {
  const settings = env();
  if (settings.PRIVACY_NOTICE_URL) redirect(settings.PRIVACY_NOTICE_URL);
  const controller = settings.PRIVACY_CONTROLLER;
  const contact = settings.PRIVACY_CONTACT;
  return (
    <main className="mx-auto max-w-2xl px-6 py-12 pt-safe pb-safe">
      <Mark className="size-9" />
      <h1 className="mt-8 text-headline font-semibold">Your data in Gen9</h1>
      <p className="mt-3 text-muted-foreground">
        Gen9 is an AI system that does the work you give it. This page says what it keeps about you, who else sees it, for how long,
        and what you can do about it.
      </p>

      <Section title="Who is responsible">
        {controller ? (
          <p>
            {controller} runs this Gen9 and decides what happens to your data.
            {contact ? <> Write to {contact} about anything on this page.</> : null}
          </p>
        ) : (
          <p>The organization running this Gen9 hasn’t named itself here yet. Ask whoever gave you access.</p>
        )}
        {settings.PRIVACY_DPO ? <p>Its data protection officer: {settings.PRIVACY_DPO}.</p> : null}
      </Section>

      <Section title="What Gen9 keeps, and why">
        <ul className="list-disc space-y-1.5 pl-5">
          <li>Your account: your name, email address and sign-in methods, so you can sign in.</li>
          <li>
            What you ask and what Gen9 answers, the files you attach and the files it makes, what it remembers about you, your scheduled
            tasks and the services you connect, so it can do the work you ask for.
          </li>
          <li>A record of sign-ins, security actions and model usage, to keep your account secure and each person within a spending limit.</li>
        </ul>
        <p>
          The first two are needed to give you the service you signed up for; the third is in the legitimate interest of keeping Gen9 secure
          and its costs bounded (GDPR Art. 6(1)(b) and (f)), unless the organization running it says otherwise above.
        </p>
      </Section>

      <Section title="Who else receives it">
        <ul className="list-disc space-y-1.5 pl-5">
          <li>
            <strong>AI model providers.</strong> What you ask, the chat around it and what you attach go to the language models that write the
            answers, through OpenRouter, which passes them to a provider such as OpenAI. To make your chats searchable by meaning, each
            question and answer, and what you search for by meaning, also go to an embedding model the same way. Gen9 asks OpenRouter to
            use only providers that don’t train on your data. OpenAI keeps requests for a time to detect abuse. These providers may be
            outside the EU.
          </li>
          <li>
            <strong>Search engines.</strong> When Gen9 searches the web for you, the search words go to web search services.
          </li>
          <li>
            <strong>Email.</strong> If you choose emails about your scheduled tasks, they go through the organization’s mail service.
          </li>
          <li>
            <strong>Services you connect.</strong> A connected service receives what Gen9 sends it on your behalf, when you allow it.
          </li>
        </ul>
        <p>Everything else, including Gen9’s records of each answer, stays on the servers that run this Gen9.</p>
      </Section>

      <Section title="For how long">
        <ul className="list-disc space-y-1.5 pl-5">
          <li>Your chats, files, memory, tasks and connections: until you delete them, or your account.</li>
          <li>Your account and your model usage: until you delete your account.</li>
          <li>Records of how each answer was made: deleted with its chat; copies within two days.</li>
          <li>Records of work in progress (runs, scheduled tasks): 72 hours after it ends.</li>
          <li>Sign-in records: 30 days. Server logs: until they rotate out, which is soon on a busy server.</li>
          <li>Backups: until the organization’s schedule replaces them, or it deletes them. Restoring one deletes again what you deleted.</li>
          <li>A record that security actions happened, naming your account only by an ID that nothing links to you once it is deleted.</li>
        </ul>
      </Section>

      <Section title="What you can do">
        <ul className="list-disc space-y-1.5 pl-5">
          <li>
            See and take a copy of your data: <Link href="/settings" className="underline underline-offset-4">Settings</Link>, Your data,
            Download a copy (a ZIP of JSON and your files).
          </li>
          <li>
            Correct your name: Settings, Profile. Change how you sign in, or take back an app’s access to your account: Settings,
            Sign-in and security, and Apps with access. To correct your email address, write to the organization running Gen9.
          </li>
          <li>Delete a chat from its menu, or your whole account from Settings. Deletion can’t be undone.</li>
          <li>
            Ask to restrict or object to processing
            {contact ? <>: write to {contact}</> : " by writing to the organization running Gen9"}.
          </li>
          <li>
            Complain to a data protection authority
            {settings.PRIVACY_AUTHORITY ? <>: {settings.PRIVACY_AUTHORITY}</> : ", such as the one where you live or work"}.
          </li>
        </ul>
      </Section>

      <Section title="Good to know">
        <p>
          An email address is needed to have an account. Gen9 makes no decision about you that has legal or similar effects: it answers
          questions, and its answers are marked as AI-generated. They can be wrong, so check what matters.
        </p>
      </Section>
    </main>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-10 space-y-3">
      <h2 className="text-title font-semibold">{title}</h2>
      {children}
    </section>
  );
}
