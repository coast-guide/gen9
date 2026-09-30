import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { Suspense } from "react";

import { LocalDate } from "@/components/local-date";
import { RelativeTime } from "@/components/relative-time";
import { RemoveApp } from "@/components/settings/apps";
import { Appearance } from "@/components/settings/appearance";
import { DeleteAccount } from "@/components/settings/delete-account";
import { Row, Section } from "@/components/settings/section";
import { SignOutBrowser, SignOutOtherBrowsers } from "@/components/settings/sign-out-browser";
import { SignOutEverywhere } from "@/components/settings/sign-out-everywhere";
import { UpdatedToast } from "@/components/settings/updated-toast";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { ConnectorSettings } from "@/components/settings/connectors";
import { EnvironmentSecretSettings } from "@/components/settings/environment-secrets";
import { NotificationSettings } from "@/components/settings/notifications";
import { PluginSettings, SkillList } from "@/components/settings/plugins";
import { MemorySettings } from "@/components/settings/memory";
import { ControlSwitch } from "@/components/settings/controls";
import { type Limit, limitWords } from "@/lib/limit";
import { agentJson, type Connector, type Controls, type Credential, type EnvironmentSecret, type Me, type Memory, type MyPlugin, type MySkill, type Notifications, type RecoveryCodes, type Security } from "@/lib/agent";
import { appsWithAccess, signedInBrowsers } from "@/lib/auth/account";
import { whatItMayDo } from "@/lib/app-scopes";
import { signedInRecently } from "@/lib/auth/recent-sign-in";
import { requireSession } from "@/lib/auth/session";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Settings" };

/** Account actions run on Keycloak's pages (application-initiated actions) and return here. */
function AccountAction({ action, returnTo = "/settings", children }: { action: string; returnTo?: string; children: React.ReactNode }) {
  return (
    <a
      href={`/auth/login?action=${action}&returnTo=${encodeURIComponent(returnTo)}`}
      className={buttonVariants({ variant: "outline" })}
    >
      {children}
    </a>
  );
}

/** One passkey or authenticator app under its heading row: its name, when it was added, and Remove. */
function CredentialRow({ credential, kind, removing }: { credential: Credential; kind: string; removing: string }) {
  const name = credential.label || kind;
  return (
    <Row
      className="pl-8 sm:pl-9"
      label={name}
      value={
        credential.created_at_ms ? (
          <>
            Added <LocalDate ms={credential.created_at_ms} />
          </>
        ) : undefined
      }
      action={
        <a
          href={`/auth/login?action=delete_credential:${credential.id}&removing=${removing}&returnTo=/settings`}
          aria-label={`Remove ${name}`}
          className={cn(buttonVariants({ variant: "ghost" }), "text-destructive hover:text-destructive")}
        >
          Remove
        </a>
      }
    />
  );
}

// Keycloak's default threshold for warning that recovery codes are running low
const FEW_CODES_LEFT = 4;

function recoveryCodesStatus(codes: RecoveryCodes | null, hasApp: boolean) {
  if (!codes) return "Sign in with a saved code when you can’t use your authenticator app";
  // Normally removed with the last app (auth callback); they may remain if it was removed elsewhere
  if (!hasApp) return "Without an authenticator app, every sign-in asks for one of these codes";
  if (codes.remaining === null || codes.total === null) return "On";
  const left = `${codes.remaining} of ${codes.total} left`;
  return codes.remaining <= FEW_CODES_LEFT ? `${left}. Create new codes soon.` : left;
}

