"use server";

import { refresh, revalidatePath } from "next/cache";

import { refusedAsNotAdmin } from "@/lib/admin-access";
import { AgentError, agentJson, type PluginAvailability, type PluginFileContent } from "@/lib/agent";
import { refreshRoles, requireSession } from "@/lib/auth/session";

export type PluginActionResult = { ok: true; message: string } | { ok: false; message: string };

async function adminCall(path: string, init: RequestInit, message: string): Promise<PluginActionResult> {
  const session = await requireSession("/admin/plugins");
  if (!session.isAdmin) return { ok: false, message: "You need admin access." };
  try {
    await agentJson<unknown>(session, path, init);
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
  revalidatePath("/admin/plugins");
  return { ok: true, message };
}

/** Adds a repository with a plugin marketplace; gen9-agent checks the address and starts its sync. */
export async function addPluginSource(url: string, ref: string): Promise<PluginActionResult> {
  return adminCall(
    "/v1/admin/plugin-sources",
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: url.trim(), ref: ref.trim() || null }) },
    "Added. Syncing it now.",
  );
}

export async function syncPluginSource(id: string): Promise<PluginActionResult> {
  return adminCall(`/v1/admin/plugin-sources/${encodeURIComponent(id)}/sync`, { method: "POST" }, "Syncing it now.");
}

export async function removePluginSource(id: string): Promise<PluginActionResult> {
  return adminCall(`/v1/admin/plugin-sources/${encodeURIComponent(id)}`, { method: "DELETE" }, "Source removed.");
}

/** What one of a plugin's kept files says, for the admin to read before choosing who may have it. */
export async function readPluginFile(id: string, path: string): Promise<PluginFileContent | { error: string }> {
  const session = await requireSession("/admin/plugins");
  if (!session.isAdmin) return { error: "You need admin access." };
  try {
    return await agentJson<PluginFileContent>(session, `/v1/admin/plugins/${encodeURIComponent(id)}/file?${new URLSearchParams({ path })}`);
  } catch (error) {
    return { error: error instanceof AgentError ? error.message : "Couldn’t read it. Try again." };
  }
}

/** Who may have a plugin, agreeing to it as the admin saw it (`fingerprint`); refused if it changed
 * since. After a change, choosing again lets people have it again. */
export async function setPluginAvailability(id: string, availability: PluginAvailability, fingerprint: string | null): Promise<PluginActionResult> {
  return adminCall(
    `/v1/admin/plugins/${encodeURIComponent(id)}`,
    { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ availability, fingerprint }) },
    "Saved.",
  );
}
