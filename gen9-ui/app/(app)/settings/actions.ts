"use server";

import { refresh } from "next/cache";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import type { Connector, Controls, DirectoryEntry } from "@/lib/agent";
import { agentFetch, agentJson } from "@/lib/agent";
import { endBrowserSession, endOtherBrowserSessions, removeAppAccess } from "@/lib/auth/account";
import { endedSessionCookie } from "@/lib/auth/cookies";
import { signedInRecently } from "@/lib/auth/recent-sign-in";
import { requireSession } from "@/lib/auth/session";
import { deleteSessionsBySub } from "@/lib/auth/store";
import { secretRefusal, type SecretRefusal } from "@/lib/secret-refusal";
import { refusal } from "@/lib/refusal";

/**
 * Ends every Keycloak session of this user (all devices and apps). Keycloak then notifies each
 * app by back-channel logout; this app's sessions are dropped right away as well.
 */
export async function signOutEverywhere(): Promise<void> {
  const session = await requireSession("/settings");
  await agentJson<void>(session, "/v1/me/sign-out-everywhere", { method: "POST" });
  await deleteSessionsBySub(session.user.sub);
  (await cookies()).set(endedSessionCookie());
  redirect("/signed-out?reason=everywhere");
}

/**
 * Signs one other browser out. Keycloak ends that session only if it belongs to this user, then
 * tells each app by back-channel logout, so its Gen9 session goes too.
 */
export async function signOutBrowser(id: string): Promise<void> {
  const session = await requireSession("/settings");
  await endBrowserSession(session, id);
  refresh();
}

/** Takes back an app's access to the person's account (Apps with access). */
export async function removeApp(clientId: string): Promise<{ error?: string }> {
  const session = await requireSession("/settings");
  try {
    await removeAppAccess(session, clientId);
  } catch (error) {
    console.error("[settings] remove app access", error);
    return { error: "Its access wasn’t removed. Try again." };
  }
  refresh();
  return {};
}

/** Signs out every browser but this one. */
export async function signOutOtherBrowsers(): Promise<void> {
  const session = await requireSession("/settings");
  await endOtherBrowserSessions(session);
  refresh();
}

export type DeleteAccountResult = { error: string; reauth?: boolean };

/**
 * Deletes the account after the user typed their email and signed in within the last 5 minutes.
 * gen9-agent removes Gen9's data, then the Keycloak user; this app's sessions go last.
 */
export async function deleteAccount(confirmation: string): Promise<DeleteAccountResult | undefined> {
  const session = await requireSession("/settings");
  const email = session.user.email?.toLowerCase();
  if (!email || confirmation.trim().toLowerCase() !== email) {
    return { error: "Type your email exactly as shown to confirm." };
  }
  const reauth = { error: "For your security, sign in again first.", reauth: true };
  if (!signedInRecently(session.authTime)) return reauth;

  const response = await agentFetch(session, "/v1/me", { method: "DELETE" });
  if (response.status === 401 && response.headers.get("WWW-Authenticate")?.includes("insufficient_user_authentication")) {
    return reauth;
  }
  if (!response.ok) {
    const detail = await response.json().then((body: { detail?: unknown }) => body.detail, () => null);
    return { error: refusal(detail, "Your account wasn’t deleted. Try again.") };
  }
  await deleteSessionsBySub(session.user.sub);
  (await cookies()).set(endedSessionCookie());
  // 202: the data isn't all gone yet (a step waits on a service that is down); it finishes on its own
  redirect(response.status === 202 ? "/signed-out?reason=deleting" : "/signed-out?reason=deleted");
}

export type MemoryResult = { error: string } | undefined;

/** Replaces what Gen9 remembers about the user (gen9-agent's /v1/me/memory; 16,000 characters at most). */
export async function saveMemory(content: string): Promise<MemoryResult> {
  const session = await requireSession("/settings");
  const response = await agentFetch(session, "/v1/me/memory", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (response.status === 422) return { error: "That’s longer than 16,000 characters. Shorten it and try again." };
  if (!response.ok) return { error: "Couldn’t save your memory. Try again." };
  refresh();
}

/** Forgets everything Gen9 remembers about the user. */
export async function clearMemory(): Promise<MemoryResult> {
  const session = await requireSession("/settings");
  const response = await agentFetch(session, "/v1/me/memory", { method: "DELETE" });
  if (!response.ok) return { error: "Couldn’t clear your memory. Try again." };
  refresh();
}

export type ConnectorResult = { error: string } | undefined;
/** A connector's result that may send the person to sign in at the server. */
export type SignInResult = { error: string } | { authorizeUrl: string } | undefined;

/** Connects a remote MCP server: gen9-agent lists its tools first, and says why if it can't. */
export async function addConnector(input: { name: string; url: string; token?: string; header?: string }): Promise<SignInResult> {
  const session = await requireSession("/settings");
  const response = await agentFetch(session, "/v1/me/connectors", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: input.name,
      url: input.url,
      ...(input.token ? { token: input.token } : {}),
      ...(input.header ? { header: input.header } : {}),
    }),
  });
  if (response.status === 409) return { error: "You already have a connector with that name." };
  if (response.status === 422) {
    const detail = (await response.json().catch(() => ({})))?.detail;
    // A message from gen9-agent (it couldn't use the server), or a field that doesn't fit
    return { error: refusal(detail, "Use a short name (lowercase letters, digits and hyphens) and an https:// address.") };
  }
  if (response.status === 503) return { error: "This Gen9 can’t keep sign-ins or tokens yet. Ask an admin." };
  if (!response.ok) return { error: "Couldn’t add the connector. Try again." };
  const added = (await response.json().catch(() => ({}))) as Partial<Connector>;
  refresh();
  // The server needs sign-in: the page sends the person there
  if (added.authorize_url) return { authorizeUrl: added.authorize_url };
}

