"use client";

import { ArrowDown01Icon, ArrowUp02Icon, Attachment01Icon, Cancel01Icon, Delete02Icon, MoreHorizontalIcon, PencilEdit01Icon, StopIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore, useTransition } from "react";
import { toast } from "sonner";

import { createThread, deleteThread } from "@/app/(app)/chat/actions";
import { onThreads, refreshThreadList } from "@/components/app-shell/thread-list";
import { Mark } from "@/components/brand/logo";
import { Markdown } from "@/components/chat/markdown";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Activity } from "@/components/chat/activity";
import { AppView, type ToComposer } from "@/components/chat/app-view";
import { ApprovalCard, type Decision } from "@/components/chat/approval";
import { type ElicitationAnswer, ElicitationCard } from "@/components/chat/elicitation";
import { SharedFiles, sizeOf } from "@/components/chat/files";
import { QuestionCard } from "@/components/chat/question";
import { RetryCard } from "@/components/chat/retry";
import { BackgroundTaskList } from "@/components/chat/background-tasks";
import { EvaluationNote } from "@/components/chat/evaluation";
import { Sources } from "@/components/chat/sources";
import { sends } from "@/lib/composer-keys";
import { loadDraft, saveDraft } from "@/lib/drafts";
import type { ActiveRun, BackgroundTask, ChatFile, InputRequest, Message, PermissionMode, Source, StepApp, ThreadDetail, Todo } from "@/lib/agent";
import { replacesDraft, settled } from "@/lib/app-asks";
import { chatTitle } from "@/lib/chat-title";
import { ANSWERABLE_KINDS } from "@/lib/input-requests";
import { lengthNote, MAX_MESSAGE_CHARS } from "@/lib/message-length";
import { completedRest, untilBroken } from "@/lib/run-events";
import { answerSources, turnOf } from "@/lib/sources";
import { useStickyInset } from "@/lib/sticky-inset";
import { cn } from "@/lib/utils";
import { noticeText, revisionText } from "@/lib/notice";
import { refusal } from "@/lib/refusal";

/** A file attached to the next message: uploading (no id), attached (an id), or refused (a problem). */
type Attachment = { key: string; name: string; size: number; id?: string; problem?: string };

type Props = {
  threadId: string | null;
  title: string | null;
  initialMessages: Message[];
  activeRun: ActiveRun | null;
  initialTodos: Todo[];
  firstName: string | null;
  permissionMode: PermissionMode;
  // A background task's chat: the chat that started it; it takes no messages of its own
  parentId?: string | null;
  // The tasks this chat's agent started in the background
  initialTasks?: BackgroundTask[];
  /** Whose chat: its unsent draft is kept per person (lib/drafts.ts) */
  owner: string;
};

// The composer's permission mode (docs/design/screens/chat.md, "Approvals and the permission mode")
const MODES: { value: PermissionMode; label: string; hint: string }[] = [
  { value: "auto", label: "Act, ask when unsure", hint: "Gen9 acts, and asks only when it needs to know something" },
  { value: "ask", label: "Ask before acting", hint: "Anything that changes something waits for your Allow" },
];

const SUGGESTIONS = [
  "What changed in the latest Keycloak release?",
  "Write and test a script that finds duplicate rows in a CSV",
  "Draft a checklist for moving a database with no downtime",
];

type RunEnd = { status: "success" | "error" | "cancelled" | "expired"; error: string | null };

// Tries to reconnect to a run's events in a row, 1, 2, 4, 8 and 16 s apart: an API restart takes about 10 s
const RECONNECTS = 5;

/** A message the API refused before any run started: nothing reached the agent. */
class NotSent extends Error {}

// Tab storage never changes under the page: nothing to subscribe to
const noop = () => () => {};

