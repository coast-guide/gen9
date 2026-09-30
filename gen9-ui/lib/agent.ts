import "server-only";

import { headers } from "next/headers";
import { redirect } from "next/navigation";

import { PAGE_HEADER } from "@/lib/auth/page-header";
import type { Session } from "@/lib/auth/session";
import { env } from "@/lib/env";
import { refusal } from "@/lib/refusal";

// Server-side client for the gen9-agent API: every call carries the user's access token (BFF).

/** A chat; `run_status` is its active run's, if it has one ("waiting": it needs the person). */
export type Thread = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  run_status?: "queued" | "running" | "waiting" | null;
  permission_mode?: PermissionMode;
  /** A background task's chat: the chat that started it (gen9-agent's background.py) */
  parent_id?: string | null;
};
/** A task a chat's agent started in the background: its chat, and its latest run's status. */
export type BackgroundTask = {
  id: string;
  title: string;
  status: string | null;
  created_at: string;
  /** Its end was told to the chat (a notice's run), or needs no telling */
  told: boolean;
  /** Its latest run, and what that run waits for the person to answer */
  run_id: string | null;
  requests: InputRequest[];
};
/** A chat's permission mode (gen9-agent's approvals.py): "ask" makes actions wait for Allow or
 * Deny ("Ask before acting"); "auto" acts, and asks only questions ("Act, ask when unsure"). */
export type PermissionMode = "ask" | "auto";
/** A tool the agent used while answering (gen9-agent's `tool.*` run events). `output`: for a
 * question to the person (`ask_user`), what they answered, as the agent read it. */
export type Step = {
  id: string;
  name: string;
  args: unknown;
  status: "running" | "success" | "error" | "declined";
  sources?: Source[];
  output?: string | null;
  /** A connector tool with a View (MCP Apps, gen9-agent's apps.py): shown under the step */
  app?: StepApp | null;
  /** A read of a plugin's skill: the plugin it came from (gen9-agent's plugin_skills.py) */
  plugin?: string | null;
  /** A background task it started: the task's chat (gen9-agent's background.py) */
  task?: string | null;
};
/** A connector tool's View, its arguments, and the result it is sent (MCP's CallToolResult). */
export type StepApp = {
  connector_id: string;
  connector: string;
  resource_uri: string;
  input: Record<string, unknown> | null;
  result: { content: { type: "text"; text: string }[]; structuredContent?: unknown; isError?: boolean };
};
/** A page an answer came from: cited by it, or consulted by one of its steps (gen9-agent's events.py). */
export type Source = { url: string; title?: string | null };
/** An item of the agent's plan (write_todos). */
export type Todo = { content: string; status: "pending" | "in_progress" | "completed" };
/** A file the chat's environment shared (gen9-agent's chat_files.py), downloadable. */
export type ChatFile = { id: string; name: string; size: number; media_type: string };
/** A grader's verdict on a scheduled task's answer, against the task's rubric (gen9-agent's outcomes.py). */
export type Evaluation = {
  result: "satisfied" | "needs_revision" | "failed";
  explanation: string;
  criteria: { criterion: string; met: boolean; why: string }[];
  /** Which try of its firing it graded, from 0 */
  iteration: number;
};
export type Message = {
  role: "user" | "assistant";
  content: string;
  steps?: Step[];
  citations?: Source[];
  files?: ChatFile[];
  evaluation?: Evaluation | null;
  /** On a question: Gen9 wrote it (a background task's notice), not the person */
  notice?: boolean;
  /** On a question: a scheduled task's grader wrote it, its findings for the next try */
  revision?: boolean;
  /** On a turn's first answer: earlier messages were summarized to make room (the context budget) */
  summarized?: boolean;
};
/** A run still active on a thread (queued, running, or waiting for the person): follow it at
 * /api/threads/{id}/runs/{run}/stream */
export type ActiveRun = { id: string; status: "queued" | "running" | "waiting"; message: string; notice?: boolean; revision?: boolean };
/** A question the agent asks mid-task (gen9-agent's questions.py). */
export type Question = { question: string; type: "text" | "multiple_choice"; choices: string[]; required: boolean };
/** An action the agent wants to take, waiting for Allow or Deny (LangChain's HITL request). */
export type Action = { name: string; args: Record<string, unknown>; description?: string };
/** What a waiting run asks the person for (its `input.requested` event); answered at
 * /api/threads/{id}/runs/{run}/inputs/{input id}: `answers` for a question, `decisions` for an
 * approval, `retry` for a run that failed. */
export type InputRequest = QuestionRequest | ApprovalRequest | RetryRequest | ElicitationRequest;
/**
 * A connector's server asking the person mid-call (MCP elicitation; gen9-agent's elicitation.py):
 * a form built from a flat schema, or an address to open. `tool_name` is `<connector>__<tool>`.
 */