/** Starts signing in to a connector again (its sign-in lapsed, or was never finished). */
export async function signInConnector(id: string): Promise<SignInResult> {
  const session = await requireSession("/settings");
  const response = await agentFetch(session, `/v1/me/connectors/${encodeURIComponent(id)}/sign-in`, { method: "POST" });
  if (!response.ok) return { error: "Couldn’t start the sign-in. Try again." };
  const connector = (await response.json()) as Connector;
  return connector.authorize_url ? { authorizeUrl: connector.authorize_url } : { error: "Couldn’t start the sign-in. Try again." };
}

/** When Gen9 asks before using a connector. */
export async function setConnectorPolicy(id: string, policy: string): Promise<ConnectorResult> {
  const session = await requireSession("/settings");
  const response = await agentFetch(session, `/v1/me/connectors/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ policy }),
  });
  if (!response.ok) return { error: "Couldn’t change it. Try again." };
  refresh();
}

/** Lets the agent use the new or changed tools the person looked at (`pins`, as Settings showed
 * them); a change made since still waits. */
export async function keepConnectorTools(id: string, pins: string[]): Promise<ConnectorResult> {
  const session = await requireSession("/settings");
  const response = await agentFetch(session, `/v1/me/connectors/${encodeURIComponent(id)}/tools`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pins }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    return { error: refusal(body.detail, "Couldn’t keep them. Try again.") };
  }
  refresh();
}

/** Disconnects it: its tools leave the next chat, and its token is deleted. */
export async function removeConnector(id: string): Promise<ConnectorResult> {
  const session = await requireSession("/settings");
  const response = await agentFetch(session, `/v1/me/connectors/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!response.ok && response.status !== 404) return { error: "Couldn’t remove it. Try again." };
  refresh();
}

/** Servers in the connector directory (Gen9's copy of an MCP registry) matching the words. */
export async function searchDirectory(words: string): Promise<DirectoryEntry[] | { error: string }> {
  const session = await requireSession("/settings");
  const response = await agentFetch(session, `/v1/directory?${new URLSearchParams({ q: words.slice(0, 200), limit: "20" })}`);
  if (!response.ok) return { error: "Couldn’t search the directory. Try again." };
  return (await response.json()) as DirectoryEntry[];
}

export type SecretResult = SecretRefusal | undefined;

/** A secret the person's chat environments send to a host, added on the way out (gen9-agent's
 * environments.py): running environments get it within seconds. */
export async function addEnvironmentSecret(input: {
  name: string;
  host: string;
  path?: string;
  auth: "bearer" | "header" | "basic";
  header?: string;
  methods: "read" | "all";
  value: string;
}): Promise<SecretResult> {
  const session = await requireSession("/settings");
  const response = await agentFetch(session, "/v1/me/environment-secrets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: input.name,
      host: input.host,
      ...(input.path ? { path: input.path } : {}),
      auth: input.auth,
      ...(input.auth === "header" ? { header: input.header } : {}),
      methods: input.methods,
      value: input.value,
    }),
  });
  if (response.status === 409) return { field: "name", error: "You already have a secret with that name." };
  if (response.status === 422) return secretRefusal(await response.json().catch(() => null));
  if (response.status === 503) return { error: "This Gen9 can’t keep secrets yet. Ask an admin." };
  if (!response.ok) return { error: "Couldn’t add the secret. Try again." };
  refresh();
}

export async function removeEnvironmentSecret(id: string): Promise<SecretResult> {
  const session = await requireSession("/settings");
  const response = await agentFetch(session, `/v1/me/environment-secrets/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!response.ok && response.status !== 404) return { error: "Couldn’t remove it. Try again." };
  refresh();
}

export type PluginResult = { error: string } | undefined;

/** Adds a plugin an admin made available: its skills join your chats from the next message. */
export async function addMyPlugin(id: string): Promise<PluginResult> {
  const session = await requireSession("/settings");
  const response = await agentFetch(session, `/v1/me/plugins/${encodeURIComponent(id)}`, { method: "PUT" });
  if (response.status === 404) return { error: "That plugin isn’t available any more." };
  if (!response.ok) return { error: "Couldn’t add it. Try again." };
  refresh();
}

export async function removeMyPlugin(id: string): Promise<PluginResult> {
  const session = await requireSession("/settings");
  const response = await agentFetch(session, `/v1/me/plugins/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (response.status === 409) return { error: "An admin gave everyone this plugin, so it stays." };
  if (!response.ok && response.status !== 404) return { error: "Couldn’t remove it. Try again." };
  refresh();
}

/** Which emails the person gets about their scheduled tasks. */
export async function setNotifications(email: "all" | "needs_you" | "never"): Promise<PluginResult> {
  const session = await requireSession("/settings");
  const response = await agentFetch(session, "/v1/me/notifications", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  if (!response.ok) return { error: "Couldn’t save it. Try again." };
  refresh();
}

/** What the person lets Gen9 do with their chats (gen9-agent's /v1/me/controls). */
export async function setControls(controls: Partial<Controls>): Promise<PluginResult> {
  const session = await requireSession("/settings");
  const response = await agentFetch(session, "/v1/me/controls", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(controls),
  });
  if (!response.ok) return { error: "Couldn’t save it. Try again." };
  refresh();
}
