"use server";

import { refresh, revalidatePath } from "next/cache";

import { refusedAsNotAdmin } from "@/lib/admin-access";
import { AgentError, agentFetch, agentJson } from "@/lib/agent";
import { signedInRecently } from "@/lib/auth/recent-sign-in";
import { refreshRoles, requireSession } from "@/lib/auth/session";
import { refusal } from "@/lib/refusal";

export type ActionResult = { ok: true; message: string } | { ok: false; message: string; reauth?: boolean };

async function adminCall(path: string, init: RequestInit, message: string): Promise<ActionResult> {
  const session = await requireSession("/admin/users");
  if (!session.isAdmin) return { ok: false, message: "You need admin access." };
  try {
    await agentJson<void>(session, path, init);
  } catch (error) {
    if (refusedAsNotAdmin(error)) {
      // Access removed since the session's token was issued: new tokens, and this page again with
      // them, so the sidebar drops its admin links in this response (P3-D7)
      await refreshRoles();
      refresh();
      return { ok: false, message: "You need admin access." };
    }
    return { ok: false, message: error instanceof AgentError ? error.message : "Something went wrong. Try again." };
  }
  revalidatePath("/admin/users");
  return { ok: true, message };
}

const json = (body: unknown): RequestInit => ({
  method: "PATCH",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export async function setEnabled(id: string, enabled: boolean) {
  return adminCall(`/v1/admin/users/${id}`, json({ enabled }), enabled ? "Account enabled." : "Account disabled and signed out.");
}

export async function setAdmin(id: string, isAdmin: boolean) {
  return adminCall(`/v1/admin/users/${id}`, json({ is_admin: isAdmin }), isAdmin ? "Admin access granted." : "Admin access removed.");
}

export async function signOutUser(id: string) {
  return adminCall(`/v1/admin/users/${id}/logout`, { method: "POST" }, "Signed out on every device.");
}

export async function sendPasswordReset(id: string) {
  return adminCall(`/v1/admin/users/${id}/password-reset`, { method: "POST" }, "Password reset email sent.");
}

export async function unlockUser(id: string) {
  return adminCall(`/v1/admin/users/${id}/unlock`, { method: "POST" }, "Sign-in unlocked.");
}

/**
 * Deletes a user and all their data (gen9-agent: Langfuse traces, sessions, chats, then Keycloak).
 * Like deleting your own account, it needs a sign-in from the last 5 minutes.
 */
export async function deleteUser(id: string): Promise<ActionResult> {
  const session = await requireSession("/admin/users");
  if (!session.isAdmin) return { ok: false, message: "You need admin access." };
  const reauth = { ok: false as const, message: "For your security, sign in again first.", reauth: true };
  if (!signedInRecently(session.authTime)) return reauth;
  const response = await agentFetch(session, `/v1/admin/users/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (response.status === 401 && response.headers.get("WWW-Authenticate")?.includes("insufficient_user_authentication")) {
    return reauth;
  }
  if (response.status === 403) return { ok: false, message: "You need admin access." };
  if (!response.ok) {
    const detail = await response.json().then((body: { detail?: unknown }) => body.detail, () => null);
    return { ok: false, message: refusal(detail, "The user wasn’t deleted. Try again.") };
  }
  revalidatePath("/admin/users");
  // 202: still deleting (a step waits on a service that is down); they show as disabled until it's done
  if (response.status === 202) return { ok: true, message: "Deleting the user and all their data. It finishes on its own; until then they show as disabled." };
  return { ok: true, message: "User deleted with all their data." };
}