export type ElicitationRequest = {
  id: string;
  kind: "elicitation";
  /** The server's own tool name */
  tool_name: string;
  /** The connector whose server asks */
  connector?: string;
  requests: (
    | { key: string; message: string; mode: "form"; requested_schema: FormSchema; order?: string[] }
    | { key: string; message: string; mode: "url"; url: string }
  )[];
};
export type FormSchema = { type?: "object"; properties?: Record<string, FieldSchema>; required?: string[] };
export type FieldSchema = {
  type?: "string" | "number" | "integer" | "boolean" | "array";
  title?: string;
  description?: string;
  format?: "email" | "uri" | "date" | "date-time";
  minLength?: number;
  maxLength?: number;
  minimum?: number;
  maximum?: number;
  minItems?: number;
  maxItems?: number;
  enum?: string[];
  oneOf?: { const: string; title?: string }[];
  anyOf?: { const: string; title?: string }[];
  items?: { enum?: string[]; anyOf?: { const: string; title?: string }[] };
  default?: unknown;
};
/** A run whose turn failed in a way someone can fix waits for Retry; `error` says why. */
export type RetryRequest = { id: string; kind: "retry"; error: string };
export type QuestionRequest = { id: string; kind: "question"; questions: Question[] };
export type ApprovalRequest = {
  id: string;
  kind: "approval";
  action_requests: Action[];
  review_configs: { action_name: string; allowed_decisions: string[] }[];
};
export type ThreadDetail = Thread & { messages: Message[]; todos: Todo[]; active_run: ActiveRun | null; tasks: BackgroundTask[] };
export type Me = { id: string; sub: string; email: string | null; name: string | null; roles: string[]; created_at: string };
/** When a connector's calls wait for Allow (gen9-agent's connectors.py). */
export type ConnectorPolicy = "ask" | "changes" | "never";
/** A secret a person's chat environments send to a host (gen9-agent's api/environment_secrets.py);
 * its value is never returned. */
