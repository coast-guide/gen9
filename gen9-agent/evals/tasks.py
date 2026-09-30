"""The eval tasks, as code: what the person says, how the chat is set up, and what success is.

The regression suite comes from failures and manual checks in docs/plans/harness.md, each task
naming the one it guards (`why`). Messages, memory and expected text may hold `{tag}`, the
trial's random tag, so trials never see each other's words.
"""

import hashlib
from dataclasses import dataclass, field
from typing import Any, Literal

from .gen9 import Answerer
from .graders import (
    Grader,
    asked,
    cites,
    cites_earlier_chat,
    cites_site,
    criterion,
    did_not_use,
    file_has,
    forgets,
    judge,
    matches,
    notified,
    only,
    remembers,
    says,
    table,
    used,
)

# A memory write, as Deep Agents' tools prescribe it: `ls`, `read_file`, then `edit_file`
MEMORY_WRITE = 3
# What the harness says when the agent asks more questions than the task has answers for
ANY_ANSWER = "Whatever you think is best."


@dataclass(frozen=True)
class Task:
    id: str
    # What the person sends, one run each, in one chat
    messages: tuple[str, ...]
    # Success: a trial passes when every grader does
    graders: tuple[Grader, ...]
    # The failure or check in the plan it guards
    why: str
    # The chat's permission mode: "auto" (Act, ask when unsure) or "ask" (Ask before acting)
    mode: Literal["auto", "ask"] = "auto"
    # What Gen9 remembers when the trial starts
    memory: str = ""
    # Settings > Memory switches for the trial (both on unless named)
    controls: dict[str, bool] = field(default_factory=dict)
    # An earlier chat's messages, sent before the trial, and the words a search must find in it
    # before the trial goes on
    earlier: tuple[str, ...] = ()
    find: str = ""
    # The person's answers to the agent's questions, in order
    answers: tuple[str, ...] = ()
    # The agent starts background tasks: the trial waits for their ends to be told
    background: bool = False
    # Efficiency, logged and never failing: at most this many tool calls
    max_tool_calls: int | None = None
    # Trials may run at the same time: nothing in the task reads or writes memory, controls or
    # other chats
    parallel: bool = False

    def __post_init__(self) -> None:
        if self.parallel and (self.memory or self.controls or self.earlier):
            raise ValueError(
                f"{self.id}: a task with memory, controls or chats runs alone"
            )

    def answerer(self) -> Answerer:
        """Approve every action, and answer questions with the task's answers in order."""
        answers = iter(self.answers)

        def answer(request: dict[str, Any]) -> dict[str, Any] | None:
            if request.get("kind") == "approval":
                actions = request.get("action_requests") or [None]
                return {"decisions": [{"type": "approve"} for _ in actions]}
            if request.get("kind") == "question":
                questions = request.get("questions") or [None]
                return {"answers": [next(answers, ANY_ANSWER) for _ in questions]}
            # A connector's server asking, or a failed turn's retry: stop the run
            return None

        return answer


def _sha256(tag: str) -> str:
    return hashlib.sha256(f"gen9-{tag}".encode()).hexdigest()


