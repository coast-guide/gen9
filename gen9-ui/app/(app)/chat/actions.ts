"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { AgentError, agentJson, type Thread } from "@/lib/agent";
import { requireSession } from "@/lib/auth/session";

export async function createThread(): Promise<Thread> {
  const session = await requireSession("/chat");
  return agentJson<Thread>(session, "/v1/threads", { method: "POST" });
}

export async function deleteThread(id: string): Promise<void> {
  const session = await requireSession("/chat");
  await agentJson<void>(session, `/v1/threads/${encodeURIComponent(id)}`, { method: "DELETE" }).catch((error) => {
    // Already gone (deleted in another tab, say): what the person asked for is done. Another
    // person's chat answers the same 404, so this tells them nothing either
    if (!(error instanceof AgentError && error.status === 404)) throw error;
  });
  revalidatePath("/chat", "layout");
  redirect("/chat");
}
