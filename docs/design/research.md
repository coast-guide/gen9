# UX research

What the principles, the information architecture and the screens rest on. Primary sources only:
vendors' own guidelines, help centers and repositories, read when each part was designed. Where a vendor's
site refused automated reading (help.openai.com, openai.com), its findings come from search results
quoting its own pages, and say so.

## Guidelines for AI features

**Apple, Human Interface Guidelines: Generative AI** (read through its documentation JSON).
- Keep people in control: they can dismiss, revert or retry what the AI made. Say clearly where AI
  is used.
- Set expectations. Offer curated suggestions for open prompts, and state known limits up front.
- Ask before irreversible or potentially problematic tasks.
- Make results easy to refine or revert, and acknowledge when a correction takes effect. Help
  people rephrase a request that was blocked.
- Plan for processing time: give specific, reassuring feedback while it works.
- Let people give feedback on outputs.
- Privacy: show what's shared, ask before using personal data, and say how it's stored.

**Microsoft, Guidelines for Human-AI Interaction** (Amershi et al., CHI 2019; the HAX Toolkit's 18).

| Phase | Guidelines |
| --- | --- |
| Initially | G1 Make clear what the system can do. G2 Make clear how well it can do it |
| During interaction | G3 Time services on context. G4 Show contextually relevant information. G5 Match social norms. G6 Mitigate social biases |
| When wrong | G7 Efficient invocation. G8 Efficient dismissal. G9 Efficient correction. G10 Scope services when in doubt. G11 Make clear why it did what it did |
| Over time | G12 Remember recent interactions. G13 Learn from behavior. G14 Update and adapt cautiously. G15 Encourage granular feedback. G16 Convey the consequences of actions. G17 Provide global controls. G18 Notify users about changes |

## How the leading agent products do it

**ChatGPT agent** (help.openai.com, "ChatGPT agent", via search):
- asks permission before consequential actions;
- the person can interrupt, take over the browser, or stop at any time;
- pauses to ask when it's unclear;
- a login needs "take over": no screenshots are captured while the person is in control, and the
  agent continues afterwards.

**ChatGPT search** (help.openai.com, "Searching the web with ChatGPT", via search): answers that
used the web carry inline citations (on desktop, pointing at one previews it) and a "Sources"
button under the response that opens a sidebar with the cited sources and other relevant links.
OpenAI's API guide adds a rule for its web search tool: citations "must be made clearly visible
and clickable" (developers.openai.com, "Web search").

**Claude, chat search and memory** (support.claude.com, "Use Claude's chat search and memory").
- Searching past chats appears as a tool call in the conversation, with citations linking to the
  original chats (and a way to delete them).
- The scope is either every chat outside projects, or one project.
- Settings > Memory has two switches: "Search and reference chats" and "Generate memory from
  chats".
- Memory is listed by topic, and each topic can be read, edited or deleted. A project has its own
  memory and summary.
- Memory can be paused or reset. Incognito chats are never saved. Sensitive topics are left out
  unless the person opts in.

**Claude Cowork, scheduled tasks** (support.claude.com, via search).
- Describe the task and how often, in plain words.
- It runs remotely, even with the computer asleep.
- A push notification when it's done, or when it needs a go-ahead.
- "Scheduled" in the left sidebar lists upcoming and past runs.

**Claude in Chrome, permissions** (support.claude.com, via search): a permission mode picked in
the composer, per chat.
- **Manually approve:** Allow or Deny for each action.
- **Automatically approve:** a safety review, which still pauses to ask when needed.
- **Skip all approvals.**

**Cursor, Cloud Agents** (cursor.com/docs, via search).
- A short status under the thread while the agent works.
- A follow-up steers a running agent without cutting it off: it waits for the next tool call.
- A notification when done, with the result.
- Take over the agent's desktop, then hand it back.

## Libraries

| Library | Licence, state | What it shows |
| --- | --- | --- |
| Vercel AI Elements | Apache-2.0, 1.9.0 (2026-03-12), shadcn-based like gen9-ui | 49 agent components: conversation, message, prompt input, reasoning, tool, task, plan, queue, confirmation (approval), question, sources, inline citation, checkpoint, context (token use), artifact, attachments, suggestion, shimmer, code block, terminal, web preview |
| AI SDK tool states | Used by AI Elements | `input-streaming`, `input-available`, `approval-requested`, `approval-responded`, `output-available`, `output-error`, `output-denied`: a shared vocabulary for a step |
| assistant-ui | MIT, 12.3k stars, active | Thread lists, branching, tool UIs, human in the loop |
| LangChain agent-chat-ui | MIT, active | Artifacts in a side panel to the right of the chat |
| CopilotKit and AG-UI | MIT, active | An event protocol between agents and frontends (milestone 6, interop) |

## What Gen9 takes from this

- **The run is visible and steerable:** a live status, steps and a plan, Stop, and follow-ups
  that steer without cutting the run off (Cursor, ChatGPT; HAX G3, G9).
- **Ask before acting:** consequential actions wait for Allow or Deny, and a chat's permission
  mode is visible in the composer (Claude in Chrome, ChatGPT agent; HIG; HAX G16, G17).
- **Search past chats** both as a screen and as a tool the agent calls, with citations that open
  the chat (Claude).
- **Memory you can see:** listed, editable, deletable, can be paused, with incognito chats
  (Claude; HAX G12, G17).
- **Scheduled tasks in their own place:** upcoming and past runs, and a notification when one
  finishes or needs a decision (Claude Cowork; HAX G18).
- **Say what it can and can't do, and why it did something:** suggestions on the empty chat,
  sources on answers, the plan and the steps taken (HIG; HAX G1, G2, G11).
- **Build on shadcn, and borrow AI Elements' component anatomy and the AI SDK's tool-state
  vocabulary.** Copy code, rather than adopt a chat framework: gen9-ui's run model (a durable
  event log with resume) is its own.
