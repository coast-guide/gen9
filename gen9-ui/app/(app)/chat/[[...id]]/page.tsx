import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { cache } from "react";

import { ChatView } from "@/components/chat/chat-view";
import { AgentError, agentJson, type ThreadDetail } from "@/lib/agent";
import { requireSession } from "@/lib/auth/session";
import { chatTitle } from "@/lib/chat-title";

// The chat, or null when it isn't the person's (or doesn't exist): fetched once for the tab's
// title and the page (React's cache, as Next's metadata guide advises)
const loadThread = cache(async (threadId: string): Promise<ThreadDetail | null> => {
  const session = await requireSession(`/chat/${threadId}`);
  return agentJson<ThreadDetail>(session, `/v1/threads/${encodeURIComponent(threadId)}`).catch((error) => {
    if (error instanceof AgentError && (error.status === 404 || error.status === 422)) return null;
    throw error;
  });
});

// The tab says which chat it is (WCAG 2.2 SC 2.4.2), and "Not found" as the not-found page does
export async function generateMetadata({ params }: PageProps<"/chat/[[...id]]">): Promise<Metadata> {
  const { id } = await params;
  if (!id) return { title: "New chat" };
  const thread = id.length === 1 ? await loadThread(id[0]) : null;
  if (!thread) return { title: "Not found" };
  return { title: chatTitle(thread.title ?? thread.messages[0]?.content) };
}

export default async function ChatPage({ params }: PageProps<"/chat/[[...id]]">) {
  const { id } = await params;
  const threadId = id?.[0];
  const session = await requireSession(threadId ? `/chat/${threadId}` : "/chat");
  if (id && id.length > 1) notFound();

  let thread: ThreadDetail | null = null;
  if (threadId) {
    thread = await loadThread(threadId);
    if (!thread) notFound();
  }
  return (
    <ChatView
      key={thread ? "thread" : "new"}
      threadId={thread?.id ?? null}
      title={thread?.title ?? null}
      initialMessages={thread?.messages ?? []}
      activeRun={thread?.active_run ?? null}
      initialTodos={thread?.todos ?? []}
      firstName={session.user.givenName}
      permissionMode={thread?.permission_mode ?? "auto"}
      parentId={thread?.parent_id ?? null}
      initialTasks={thread?.tasks ?? []}
      owner={session.user.sub}
    />
  );
}