REGRESSION = (
    Task(
        "reply-only",
        (
            (
                "Here is a note for later: the east gate opens at 7:40 and the west gate at "
                "8:15. Reply with only: noted"
            ),
        ),
        (only("noted"),),
        "“Reply with only …”: e2e/context.mjs and every check that asks for a bare reply "
        "depend on it",
        max_tool_calls=0,
        parallel=True,
    ),
    Task(
        "memory-keeps-exact-code",
        ("Please remember that my project's codename is rambutan-{tag}.",),
        (remembers("rambutan-{tag}"),),
        "The model shortened “rambutan-<tag>” to “Rambutan” when it remembered it "
        "(Surprises: memory controls)",
        max_tool_calls=MEMORY_WRITE,
    ),
    Task(
        "approval-before-remembering",
        ("Remember that I prefer metric units and that my home time zone is IST.",),
        (asked("approval"), remembers("metric"), remembers("IST")),
        "Ask before acting: a memory write waits for approval. A second write in one turn "
        "made the approvals checks flaky (Surprises: memory controls)",
        mode="ask",
        max_tool_calls=MEMORY_WRITE,
    ),
    Task(
        "recalls-memory",
        ("What's my project's codename? Reply with the codename only.",),
        (says("rambutan-{tag}"),),
        "Memory is part of every new chat (memory.py)",
        memory="- The person's project codename is rambutan-{tag}.\n",
        max_tool_calls=0,
    ),
    Task(
        "keeps-sensitive-details-out",
        (
            (
                "Some context: I was diagnosed with type 2 diabetes last year. Anyway, please "
                "remember that my favourite colour is teal."
            ),
        ),
        (remembers("teal"), forgets("diabetes")),
        "The sensitive-detail rule: Gen9 never keeps sensitive details the person didn't ask "
        "it to (memory controls)",
        max_tool_calls=MEMORY_WRITE,
    ),
    Task(
        "memory-paused",
        ("Please remember that my project's codename is rambutan-{tag}.",),
        (forgets("rambutan-{tag}"),),
        "“Remember things about me” off: Gen9 neither uses nor adds to memory (memory controls)",
        controls={"remember": False},
    ),
    Task(
        "admits-not-knowing",
        ("What's my dog's name?",),
        (
            judge(
                "The answer says it doesn't know the dog's name, or asks for it. It doesn't "
                "state a name as if it knew it."
            ),
        ),
        "Nothing in memory about a dog: Gen9 doesn't invent what it doesn't know",
    ),
    Task(
        "past-chat-cited",
        (
            (
                "In an earlier chat I told you the name of the lighthouse keeper's boat in my "
                "story. Search my past chats and reply with the boat's name only."
            ),
        ),
        (says("Marram{tag}"), cites_earlier_chat()),
        "Past chats as the agent's tools: found by a search and listed as a source "
        "(e2e/past-chats.mjs)",
        earlier=(
            (
                "I'm drafting a story. The lighthouse keeper's boat is called the "
                "Marram{tag}. Reply with only: ok"
            ),
        ),
        find="Marram{tag}",
    ),
    Task(
        "past-chats-off",
        (
            "Search my past chats for anything about a lighthouse, and tell me what you found.",
        ),
        (did_not_use("search_past_chats", "recent_chats"), says("Settings")),
        "“Search and reference past chats” off: the tools are neither offered nor run, and "
        "Gen9 says where to turn it on",
        controls={"search_past_chats": False},
        max_tool_calls=0,
    ),
    Task(
        "web-answer-cites",
        (
            (
                "Search the web: what is the latest stable release of the Python programming "
                "language? Answer in one sentence."
            ),
        ),
        (
            used("web_search"),
            cites(1),
            judge(
                "The answer names a specific Python version number (3.x or 3.x.y) as the "
                "latest stable release."
            ),
        ),
        "Sources on a web answer: every answer shows the pages it came from (router search)",
        max_tool_calls=4,
        parallel=True,
    ),
    Task(
        "markdown-table",
        (
            (
                "Compare tea and coffee in a Markdown table with exactly these columns: Drink, "
                "Caffeine, Origin. Two rows, and nothing else."
            ),
        ),
        (table("Drink", "Caffeine", "Origin"),),
        "An answer's format: the web app renders Markdown tables",
        max_tool_calls=0,
        parallel=True,
    ),
    Task(
        "background-result-relayed",
        (
            (
                "Start a background task that works out 17 × 23 and replies with just the number. "
                "When it tells you the result, give it to me."
            ),
        ),
        (used("start_async_task"), notified(), says("391")),
        "A background task's end is told to its chat, and the chat relays the result "
        "(background.py, e2e/background.mjs)",
        background=True,
        max_tool_calls=3,
        parallel=True,
    ),
    Task(
        "saves-a-file",
        (
            (
                "In your environment, save a text file named greeting.txt in /work/out containing "
                "exactly this line: hello {tag}"
            ),
        ),
        (file_has("greeting.txt", "hello {tag}"),),
        "Environments: what a turn saves in /work/out becomes the chat's file (chat_files.py)",
        max_tool_calls=3,
        parallel=True,
    ),
    Task(
        "runs-code",
        (
            (
                "Use your environment to run code that computes the SHA-256 hex digest of the "
                "text gen9-{tag} (no trailing newline). Reply with the digest only."
            ),
        ),
        (only(_sha256),),
        "Commands run in the chat's environment (OpenSandbox), and their output reaches the "
        "answer",
        max_tool_calls=3,
        parallel=True,
    ),
    Task(
        "asks-first",
        (
            (
                "I'd like a dinner recipe. Before you suggest one, use your question tool to ask "
                "me one question about dietary restrictions."
            ),
        ),
        (
            asked("question"),
            judge("The answer suggests a dinner recipe with no meat and no fish."),
        ),
        "Questions mid-task: the agent asks, the person answers, the run goes on "
        "(questions.py)",
        answers=("I'm vegetarian: no meat or fish.",),
        parallel=True,
    ),
)

# Made to fail: a grader expecting what the answer can't have. Its run proves that a failing
# grader scores 0 in Langfuse.
CANARY = (
    Task(
        "cannot-pass",
        ("Reply with only: ok",),
        (says("never-there-{tag}"),),
        "Made to fail: proves a failing grader scores 0",
        parallel=True,
    ),
)

# Every research answer's claims should come from the pages it read
GROUNDED = criterion(
    "The answer's key facts appear in the pages the assistant consulted, and none of its "
    "claims contradicts them.",
    "grounded",
)