export default async function SettingsPage({ searchParams }: PageProps<"/settings">) {
  const session = await requireSession("/settings");
  const { delete: reopenDelete, sign_in: signIn, connector: signedInTo, reason, next, updated } = await searchParams;
  // Back from a connector's sign-in (settings/connectors/callback)
  const text = (value: string | string[] | undefined) => (typeof value === "string" ? value : "");
  const signInNotice =
    text(signIn) === "done"
      ? { ok: true, message: `Signed in. Gen9 can use ${text(signedInTo) || "it"} now.` }
      : text(signIn) === "denied"
        ? { ok: false, message: "The sign-in was cancelled at the server." }
        : text(signIn) === "failed"
          ? { ok: false, message: text(reason) || "The sign-in didn’t finish. Try again." }
          : null;
  const [me, security, memory, connectors, secrets, plugins, skills, notifications, controls, browsers, limit, apps] = await Promise.all([
    agentJson<Me>(session, "/v1/me"),
    agentJson<Security>(session, "/v1/me/security").catch(() => null),
    agentJson<Memory>(session, "/v1/me/memory").catch(() => null),
    agentJson<Connector[]>(session, "/v1/me/connectors").catch(() => null),
    agentJson<EnvironmentSecret[]>(session, "/v1/me/environment-secrets").catch(() => null),
    agentJson<MyPlugin[]>(session, "/v1/me/plugins").catch(() => null),
    agentJson<MySkill[]>(session, "/v1/me/skills").catch(() => null),
    agentJson<Notifications>(session, "/v1/me/notifications").catch(() => null),
    agentJson<Controls>(session, "/v1/me/controls").catch(() => null),
    signedInBrowsers(session).catch((error: unknown) => {
      console.error("[settings] signed-in browsers", error);
      return null;
    }),
    agentJson<Limit>(session, "/v1/me/limit").catch(() => null),
    appsWithAccess(session).catch((error: unknown) => {
      console.error("[settings] apps with access", error);
      return null;
    }),
  ]);
  // Just set up a first authenticator app: without recovery codes, losing the phone locks the person out
  // (Forgot password asks for the app's code too), so saving them comes next (found by hand: P2-J4)
  if (text(next) === "recovery-codes" && security && security.authenticator_apps.length > 0 && !security.recovery_codes) {
    // What was set up goes along, so the toast after the codes names both
    const returnTo = [updated].flat().includes("authenticator") ? "/settings?updated=authenticator" : "/settings";
    redirect(`/auth/login?action=CONFIGURE_RECOVERY_AUTHN_CODES&returnTo=${encodeURIComponent(returnTo)}`);
  }

  return (
    <main className="mx-auto w-full max-w-2xl px-5 pt-8 pb-16 sm:px-8 lg:pt-14">
      <Suspense>
        <UpdatedToast />
      </Suspense>
      <h1 className="text-headline font-semibold">Settings</h1>

      <div className="mt-8 grid gap-10">
        <Section title="Profile">
          <Row label="Name" value={me.name ?? "Not set"} action={<AccountAction action="UPDATE_PROFILE">Edit</AccountAction>} />
          <Row
            label="Email"
            value={
              <span className="flex flex-wrap items-center gap-2">
                {me.email}
                <Badge variant="secondary">Verified</Badge>
              </span>
            }
          />
          <Row
            label="Role"
            value={session.isAdmin ? "Admin: can manage users" : "Member"}
          />
          {/* How much of the model usage limit is used, and when it resets (P5-D1) */}
          {limit && <Row label="Model use" value={limitWords(limit)} />}
        </Section>

        {memory && (
          <Section title="Memory" description="What Gen9 remembers about you. Every chat reads it as it is now, new chats and ones you’ve already started.">
            <MemorySettings
              key={memory.updated_at ?? "empty"}
              content={memory.content}
              updatedAtMs={memory.updated_at ? Date.parse(memory.updated_at) : null}
            />
            {controls && (
              <>
                <ControlSwitch
                  control="remember"
                  value={controls.remember}
                  label="Remember things about me"
                  hint="Gen9 keeps what helps future chats, never sensitive details you didn’t ask it to. Off, it neither uses nor adds to this memory."
                />
                <ControlSwitch
                  control="search_past_chats"
                  value={controls.search_past_chats}
                  label="Search and reference past chats"
                  hint="Gen9 looks through your earlier chats when you mention one, and says which it used."
                />
              </>
            )}
          </Section>
        )}

        {connectors && (
          <Section title="Connectors" description="Services Gen9 can use for you. It asks before using them, unless you say otherwise.">
            <ConnectorSettings connectors={connectors} notice={signInNotice} />
          </Section>
        )}
        {plugins && plugins.length > 0 && (
          <Section title="Plugins" description="Know-how and services your admins made available. What you add joins your chats.">
            <PluginSettings plugins={plugins} />
          </Section>
        )}
        {skills && skills.length > 0 && (
          <Section title="Skills" description="What Gen9 follows when a task matches.">
            <SkillList skills={skills} />
          </Section>
        )}
        {notifications && (
          <Section title="Notifications" description="Gen9 tells you once when a scheduled task is done or needs you.">
            <NotificationSettings notifications={notifications} />
          </Section>
        )}
        {secrets && (
          <Section
            title="Environment secrets"
            description="Tokens your chats’ environments send to a service, added on the way out: code running there never sees them."
          >
            <EnvironmentSecretSettings secrets={secrets} />
          </Section>
        )}

        <Section title="Sign-in and security" description="Changes open Gen9’s secure sign-in page, then bring you back.">
          <Row
            label="Password"
            value={
              security?.password_changed_at_ms ? (
                <>
                  Last changed <LocalDate ms={security.password_changed_at_ms} />
                </>
              ) : (
                "Set"
              )
            }
            action={<AccountAction action="UPDATE_PASSWORD">Change password</AccountAction>}
          />
          <Row
            label="Authenticator app"
            value={
              security === null
                ? "Status unavailable"
                : security.authenticator_apps.length
                  ? "On: codes are required at sign-in"
                  : "Off"
            }
            action={
              <AccountAction
                action="CONFIGURE_TOTP"
                // A first authenticator app goes on to recovery codes, as GitHub's two-factor setup does
                returnTo={security?.authenticator_apps.length || security?.recovery_codes ? "/settings" : "/settings?next=recovery-codes"}
              >
                {security?.authenticator_apps.length ? "Add another" : "Set up"}
              </AccountAction>
            }
          />
          {security?.authenticator_apps.map((app) => (
            <CredentialRow key={app.id} credential={app} kind="Authenticator app" removing="authenticator" />
          ))}
          {security && (security.authenticator_apps.length > 0 || security.recovery_codes) && (
            <Row
              label="Recovery codes"
              value={recoveryCodesStatus(security.recovery_codes, security.authenticator_apps.length > 0)}
              action={
                security.authenticator_apps.length > 0 || !security.recovery_codes ? (
                  <AccountAction action="CONFIGURE_RECOVERY_AUTHN_CODES">
                    {security.recovery_codes ? "Create new codes" : "Create codes"}
                  </AccountAction>
                ) : (
                  <a
                    href={`/auth/login?action=delete_credential:${security.recovery_codes.id}&removing=recovery-codes&returnTo=/settings`}
                    className={cn(buttonVariants({ variant: "ghost" }), "text-destructive hover:text-destructive")}
                  >
                    Remove codes
                  </a>
                )
              }
            />
          )}
          <Row
            label="Passkeys"
            value={security === null ? "Status unavailable" : "Sign in with your face, fingerprint or screen lock"}
            action={<AccountAction action="webauthn-register-passwordless">Add a passkey</AccountAction>}
          />
          {security?.passkeys.map((passkey) => (
            <CredentialRow key={passkey.id} credential={passkey} kind="Passkey" removing="passkey" />
          ))}
        </Section>

        <Section title="Where you’re signed in">
          {browsers === null && <Row label="Signed-in browsers" value="Unavailable right now" />}
          {browsers?.map((browser) => {
            const device = `${browser.browser} on ${browser.os}`;
            // A session only the CLI uses is a terminal, approved in that browser
            const name = browser.web ? device : "Gen9 CLI";
            const via = !browser.cli ? "" : browser.web ? " Gen9 CLI signed in through it too." : ` Approved in ${device}.`;
            return (
              <Row
                key={browser.id}
                label={name}
                value={
                  browser.current ? (
                    <>
                      This browser. Signed in <RelativeTime ms={browser.startedMs} />.{via}
                    </>
                  ) : (
                    <>
                      Last active <RelativeTime ms={browser.lastAccessMs} />, from {browser.ipAddress}.{via}
                    </>
                  )
                }
                action={browser.current ? <Badge variant="secondary">This browser</Badge> : <SignOutBrowser id={browser.id} name={name} />}
              />
            );
          })}
          {browsers && browsers.some((browser) => !browser.current) && (
            <Row
              label="Other sessions"
              value="Sign them out and keep using Gen9 here."
              action={<SignOutOtherBrowsers count={browsers.filter((browser) => !browser.current).length} />}
            />
          )}
          <Row
            label="Sign out everywhere"
            value="Ends your Gen9 sessions on every browser and device."
            action={<SignOutEverywhere />}
          />
        </Section>

        <Section title="Apps with access" description="Apps you let use your account: Gen9’s terminal, and agents that use Gen9 for you.">
          {apps === null && <Row label="Apps" value="Unavailable right now" />}
          {apps?.length === 0 && <Row label="None" value="When you sign in to Gen9’s terminal or let an agent use Gen9, it appears here." />}
          {apps?.map((app) => (
            <Row
              key={app.clientId}
              label={app.name}
              value={
                <>
                  Can {whatItMayDo(app.consentTexts).join("; ")}.
                  {app.allowedMs !== null && (
                    <>
                      {" "}Allowed <LocalDate ms={app.allowedMs} />.
                    </>
                  )}
                </>
              }
              action={<RemoveApp clientId={app.clientId} name={app.name} />}
            />
          ))}
        </Section>

        <Section title="Appearance">
          <Row label="Theme" action={<Appearance />} />
        </Section>

        {/* GDPR Art. 20, as ChatGPT and Claude offer it (gen9-agent's api/export.py, P3-E1) */}
        <Section title="Your data">
          <Row
            label="Download a copy"
            value="Your chats, memory, scheduled tasks, connectors and environment secrets’ settings, your chats’ files, your model usage, your account’s activity and sign-ins, and the apps you allowed, as a ZIP. No tokens or secret values."
            action={
              <a href="/api/export" download className={buttonVariants({ variant: "outline" })}>
                Download
              </a>
            }
          />
          {/* What Gen9 keeps, who else receives it, for how long, and a person's rights (GDPR Art. 13) */}
          <Row
            label="How Gen9 uses your data"
            value="What it keeps, who else receives it, for how long, and what you can do."
            action={
              <Link href="/privacy" className={buttonVariants({ variant: "outline" })}>
                Read
              </Link>
            }
          />
        </Section>

        {me.email && (
          <Section title="Delete account">
            <Row
              label="Delete your account"
              value="Permanently deletes your account and all it holds: chats, memory, scheduled tasks, connectors and sign-in methods."
              action={
                <DeleteAccount
                  email={me.email}
                  recentlySignedIn={signedInRecently(session.authTime)}
                  open={reopenDelete === "1"}
                />
              }
            />
          </Section>
        )}
      </div>
    </main>
  );
}