export function ChatView({
  threadId: initialThreadId,
  title,
  initialMessages,
  activeRun,
  initialTodos,
  firstName,
  permissionMode,
  parentId = null,
  initialTasks = [],
  owner,
}: Props) {
  const [threadId, setThreadId] = useState(initialThreadId);
  // The chat's id as soon as it has one: attaching a file can make the chat before the first send
  const chatId = useRef(initialThreadId);
  // Files attached to the next message (gen9-agent's api/files.py): uploading, attached, or refused
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const picker = useRef<HTMLInputElement>(null);
  // Its title once the first answer named it: from the chats a refresh brings
  const [shownTitle, setShownTitle] = useState(title);
  useEffect(() => onThreads((threads) => setShownTitle((t) => threads.find((x) => x.id === threadId)?.title ?? t)), [threadId]);
  const [messages, setMessages] = useState<Message[]>(() =>
    activeRun ? [...initialMessages, { role: "assistant", content: "" }] : initialMessages,
  );
  // A chat made on this page (history.replaceState, no navigation) keeps the tab in step with its
  // header: the page's metadata still said "New chat"
  const tabTitle = threadId ? chatTitle(shownTitle ?? messages[0]?.content) : null;
  useEffect(() => {
    if (tabTitle) document.title = `${tabTitle} – Gen9`;
  }, [tabTitle]);
  // A draft left unsent (an ended session's sign-in, a reload) is in the composer until the person
  // types: read once hydrated, since the server has no tab storage (lib/drafts.ts)
  const kept = useSyncExternalStore(noop, () => loadDraft(owner, initialThreadId), () => "");
  const [typed, setInput] = useState<string | null>(null);
  const input = typed ?? kept;
  // The connector whose app's message is in the composer, and when it came: a send a moment later
  // was meant for what was there before
  const [fromApp, setFromApp] = useState<string | null>(null);
  const appPutAt = useRef(-Infinity);
  const [status, setStatus] = useState<string | null>(null);
  // Said to screen readers when a turn ends well (errors have their toast)
  const [answered, setAnswered] = useState(false);
  const [todos, setTodos] = useState<Todo[]>(initialTodos); // the latest turn's plan
  const [streaming, setStreaming] = useState(Boolean(activeRun));
  // What the run waits for the person to answer (its `input.requested` events not yet answered)
  const [requests, setRequests] = useState<InputRequest[]>([]);
  // A question arrived while the person was typing in the composer: focus moves to the card
  const [focusQuestion, setFocusQuestion] = useState(false);
  const [mode, setMode] = useState<PermissionMode>(permissionMode);
  const [confirmDelete, setConfirmDelete] = useState(false);
  // Rename (Chat options): the title bar's heading becomes a field until Enter, Escape or leaving it
  const [renaming, setRenaming] = useState(false);
  const renameOpen = useRef(false);
  const renameField = useRef<HTMLInputElement>(null);
  const optionsButton = useRef<HTMLButtonElement>(null);
  const [deleting, startDelete] = useTransition();
  // The run being followed: its id (from `run.queued`) and the last event seen, to resume from
  const run = useRef<{ threadId: string; runId: string | null; lastEventId: string | null } | null>(null);
  // The text each message of the run has shown, by its id: one the model didn't stream shows when it completes
  const shown = useRef(new Map<string, string>());
  const listening = useRef<AbortController | null>(null);
  // Stop pressed before the run said its id: stop it as soon as it does (P2-K1)
  const stopWhenKnown = useRef(false);
  const bottom = useRef<HTMLDivElement>(null);
  // Pinned over the conversation: focus scrolls clear of them (lib/sticky-inset.ts)
  const titleBar = useStickyInset("top");
  const composerArea = useStickyInset("bottom");
  const textarea = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  /** A View's message (MCP Apps `ui/message`): into the composer for the person to read and send,
   * marked as the app's until they change it (P5-C1). Never over their draft unless they agreed. */
  const toComposer: ToComposer = (text, { from, replace, focus }) => {
    if (!replace && replacesDraft(input, text)) return false;
    setInput(text);
    setFromApp(from);
    appPutAt.current = performance.now();
    if (focus) textarea.current?.focus();
    return true;
  };

  /** Change the answer being written (the last message): its text, or its steps. */
  const updateAnswer = (update: (answer: Message) => Message) =>
    setMessages((m) => [...m.slice(0, -1), update(m[m.length - 1] ?? { role: "assistant", content: "" })]);
  const setAnswer = (update: (content: string) => string) => updateAnswer((a) => ({ ...a, content: update(a.content) }));

  /** Read a run's events until it completes; `null` if the connection ended first. */
  async function follow(body: ReadableStream<Uint8Array>): Promise<RunEnd | null> {
    for await (const { event, id, data } of untilBroken(body)) {
      if (run.current && id) run.current.lastEventId = id;
      if (event === "run.queued" && run.current) {
        run.current.runId = String(data.run_id);
        if (stopWhenKnown.current) {
          stopWhenKnown.current = false;
          void cancelRun(run.current.threadId, run.current.runId);
        }
      }
      if (event === "run.started" && Number(data.attempt) > 1) {
        shown.current.clear();
        updateAnswer((a) => ({ ...a, content: "", steps: [] })); // a new attempt answers again
      }
      if (event === "status") setStatus(String(data.text));
      if (event === "tool.started") {
        const step = {
          id: String(data.id),
          name: String(data.name),
          args: data.args,
          status: "running" as const,
          ...(typeof data.plugin === "string" ? { plugin: data.plugin } : {}),
        };
        updateAnswer((a) => ({ ...a, steps: [...(a.steps ?? []).filter((s) => s.id !== step.id), step] }));
      }
      if (event === "tool.completed") {
        const done = data.status === "error" ? ("error" as const) : data.status === "declined" ? ("declined" as const) : ("success" as const);
        const sources = (data.sources as Source[] | undefined) ?? undefined;
        const app = (data.app as StepApp | undefined) ?? undefined;
        // A question's step keeps the person's answer, a declined one their reason (the others'
        // output isn't shown)
        const output = data.name === "ask_user" || done === "declined" ? String(data.output ?? "") : undefined;
        updateAnswer((a) => ({
          ...a,
          steps: (a.steps ?? []).map((s) => (s.id === data.id ? { ...s, status: done, sources: sources ?? s.sources, output: output ?? s.output, app: app ?? s.app } : s)),
        }));
      }
      // Questions, approvals, a connector's requests, and Retry after a failure; a kind this page doesn't know is left
      // waiting (it expires)
      if (event === "input.requested" && (ANSWERABLE_KINDS as readonly string[]).includes(String(data.kind))) {
        const asked = data as unknown as InputRequest;
        setStatus(null);
        setFocusQuestion(document.activeElement === textarea.current);
        setRequests((r) => (r.some((x) => x.id === asked.id) ? r : [...r, asked]));
        void refreshThreadList(); // "Needs you" in the sidebar
      }
      if (event === "input.provided") {
        setRequests((r) => r.filter((x) => x.id !== data.id));
        // Retried: the turn starts again from its checkpoint and writes its answer anew
        if (data.retry) {
          shown.current.clear();
          updateAnswer((a) => ({ ...a, content: "" }));
        }
        void refreshThreadList();
      }
      if (event === "message.completed" && Array.isArray(data.citations)) {
        const cited = data.citations as Source[];
        updateAnswer((a) => ({ ...a, citations: [...(a.citations ?? []), ...cited] }));
      }
      if (event === "todos.updated") setTodos(data.todos as Todo[]);
      // The chat outgrew the context budget: earlier messages were summarized (gen9-agent's events.py)
      if (event === "context.summarized") updateAnswer((a) => ({ ...a, summarized: true }));
      // What the turn's environment shared (gen9-agent's chat_files.py): under this answer
      if (event === "files.shared" && Array.isArray(data.files)) {
        const shared = data.files as ChatFile[];
        updateAnswer((a) => ({ ...a, files: [...(a.files ?? []).filter((f) => !shared.some((s) => s.id === f.id)), ...shared] }));
      }
      if (event === "message.delta") {
        setStatus(null);
        const id = String(data.id ?? "");
        shown.current.set(id, (shown.current.get(id) ?? "") + String(data.text));
        setAnswer((content) => content + String(data.text));
      }
      if (event === "message.completed") {
        const id = String(data.id ?? "");
        const rest = completedRest(shown.current.get(id) ?? "", String(data.text));
        if (rest) {
          setStatus(null);
          shown.current.set(id, String(data.text));
          setAnswer((content) => content + rest);
        }
      }
      if (event === "run.completed") return data as RunEnd;
    }
    return null;
  }

  /** Follow the current run to its end, reconnecting from the last event when the connection drops. */
  async function followToEnd(first: ReadableStream<Uint8Array>, signal: AbortSignal): Promise<RunEnd | null> {
    let end = await follow(first);
    for (let failures = 0; !end && failures < RECONNECTS && run.current?.runId && !signal.aborted; ) {
      setStatus("Reconnecting");
      await new Promise((resolve) => setTimeout(resolve, 1000 * 2 ** failures));
      failures++;
      const { threadId: id, runId, lastEventId } = run.current;
      try {
        const response = await fetch(`/api/threads/${id}/runs/${runId}/stream`, {
          headers: { Accept: "text/event-stream", ...(lastEventId ? { "Last-Event-ID": lastEventId } : {}) },
          signal,
        });
        if (response.ok && response.body) {
          setStatus(null);
          end = await follow(response.body);
        }
      } catch (error) {
        if ((error as Error).name === "AbortError") throw error; // left the page
      }
      if (run.current?.lastEventId !== lastEventId) failures = 0; // it got through: a later drop gets its tries again
    }
    return end;
  }

  function finish(end: RunEnd | null) {
    if (end) setRequests([]);
    setAnswered(end?.status === "success");
    if (!end) toast.error("Lost the connection to this answer. Reload the page to see it.");
    else if (end.status === "error") toast.error(end.error || "Gen9 couldn’t answer. Try again.");
    else if (end.status === "expired") toast("Gen9 stopped waiting for an answer.");
    // A lost connection may still be answering: its empty answer goes. A turn that ended without text keeps it, with
    // "This answer didn't finish." under its steps, as after a reload (docs/design/screens/chat.md; P2-K1)
    if (!end) setMessages((m) => (m[m.length - 1]?.content === "" ? m.slice(0, -1) : m));
    void refreshThreadList(); // new title / order in the sidebar, and this chat's title
  }

  async function withRun(
    threadIdToFollow: string,
    runId: string | null,
    controller: AbortController,
    start: (signal: AbortSignal) => Promise<Response | null>,
  ) {
    listening.current = controller;
    run.current = { threadId: threadIdToFollow, runId, lastEventId: null };
    shown.current.clear();
    try {
      const response = await start(controller.signal);
      if (!response) return;
      finish(await followToEnd(response.body!, controller.signal));
    } catch (error) {
      if ((error as Error).name === "AbortError") return; // left the page: the run goes on without us
      toast.error((error as Error).message || "Gen9 couldn’t answer. Try again.");
      if (!(error instanceof NotSent)) setMessages((m) => (m[m.length - 1]?.content === "" ? m.slice(0, -1) : m));
    } finally {
      if (listening.current === controller) {
        listening.current = null;
        run.current = null;
        setStreaming(false);
        setStatus(null);
        textarea.current?.focus();
      }
    }
  }

  // Opened (or reloaded) while a run was still answering: follow it from its first event
  useEffect(() => {
    if (!activeRun || !initialThreadId) return;
    const controller = new AbortController();
    fetch(`/api/threads/${initialThreadId}/runs/${activeRun.id}/stream?after=0`, {
      headers: { Accept: "text/event-stream" },
      signal: controller.signal,
    }).then(
      (response) =>
        withRun(initialThreadId, activeRun.id, controller, async () => {
          if (!response.ok || !response.body) throw new Error("Couldn’t follow this answer. Reload the page.");
          return response;
        }),
      () => {}, // left the page before it connected
    );
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, for the run the page opened with
  }, []);

  useEffect(() => () => listening.current?.abort(), []);

  /** A run this page didn't start (a background task's notice): shown as it is, then followed. */
  const pickUp = useCallback(async () => {
    const id = chatId.current;
    if (!id || listening.current) return;
    const response = await fetch(`/api/threads/${id}`).catch(() => null);
    if (!response?.ok || listening.current) return;
    const thread = (await response.json()) as ThreadDetail;
    const active = thread.active_run;
    setMessages(active ? [...thread.messages, { role: "assistant", content: "" }] : thread.messages);
    if (!active) return;
    setStreaming(true);
    const controller = new AbortController();
    const stream = await fetch(`/api/threads/${id}/runs/${active.id}/stream?after=0`, {
      headers: { Accept: "text/event-stream" },
      signal: controller.signal,
    }).catch(() => null);
    await withRun(id, active.id, controller, async () => {
      if (!stream?.ok || !stream.body) throw new Error("Couldn’t follow this answer. Reload the page.");
      return stream;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reads refs and stable setters
  }, []);

  /** The chat's id, making the chat first if it has none yet. */
  async function ensureThread(): Promise<string> {
    if (chatId.current) return chatId.current;
    const id = (await createThread()).id;
    chatId.current = id;
    setThreadId(id);
    window.history.replaceState(null, "", `/chat/${id}`);
    return id;
  }

  /** Attach files to the next message: each goes to the chat's files now, and into its environment
   * when the message is sent. */
  async function attach(list: FileList | null) {
    const files = Array.from(list ?? []);
    if (!files.length) return;
    const chat = await ensureThread().catch(() => null);
    if (!chat) {
      toast.error("Couldn’t attach files right now. Try again in a moment.");
      return;
    }
    for (const file of files) {
      const key = crypto.randomUUID();
      setAttachments((a) => [...a, { key, name: file.name, size: file.size }]);
      const response = await fetch(`/api/threads/${chat}/files?${new URLSearchParams({ name: file.name })}`, { method: "POST", body: file }).catch(() => null);
      const body = await response?.json().catch(() => ({}));
      setAttachments((a) =>
        a.map((x) => (x.key !== key ? x : response?.ok ? { ...x, id: String(body.id), name: String(body.name) } : { ...x, problem: refusal(body?.detail, "Couldn’t attach it.") })),
      );
    }
  }

  function unattach(attachment: Attachment) {
    setAttachments((a) => a.filter((x) => x.key !== attachment.key));
    if (attachment.id && chatId.current) void fetch(`/api/threads/${chatId.current}/files/${attachment.id}`, { method: "DELETE" }).catch(() => {});
  }

  /** The composer's Send, at the key's or click's time: not a moment after a View's message landed
   * there (SETTLE_MS), since it was meant for what was there before. */
  const sendComposer = (at: number) => {
    if (settled(appPutAt.current, at)) void send(input);
  };

  async function send(text: string) {
    const message = text.trim();
    if (!message || streaming || message.length > MAX_MESSAGE_CHARS) return;
    // Still uploading: wait for it (Send stays off meanwhile)
    if (attachments.some((a) => !a.id && !a.problem)) return;
    const files = attachments.filter((a) => a.id).map((a) => ({ id: a.id!, name: a.name, size: a.size, media_type: "" }));
    const pending = attachments;
    setInput("");
    setFromApp(null);
    setAttachments([]);
    stopWhenKnown.current = false;
    setStreaming(true);
    setStatus(null);
    setAnswered(false);
    setTodos([]); // a new turn makes its own plan, if any
    setMessages((m) => [...m, { role: "user", content: message, files }, { role: "assistant", content: "" }]);
    const chat = await ensureThread().catch(() => null);
    // Kept under the chat's id until the run has it: a sign-in on the way comes back to it
    if (chat) {
      saveDraft(owner, chat, message);
      saveDraft(owner, null, "");
    }
    if (!chat) {
      // The chat couldn't be made (Gen9's API or its database is down): nothing was sent, so the
      // question goes back in the composer, as when a run can't start
      setMessages((m) => m.slice(0, -2));
      setInput(message);
      setAttachments(pending);
      setStreaming(false);
      toast.error("Gen9 can’t start a chat right now. Try again in a moment.");
      textarea.current?.focus();
      return;
    }
    if (stopWhenKnown.current) {
      // Stopped while the chat was being made: nothing was sent, so the question goes back in the composer
      stopWhenKnown.current = false;
      setMessages((m) => m.slice(0, -2));
      setInput(message);
      setAttachments(pending);
      setStreaming(false);
      setStatus(null);
      textarea.current?.focus();
      return;
    }
    await withRun(chat, null, new AbortController(), async (signal) => {
      const response = await fetch(`/api/threads/${chat}/runs`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify({ message, permission_mode: mode, ...(files.length ? { files: files.map((f) => f.id) } : {}) }),
        signal,
      });
      if (response.status === 401) {
        // /auth/login is a Route Handler that redirects to Keycloak: it needs a full page load
        // eslint-disable-next-line @next/next/no-location-assign-relative-destination
        window.location.assign(`/auth/login?returnTo=${encodeURIComponent(`/chat/${chat}`)}`);
        return null;
      }
      if (!response.ok || !response.body) {
        const detail = (await response.json().catch(() => ({})))?.detail;
        // Refused before the agent started (it isn't in the chat, even after a reload): the
        // question goes back in the composer, with its files, to send again
        setMessages((m) => m.slice(0, -2));
        setInput(message);
        setAttachments(pending);
        throw new NotSent(refusal(detail, "Gen9 couldn’t answer. Try again."));
      }
      saveDraft(owner, chat, "");
      return response;
    });
  }

  /** Answer what the run asks (`{answers}` for a question, `{decisions}` for an approval); returns
   * what went wrong, or null once the run has the answer. */
  async function answer(
    request: InputRequest,
    body: { answers: string[] } | { decisions: Decision[] } | { responses: Record<string, ElicitationAnswer> } | { retry: true },
  ): Promise<string | null> {
    const current = run.current;
    if (!current?.runId) return "This answer can’t be sent right now. Reload the page.";
    const response = await fetch(`/api/threads/${current.threadId}/runs/${current.runId}/inputs/${request.id}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).catch(() => null);
    if (response?.ok || response?.status === 409) {
      if (response.status === 409) toast(refusal((await response.json().catch(() => ({})))?.detail, "Already answered"));
      setRequests((r) => r.filter((x) => x.id !== request.id));
      return null;
    }
    if (response?.status === 422) return refusal((await response.json().catch(() => ({})))?.detail, "Check your answers.");
    return "Couldn’t send your answer. Try again.";
  }

  /** Stop answering: the run is cancelled on the server, and its stream then ends with `cancelled`. */
  async function cancelRun(threadId: string, runId: string) {
    const response = await fetch(`/api/threads/${threadId}/runs/${runId}/cancel`, { method: "POST" });
    if (!response.ok) toast.error("Couldn’t stop this answer. Try again.");
  }

  async function stop() {
    const current = run.current;
    if (current?.runId) return cancelRun(current.threadId, current.runId);
    // Not sent yet (the chat is being made), or sent but its run hasn't said its id: leaving would let it answer
    // anyway, so it isn't sent, or it's stopped as soon as it says its id (P2-K1)
    stopWhenKnown.current = true;
    setStatus("Stopping");
  }

  /** Save a new name: one line, as gen9-agent keeps it. Blank or unchanged keeps the old one, and
   * so does Escape (`save` false). Enter and Escape give focus back to Chat options; leaving the
   * field leaves it where the person put it. */
  async function rename(value: string, { save, refocus }: { save: boolean; refocus: boolean }) {
    if (!renameOpen.current) return; // Enter's save unmounts the field, which can blur it too
    renameOpen.current = false;
    setRenaming(false);
    if (refocus) requestAnimationFrame(() => optionsButton.current?.focus());
    const name = value.split(/\s+/).filter(Boolean).join(" ");
    const before = shownTitle;
    if (!save || !threadId || !name || name === (before ?? messages[0]?.content)) return;
    setShownTitle(name);
    const response = await fetch(`/api/threads/${threadId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: name }),
    }).catch(() => null);
    if (!response?.ok) {
      setShownTitle(before);
      const detail = (await response?.json().catch(() => null))?.detail;
      toast.error(refusal(detail, "Couldn’t rename this chat. Try again."));
      return;
    }
    void refreshThreadList();
  }

  const empty = messages.length === 0;
  const waiting = requests.length > 0;
  const Greeting = threadId ? "h2" : "h1";

  return (
    <main className="relative flex min-h-0 flex-1 flex-col">
      {threadId && (
        <div ref={titleBar} className="sticky top-14 z-20 flex h-12 items-center gap-2 border-b bg-background/85 px-4 backdrop-blur-md short:static lg:top-0 lg:h-14 lg:px-6">
          <h1 dir="auto" className={cn("min-w-0 flex-1 truncate text-sm font-medium", renaming && "sr-only")}>
            {shownTitle ?? messages[0]?.content ?? "New chat"}
          </h1>
          {renaming && (
            <form
              className="min-w-0 flex-1"
              onSubmit={(e) => {
                e.preventDefault();
                void rename(renameField.current?.value ?? "", { save: true, refocus: true });
              }}
            >
              <Input
                ref={renameField}
                dir="auto"
                aria-label="Chat name"
                defaultValue={shownTitle ?? messages[0]?.content ?? ""}
                maxLength={80}
                autoFocus
                onFocus={(e) => e.currentTarget.select()}
                onBlur={(e) => void rename(e.currentTarget.value, { save: true, refocus: false })}
                onKeyDown={(e) => {
                  if (e.key !== "Escape") return;
                  e.preventDefault();
                  void rename("", { save: false, refocus: true });
                }}
                className="h-8 text-sm font-medium"
              />
            </form>
          )}
          <DropdownMenu>
            <DropdownMenuTrigger render={<Button ref={optionsButton} variant="ghost" size="icon" aria-label="Chat options" />}>
              <HugeiconsIcon icon={MoreHorizontalIcon} strokeWidth={1.8} className="size-5" />
            </DropdownMenuTrigger>
            {/* Closed by Rename: focus goes to the new field, not back to this button */}
            <DropdownMenuContent align="end" className="w-48" finalFocus={() => (renameOpen.current ? (renameField.current ?? false) : true)}>
              <DropdownMenuItem
                onClick={() => {
                  renameOpen.current = true;
                  setRenaming(true);
                }}
              >
                <HugeiconsIcon icon={PencilEdit01Icon} strokeWidth={1.8} />
                Rename
              </DropdownMenuItem>
              <DropdownMenuItem variant="destructive" onClick={() => setConfirmDelete(true)}>
                <HugeiconsIcon icon={Delete02Icon} strokeWidth={1.8} />
                Delete chat
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      )}

      <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col px-5 sm:px-8">
        {empty ? (
          <div className="flex flex-1 flex-col justify-center py-10">
            <Mark className="size-9" />
            {/* The page's heading when there's no chat yet, under the chat's own when there is */}
            <Greeting className="mt-6 text-headline font-semibold text-balance">
              {firstName ? `What should Gen9 do, ${firstName}?` : "What should Gen9 do?"}
            </Greeting>
            <ul className="mt-8 grid gap-2 sm:grid-cols-3">
              {SUGGESTIONS.map((s) => (
                <li key={s}>
                  <button
                    type="button"
                    onClick={() => send(s)}
                    className="h-full w-full rounded-2xl border bg-card p-4 text-left text-sm leading-snug transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-hidden"
                  >
                    {s}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          // A chat log (WAI-ARIA's `log`: new messages are read out), busy while an answer is written so a
          // screen reader takes it as one update, not token by token (WAI-ARIA 1.2, aria-busy). What Gen9 is
          // doing meanwhile is said by the status below (P2-J1)
          <ol className="flex flex-col gap-8 py-8" aria-live="polite" aria-busy={streaming && !waiting}>
            {messages.map((m, i) =>
              m.role === "user" && m.notice ? (
                // A background task's notice (gen9-agent's background.py): Gen9 wrote it, not the person
                <li key={i} className="rounded-2xl border px-4 py-2.5 text-sm text-muted-foreground">
                  <span className="block text-xs font-medium">From a background task</span>
                  <span className="line-clamp-3 whitespace-pre-wrap">{noticeText(m.content)}</span>
                </li>
              ) : m.role === "user" && m.revision ? (
                // A scheduled task's grader wrote it, its findings for the next try (outcomes.py)
                <li key={i} className="rounded-2xl border px-4 py-2.5 text-sm text-muted-foreground">
                  <span className="block text-xs font-medium">From the rubric check</span>
                  <span className="whitespace-pre-wrap">{revisionText(m.content)}</span>
                </li>
              ) : m.role === "user" ? (
                <li key={i} className="ml-auto max-w-[85%] rounded-2xl rounded-br-md bg-secondary px-4 py-2.5 whitespace-pre-wrap">
                  <span className="sr-only">You said: </span>
                  {/* Its own direction: the hidden words before it are English (W3C i18n) */}
                  <span dir="auto" className="block">
                    {m.content}
                  </span>
                  {m.files && m.files.length > 0 && (
                    <span className="mt-1 block text-sm text-muted-foreground">Attached: {m.files.map((f) => f.name).join(", ")}</span>
                  )}
                </li>
              ) : (
                <li key={i} className="flex gap-3">
                  <Mark className="mt-0.5 size-6" decorative />
                  <span className="sr-only">Gen9 said: </span>
                  <div className="min-w-0 flex-1">
                    {m.summarized && (
                      <p className="mb-2 text-xs text-muted-foreground" role="note">
                        Earlier messages were summarized to make room. The whole chat is still here.
                      </p>
                    )}
                    <Activity
                      steps={m.steps ?? []}
                      todos={i === messages.length - 1 ? todos : []}
                      working={streaming && i === messages.length - 1}
                      waiting={waiting && i === messages.length - 1}
                      awaiting={i === messages.length - 1 ? requests.flatMap((r) => (r.kind === "approval" ? r.action_requests : [])) : []}
                    />
                    {/* A connector tool's View (MCP Apps), shown whether or not the steps are folded */}
                    {(m.steps ?? []).map((step) => step.app && <AppView key={step.id} app={step.app} onMessage={toComposer} />)}
                    {i === messages.length - 1 &&
                      requests.map((request, j) =>
                        request.kind === "question" ? (
                          <QuestionCard
                            key={request.id}
                            request={request}
                            answer={(answers) => answer(request, { answers })}
                            takeFocus={focusQuestion && j === 0}
                          />
                        ) : request.kind === "approval" ? (
                          <ApprovalCard
                            key={request.id}
                            request={request}
                            decide={(decisions) => answer(request, { decisions })}
                            takeFocus={focusQuestion && j === 0}
                          />
                        ) : request.kind === "elicitation" ? (
                          <ElicitationCard
                            key={request.id}
                            request={request}
                            respond={(responses) => answer(request, { responses })}
                            takeFocus={focusQuestion && j === 0}
                          />
                        ) : (
                          <RetryCard
                            key={request.id}
                            request={request}
                            retry={() => answer(request, { retry: true })}
                            takeFocus={focusQuestion && j === 0}
                          />
                        ),
                      )}
                    {m.content ? (
                      <>
                        <Markdown>{m.content}</Markdown>
                        {threadId && m.files && <SharedFiles threadId={threadId} files={m.files} />}
                        {/* Under a turn's last answer, once it's written: where the answer came from */}
                        {messages[i + 1]?.role !== "assistant" && !(streaming && i === messages.length - 1) && (
                          <Sources sources={answerSources(turnOf(messages, i))} />
                        )}
                        {m.evaluation && <EvaluationNote evaluation={m.evaluation} />}
                      </>
                    ) : waiting && i === messages.length - 1 ? (
                      <p className="text-muted-foreground">Needs you</p>
                    ) : streaming && i === messages.length - 1 ? (
                      <p className="flex items-center gap-2 text-muted-foreground">
                        <span className="size-1.5 animate-pulse rounded-full bg-leaf" aria-hidden />
                        {status ?? "Thinking"}
                      </p>
                    ) : (
                      // A turn that ended without text (stopped, or failed and not retried): not "Thinking"
                      <p className="text-muted-foreground">This answer didn’t finish.</p>
                    )}
                  </div>
                </li>
              ),
            )}
          </ol>
        )}
        {/* What Gen9 is doing, for screen readers: the answer itself is read once it's written */}
        <p role="status" className="sr-only">
          {streaming ? (waiting ? "Gen9 needs your answer." : `${status ?? "Gen9 is answering"}…`) : answered ? "Gen9 answered." : ""}
        </p>
      </div>

      {/* Composer: pinned to the bottom (thumb zone on phones), above the home indicator */}
      <div ref={composerArea} className="sticky bottom-0 bg-gradient-to-t from-background from-70% to-transparent pt-6 pb-safe [--safe-min:1rem] short:static">
        {threadId && !parentId && (
          <div className="mx-auto w-full max-w-3xl px-4 sm:px-8">
            <BackgroundTaskList threadId={threadId} initial={initialTasks} after={streaming ? -1 : messages.length} onTold={pickUp} />
          </div>
        )}
        {parentId ? (
          // A background task's chat: its instructions come from the chat that started it
          <div className="mx-auto flex w-full max-w-3xl items-center justify-between gap-3 px-4 sm:px-8">
            <p className="rounded-2xl border bg-card px-4 py-3 text-sm text-muted-foreground">
              A background task. It takes its instructions from{" "}
              <Link href={`/chat/${parentId}`} className="text-foreground underline underline-offset-4">
                the chat that started it
              </Link>
              .
            </p>
            {streaming && (
              <Button type="button" variant="outline" onClick={() => void stop()}>
                Stop
              </Button>
            )}
          </div>
        ) : (
        <form
          className="mx-auto flex w-full max-w-3xl items-end gap-2 px-4 sm:px-8"
          onSubmit={(e) => {
            e.preventDefault();
            sendComposer(e.timeStamp);
          }}
        >
          <label htmlFor="composer" className="sr-only">
            Message Gen9
          </label>
          <div className="flex min-w-0 flex-1 flex-col rounded-3xl border bg-card p-1.5 shadow-sm focus-within:ring-2 focus-within:ring-ring/60">
            <textarea
              id="composer"
              dir="auto"
              ref={textarea}
              value={input}
              onChange={(e) => {
                setInput(e.target.value);
                setFromApp(null);
                saveDraft(owner, chatId.current, e.target.value);
                // The chat has its id now (a file attached made it): no draft left under "new"
                if (chatId.current) saveDraft(owner, null, "");
              }}
              onKeyDown={(e) => {
                if (sends(e.nativeEvent)) {
                  e.preventDefault();
                  sendComposer(e.timeStamp);
                }
              }}
              rows={1}
              aria-describedby={[fromApp && "composer-from", lengthNote(input.trim().length) && "composer-length"].filter(Boolean).join(" ") || undefined}
              disabled={waiting}
              placeholder={
                waiting
                  ? requests.some((r) => r.kind === "retry")
                    ? "Retry above, or stop this answer"
                    : requests.some((r) => r.kind === "approval")
                      ? "Allow or deny the action above to continue"
                      : requests.some((r) => r.kind === "elicitation")
                        ? "Answer the connector above to continue"
                      : "Answer the question above to continue"
                  : "Give Gen9 a task"
              }
              className="field-sizing-content max-h-48 min-h-11 w-full resize-none bg-transparent px-3 py-2.5 text-base outline-hidden placeholder:text-muted-foreground"
            />
            {fromApp && (
              <p id="composer-from" className="px-3 pb-1 text-xs text-muted-foreground">
                From {fromApp}’s app, not written by you. Read it before you send it.
              </p>
            )}
            {(() => {
              const note = lengthNote(input.trim().length);
              return note ? (
                <p id="composer-length" aria-live="polite" className={cn("px-3 pb-1 text-xs", note.over ? "text-destructive" : "text-muted-foreground")}>
                  {note.text}
                </p>
              ) : null;
            })()}
            {attachments.length > 0 && (
              <ul aria-label="Attached files" className="flex flex-wrap gap-1.5 px-2 pb-1.5">
                {attachments.map((a) => (
                  <li key={a.key} className="flex items-center gap-1.5 rounded-full border bg-background py-1 pr-1 pl-3 text-sm">
                    {!a.id && !a.problem && <Spinner className="size-3.5" aria-label="Attaching" />}
                    <span className={cn("max-w-48 truncate", a.problem && "text-destructive")}>{a.name}</span>
                    <span className="text-xs text-muted-foreground">{a.problem ?? sizeOf(a.size)}</span>
                    <Button type="button" variant="ghost" size="icon" className="size-6 rounded-full" aria-label={`Remove ${a.name}`} onClick={() => unattach(a)}>
                      <HugeiconsIcon icon={Cancel01Icon} strokeWidth={2} className="size-3.5" />
                    </Button>
                  </li>
                ))}
              </ul>
            )}
            <div className="flex items-center justify-between gap-2">
              <div className="flex min-w-0 items-center gap-0.5">
              <input
                ref={picker}
                type="file"
                multiple
                className="sr-only"
                tabIndex={-1}
                aria-hidden
                onChange={(e) => {
                  void attach(e.target.files);
                  e.target.value = "";
                }}
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="rounded-full text-muted-foreground pointer-coarse:size-11"
                aria-label="Attach files"
                disabled={waiting || streaming}
                onClick={() => picker.current?.click()}
              >
                <HugeiconsIcon icon={Attachment01Icon} strokeWidth={2} />
              </Button>
              <DropdownMenu>
                <DropdownMenuTrigger
                  render={
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      // With large text it wraps rather than running under Send (WCAG 1.4.4: not truncated either)
                      className="h-auto min-h-8 min-w-0 shrink rounded-full py-1 text-left whitespace-normal text-muted-foreground pointer-coarse:min-h-11"
                      aria-label={`Permission mode: ${MODES.find((m) => m.value === mode)?.label}`}
                    />
                  }
                >
                  <span>{MODES.find((m) => m.value === mode)?.label}</span>
                  <HugeiconsIcon icon={ArrowDown01Icon} strokeWidth={2} className="size-4" aria-hidden />
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" side="top" className="w-72">
                  <DropdownMenuRadioGroup value={mode} onValueChange={(value) => setMode(value as PermissionMode)}>
                    {MODES.map((option) => (
                      <DropdownMenuRadioItem key={option.value} value={option.value} className="items-start">
                        <span className="grid gap-0.5">
                          <span className="font-medium">{option.label}</span>
                          <span className="text-xs text-muted-foreground">{option.hint}</span>
                        </span>
                      </DropdownMenuRadioItem>
                    ))}
                  </DropdownMenuRadioGroup>
                </DropdownMenuContent>
              </DropdownMenu>
              </div>
              {streaming ? (
                <Button type="button" size="icon" onClick={() => void stop()} aria-label="Stop">
                  <HugeiconsIcon icon={StopIcon} strokeWidth={2} />
                </Button>
              ) : (
                <Button type="submit" size="icon" disabled={!input.trim() || input.trim().length > MAX_MESSAGE_CHARS || attachments.some((a) => !a.id && !a.problem)} aria-label="Send">
                  <HugeiconsIcon icon={ArrowUp02Icon} strokeWidth={2} />
                </Button>
              )}
            </div>
          </div>
        </form>
        )}
        {/* Before the first question too: people are told they're talking to an AI system at the first
            interaction at the latest, near the input (AI Act Art. 50(1) and (5); the Commission's
            guidelines, 20 July 2026, ¶37 and ¶40) */}
        <p className="mx-auto mt-2 max-w-3xl px-8 text-center text-xs text-muted-foreground">
          Gen9 is an AI system and can be wrong. Check its work before you rely on it.
        </p>
      </div>
      {/* The end of the page: scrolled to, the composer rests below the last answer instead of
          covering it, whatever the composer's height */}
      <div ref={bottom} />

      <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this chat?</AlertDialogTitle>
            <AlertDialogDescription>The conversation and its history are deleted for good.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={deleting}
              onClick={() => threadId && startDelete(() => deleteThread(threadId))}
            >
              Delete chat
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </main>
  );
}