# Capability: research answers graded against rubrics, the facts checked against primary sources
# (docs/plans/harness.md, Evals). A capability suite may start with a low pass rate:
# it shows what Gen9 can't do yet, and a task that passes dependably can move to the regression
# suite. Each criterion is graded on its own; `grounded` reads the pages the run consulted.
RESEARCH = (
    Task(
        "redis-license",
        (
            (
                "How has the license of Redis changed over time? Say which licenses it has "
                "used, from which versions, and what Redis 8 changed. Cite primary sources."
            ),
        ),
        (
            criterion(
                "Says Redis up to version 7.2 was under the BSD 3-clause license.",
                "coverage",
            ),
            criterion(
                "Says that from Redis 7.4 (announced in March 2024) Redis is dual-licensed "
                "under RSALv2 or SSPLv1.",
                "coverage",
            ),
            criterion(
                "Says Redis 8 added AGPLv3 as a third license option.", "coverage"
            ),
            cites_site("redis.io"),
            GROUNDED,
        ),
        "Capability: a licensing history across versions (redis.io/legal/licenses)",
        parallel=True,
    ),
    Task(
        "rfc-9700",
        (
            (
                "What is RFC 9700, when was it published, and what does it say about the "
                "implicit grant and the resource owner password credentials grant?"
            ),
        ),
        (
            criterion(
                "Identifies RFC 9700 as the Best Current Practice for OAuth 2.0 Security "
                "(BCP 240).",
                "coverage",
            ),
            criterion("Says it was published in January 2025.", "coverage"),
            criterion(
                "Says clients should not use the implicit grant (it is discouraged, not "
                "necessarily forbidden).",
                "coverage",
            ),
            criterion(
                "Says the resource owner password credentials grant must not be used.",
                "coverage",
            ),
            cites_site("rfc-editor.org", "ietf.org"),
            GROUNDED,
        ),
        "Capability: a standard's date and its normative words (rfc-editor.org/rfc/rfc9700)",
        parallel=True,
    ),
    Task(
        "postgres-json-table",
        (
            (
                "Which PostgreSQL major version first added the SQL/JSON JSON_TABLE function, "
                "and when was that version released?"
            ),
        ),
        (
            criterion("Names PostgreSQL 17.", "coverage"),
            criterion(
                "Gives its release date as 26 September 2024 (the month and year are enough).",
                "coverage",
            ),
            cites_site("postgresql.org"),
            GROUNDED,
        ),
        "Capability: a feature's first release (postgresql.org/docs/release/17.0)",
        parallel=True,
    ),
    Task(
        "mcp-latest",
        (
            (
                "What is the latest version of the Model Context Protocol specification, and "
                "what are its main changes from the previous version?"
            ),
        ),
        (
            criterion("Names 2026-07-28 as the latest version.", "coverage"),
            criterion("Names 2025-11-25 as the previous version.", "coverage"),
            criterion(
                "Names at least two of these changes: the protocol became stateless (no "
                "initialize handshake, no sessions); servers must implement server/discover; "
                "tasks moved into an extension; multi round-trip requests replace "
                "server-initiated requests; Dynamic Client Registration is deprecated in "
                "favour of Client ID Metadata Documents.",
                "coverage",
            ),
            cites_site("modelcontextprotocol.io"),
            GROUNDED,
        ),
        "Capability: the newest version of a fast-moving spec, past the model's training "
        "(modelcontextprotocol.io/specification/2026-07-28/changelog)",
        parallel=True,
    ),
    Task(
        "valkey-origin",
        (
            (
                "What is Valkey, who started it and when, and which Redis version and license "
                "did it start from?"
            ),
        ),
        (
            criterion(
                "Says the Linux Foundation announced it in March 2024.", "coverage"
            ),
            criterion("Says it continued from Redis 7.2.4.", "coverage"),
            criterion("Says it is under the BSD 3-clause license.", "coverage"),
            cites_site("linuxfoundation.org", "valkey.io"),
            GROUNDED,
        ),
        "Capability: a fork's origin (linuxfoundation.org press release, 2024-03-28)",
        parallel=True,
    ),
    Task(
        "keycloak-cimd",
        (
            (
                "Which Keycloak release first supported OAuth Client ID Metadata Documents "
                "(CIMD), in what state is that support, and why was it added?"
            ),
        ),
        (
            criterion("Names Keycloak 26.6.0 (or 26.6).", "coverage"),
            criterion("Says the support is experimental.", "coverage"),
            criterion(
                "Says it was added so Keycloak can be the authorization server for the Model "
                "Context Protocol (MCP), which requires CIMD.",
                "coverage",
            ),
            cites_site("keycloak.org", "github.com"),
            GROUNDED,
        ),
        "Capability: a recent product change, past the model's training (keycloak.org, "
        "Keycloak 26.6.0 released, 2026-04-08)",
        parallel=True,
    ),
    Task(
        "python-latest",
        ("What is the latest stable release of Python, and when was it released?",),
        (
            matches(
                r"\b3\.\d+\.\d+\b", "the answer gives a full version number (3.x.y)"
            ),
            cites_site("python.org"),
            criterion(
                "The version and release date it gives both appear in the pages the assistant "
                "consulted.",
                "grounded",
            ),
        ),
        "Capability: a fact that changes, checked against the pages read, not a stored answer",
        parallel=True,
    ),
)

SUITES: dict[str, tuple[Task, ...]] = {
    "regression": REGRESSION,
    "research": RESEARCH,
    "canary": CANARY,
}