export type EnvironmentSecret = {
  id: string;
  name: string;
  host: string;
  path: string;
  auth: "bearer" | "header" | "basic";
  header: string | null;
  /** Which requests it goes with: reads (GET, HEAD, OPTIONS), or changes too */
  methods: "read" | "all";
  created_at: string;
};
/** A remote MCP server the person connected; `tools` as they kept them. */
export type Connector = {
  id: string;
  name: string;
  url: string;
  has_token: boolean;
  policy: ConnectorPolicy;
  tools: { name: string; description: string; read_only: boolean }[];
  /** ready; sign_in: waits for the person to sign in at the server; reconnect: their sign-in lapsed */
  status: "ready" | "sign_in" | "reconnect";
  created_at: string;
  /** Where to send the person to sign in, when a sign-in was just started */
  authorize_url?: string | null;
  /** The plugin that brought it (gen9-agent's plugin_connectors.py): it goes with the plugin */
  plugin?: string | null;
  /** Tools the server added or changed since the person kept them, held back until they look:
   * the description now, and before (`was`, null for a new tool) */
  changed: { name: string; description: string; was: string | null; pin: string }[];
};
/** A server in the connector directory: Gen9's copy of an MCP registry, not reviewed by Gen9. */
export type DirectoryEntry = {
  name: string;
  title: string | null;
  description: string;
  url: string;
  /** The header the server needs a secret in, sent with each call, if any */
  header: string | null;
  header_description: string | null;
  repository_url: string | null;
  website_url: string | null;
  status: "active" | "deprecated";
};
/** What Gen9 remembers about the user (gen9-agent's memory.py): Markdown, "" when nothing yet. */
export type Memory = { content: string; updated_at: string | null };
export type Credential = { id: string; label: string | null; created_at_ms: number | null };
export type RecoveryCodes = { id: string; remaining: number | null; total: number | null; created_at_ms: number | null };
export type Security = {
  password_changed_at_ms: number | null;
  authenticator_apps: Credential[];
  passkeys: Credential[];
  recovery_codes: RecoveryCodes | null;
};
export type AdminUser = {
  id: string;
  email: string | null;
  first_name: string | null;
  last_name: string | null;
  enabled: boolean;
  email_verified: boolean;
  created_at_ms: number | null;
  is_admin: boolean;
  required_actions: string[];
  locked: boolean;
};
export type AdminUserPage = { users: AdminUser[]; total: number };
/** Who did what (gen9-agent's audit.py, `GET /v1/admin/audit`), newest first. `actor` and `target` are subs or ids; the emails are Gen9's when it knows the person. */
export type AuditEvent = {
  id: number;
  at: string;
  actor: string;
  actor_email: string | null;
  action: string;
  outcome: "success" | "denied";
  target: string | null;
  target_email: string | null;
  where: string | null;
  detail: Record<string, unknown>;
};
/** What a person lets Gen9 do with their chats (gen9-agent's /v1/me/controls). */
export type Controls = { search_past_chats: boolean; remember: boolean };
/** Which emails a person gets about background runs (gen9-agent's notices.py), and whether this Gen9 sends any. */
export type Notifications = { email: "all" | "needs_you" | "never"; available: boolean };
/** When a scheduled task runs (gen9-agent's tasks.py): times in its time zone; weekday 0 is Monday. */
export type TaskSchedule = {
  kind: "once" | "hourly" | "daily" | "weekdays" | "weekly";
  time: string;
  weekday?: number;
  date?: string;
};
/** A task Gen9 runs on its own (gen9-agent's api/tasks.py). */
export type ScheduledTask = {
  id: string;
  name: string;
  prompt: string;
  schedule: TaskSchedule;
  schedule_words: string;
  time_zone: string;
  permission_mode: "ask" | "auto";
  /** What done looks like: a Markdown rubric each run is graded against, and how many runs a firing may take */
  rubric: string | null;
  max_iterations: number;
  status: "active" | "paused" | "done";
  next_at: string | null;
  skipped: number;
  runs: {
    thread_id: string;
    status: string | null;
    created_at: string;
    /** With a rubric: the chat's latest verdict, how many of its runs were graded, and whether it's still being checked */
    outcome: "satisfied" | "needs_revision" | "failed" | null;
    graded: number;
    checking: boolean;
  }[];
  /** It has an API trigger (a token); the address to POST to */
  has_trigger: boolean;
  fire_url: string;
  created_at: string;
};
/** A plugin a person may add, or has (gen9-agent's api/my_plugins.py). */
export type MyPlugin = {
  id: string;
  name: string;
  title: string | null;
  description: string | null;
  version: string | null;
  source: string;
  skills: { name: string; description: string }[];
  connectors: number;
  added: boolean;
  for_everyone: boolean;
  /** It changed since an admin chose who may have it: its skills and connectors wait for them */
  waiting: boolean;
};
/** A skill a person's chats can use: Gen9's own (plugin null) or a plugin's. */
export type MySkill = { name: string; description: string; plugin: string | null };
/** A git repository plugins come from (gen9-agent's plugin_sources.py), for admins. */
export type PluginSource = {
  id: string;
  url: string;
  ref: string | null;
  name: string | null;
  description: string | null;
  format: "codex" | "claude" | null;
  commit: string | null;
  status: "pending" | "synced" | "failed";
  error: string | null;
  synced_at: string | null;
  /** When a sync last succeeded: a failed source still offers what it synced then */
  succeeded_at: string | null;
  plugins: number;
};
/** Who may have a plugin: nobody, people who add it, or everyone. */
export type PluginAvailability = "off" | "available" | "installed";
/** A plugin a source lists, as loaded (gen9-agent's plugins.py), for admins. */
export type AdminPlugin = {
  id: string;
  source_id: string;
  name: string;
  title: string | null;
  description: string | null;
  version: string | null;
  format: "agent-plugins" | "codex" | "claude" | null;
  status: "loaded" | "rejected" | "unsupported" | "failed";
  reason: string | null;
  availability: PluginAvailability;
  skills: { name: string; description: string }[];
  mcp_servers: { name: string; type: string; connects: boolean; url: string | null }[];
  skipped: { what: string; why: string }[];
  notes: string[];
  synced_at: string;
  /** The commit its files came from */
  commit: string | null;
  /** What it is now (gen9-agent's plugin_skills.fingerprint): an admin's choice names it */
  fingerprint: string | null;
  /** It changed since an admin chose who may have it: nobody gets it until they look again */
  changed: boolean;
  /** The files Gen9 keeps of it (its skills'), for the admin to read */
  files: { path: string; size: number }[];
};
/** One of a plugin's kept files, as an admin reads it: `text` null when it isn't text; `cut` when
 * only its start is here. */
export type PluginFileContent = { path: string; size: number; text: string | null; cut: boolean };
/** How /v1/search finds chats: hybrid (words and meaning), keyword, semantic, fuzzy (titles). */
export type SearchMode = "hybrid" | "keyword" | "semantic" | "fuzzy";
/** A chat found by /v1/search; one per matching run, so a chat can come back more than once. */
export type SearchHit = {
  thread_id: string;
  title: string;
  run_id: string | null;
  snippet: string | null;
  score: number;
  ranked_by: SearchMode | "rerank";
  created_at: string;
};

export class AgentError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function agentFetch(session: Pick<Session, "accessToken">, path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${session.accessToken}`);
  if (!headers.has("Accept")) headers.set("Accept", "application/json");
  return fetch(new URL(path, env().GEN9_AGENT_URL), { ...init, headers, cache: "no-store" });
}

export async function agentJson<T>(session: Session, path: string, init: RequestInit = {}): Promise<T> {
  const response = await agentFetch(session, path, init);
  // The API rejected a token the session considered valid: the Keycloak session has ended. Sign
  // in again and come back to the page (the proxy names it; /auth/login checks it's Gen9's)
  if (response.status === 401) {
    const page = (await headers()).get(PAGE_HEADER) ?? "/chat";
    redirect(`/auth/login?returnTo=${encodeURIComponent(page)}`);
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new AgentError(response.status, refusal((body as { detail?: unknown } | null)?.detail, response.statusText));
  }
  return (response.status === 204 ? undefined : await response.json()) as T;
}
