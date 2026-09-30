"""A thread's messages as the user saw them, rebuilt from the agent's checkpoint."""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from gen9_agent.api.threads import (
    Evaluation,
    MessageOut,
    conversation,
    with_evaluations,
    with_unfinished,
)


def search_block(query, status="completed"):
    return {
        "type": "web_search_call",
        "id": "ws1",
        "status": status,
        "action": {"type": "search", "query": query},
    }


def test_each_turn_carries_the_tools_it_used_on_its_first_answer():
    values = {
        "messages": [
            HumanMessage("Compare Paris and Tokyo"),
            AIMessage(
                content="",
                tool_calls=[{"id": "c1", "name": "lookup", "args": {"city": "Paris"}}],
            ),
            ToolMessage(content="2.1 million", tool_call_id="c1", name="lookup"),
            AIMessage(
                content="",
                tool_calls=[{"id": "c2", "name": "lookup", "args": {"city": "Tokyo"}}],
            ),
            ToolMessage(
                content="boom", tool_call_id="c2", name="lookup", status="error"
            ),
            AIMessage(content="Tokyo is larger."),
            HumanMessage("Latest Postgres?"),
            AIMessage(
                content=[
                    search_block("postgres latest"),
                    {"type": "text", "text": "18.1"},
                ]
            ),
        ]
    }
    out = conversation(values)
    assert [(m.role, m.content) for m in out] == [
        ("user", "Compare Paris and Tokyo"),
        ("assistant", "Tokyo is larger."),
        ("user", "Latest Postgres?"),
        ("assistant", "18.1"),
    ]
    assert [(s.name, s.args, s.status) for s in out[1].steps] == [
        ("lookup", {"city": "Paris"}, "success"),
        ("lookup", {"city": "Tokyo"}, "error"),
    ]
    assert [(s.name, s.args) for s in out[3].steps] == [
        ("web_search", {"query": "postgres latest"})
    ]


def test_a_stopped_turn_still_shows_its_tools():
    values = {
        "messages": [
            HumanMessage("Look it up"),
            AIMessage(
                content="", tool_calls=[{"id": "c1", "name": "lookup", "args": {}}]
            ),
        ]
    }
    [_question, answer] = conversation(values)
    assert answer.content == "" and [(s.name, s.status) for s in answer.steps] == [
        ("lookup", "running")
    ]


def test_no_messages():
    assert conversation({}) == []


def test_answers_keep_their_sources_whatever_the_model_wrote():
    """A search's consulted pages (`action.sources`) go on its step, the answer's `url_citation`
    annotations on the answer, and the router search's results (its artifact) on its step."""
    search = {
        "type": "web_search_call",
        "id": "ws9",
        "status": "completed",
        "action": {
            "type": "search",
            "query": "valkey release",
            "sources": [
                {"type": "url", "url": "https://valkey.io/download/releases/"},
                {"type": "api", "name": "oai-weather"},  # not a page: skipped
                {"type": "url", "url": "https://github.com/valkey-io/valkey/releases"},
            ],
        },
    }
    cited = {
        "type": "text",
        "text": "9.1.2 ([valkey.io](https://valkey.io/?utm_source=openai)).",
        "annotations": [
            {
                "type": "url_citation",
                "url": "https://valkey.io/?utm_source=openai",
                "title": "Valkey",
            },
            {
                "type": "url_citation",
                "url": "https://valkey.io/?utm_source=openai",
                "title": "Valkey",
            },
        ],
    }
    values = {
        "messages": [
            HumanMessage("Latest Valkey?"),
            AIMessage(content=[search, cited]),
            HumanMessage("And via the router?"),
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "r1", "name": "web_search", "args": {"query": "valkey"}}
                ],
            ),
            ToolMessage(
                content='[{"title": "Valkey", "url": "https://valkey.io/"}]',
                artifact=[
                    {"title": "Valkey", "url": "https://valkey.io/", "snippet": "…"}
                ],
                tool_call_id="r1",
                name="web_search",
            ),
            AIMessage(content="9.1.2"),
        ]
    }
    first, second = [m for m in conversation(values) if m.role == "assistant"]
    assert [s.url for s in first.steps[0].sources] == [
        "https://valkey.io/download/releases/",
        "https://github.com/valkey-io/valkey/releases",
    ]
    assert [(c.url, c.title) for c in first.citations] == [
        ("https://valkey.io/?utm_source=openai", "Valkey")
    ]
    assert [(s.url, s.title) for s in second.steps[0].sources] == [
        ("https://valkey.io/", "Valkey")
    ]
    assert second.citations == []


def test_a_question_to_the_person_keeps_their_answer_other_steps_no_output():
    values = {
        "messages": [
            HumanMessage("Plan a trip"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "q1",
                        "name": "ask_user",
                        "args": {"questions": [{"question": "Which city?"}]},
                    },
                    {"id": "c1", "name": "lookup", "args": {"city": "Rome"}},
                ],
            ),
            ToolMessage(
                content="Q: Which city?\nA: Rome", tool_call_id="q1", name="ask_user"
            ),
            ToolMessage(content="2.8 million", tool_call_id="c1", name="lookup"),
            AIMessage(content="Rome, then."),
        ]
    }
    [_, answer] = conversation(values)
    asked, looked_up = answer.steps
    assert (asked.name, asked.output) == ("ask_user", "Q: Which city?\nA: Rome")
    assert looked_up.output is None


def test_a_denied_action_is_a_declined_step_with_the_reason():
    values = {
        "messages": [
            HumanMessage("Remember teal"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "e1",
                        "name": "edit_file",
                        "args": {"file_path": "/memories/AGENTS.md"},
                    }
                ],
            ),
            ToolMessage(
                content="User rejected the tool call for `edit_file` with reason: no",
                tool_call_id="e1",
                name="edit_file",
                status="error",
            ),
            AIMessage(content="I won't remember it."),
        ]
    }
    [_, answer] = conversation(values)
    [step] = answer.steps
    assert step.status == "declined" and step.output.endswith("with reason: no")


def test_a_graded_turns_last_answer_carries_the_verdict():
    messages = conversation(
        {
            "messages": [
                HumanMessage("Write the report", id="run-r1"),
                AIMessage("Working on it."),
                AIMessage("The report."),
                HumanMessage("Fix it", id="run-r2"),
                AIMessage("The fixed report."),
            ]
        }
    )
    verdict = Evaluation(
        result="needs_revision", explanation="No dates.", criteria=[], iteration=0
    )
    graded = with_evaluations(messages, {"r1": verdict})
    assert [m.evaluation for m in graded] == [None, None, verdict, None, None]


def test_a_turn_that_ended_before_writing_anything_has_an_empty_answer():
    """Stopped at once, failed or expired with nothing written: an empty answer, which clients
    show as "This answer didn't finish." (P2-K1); a turn with an answer or steps is left as is."""
    q = lambda run_id: MessageOut(role="user", content=run_id, run_id=run_id)
    a = MessageOut(role="assistant", content="Done.")
    shown = with_unfinished([q("r1"), q("r2"), a, q("r3")], ended={"r1", "r2", "r3"})
    assert [(m.role, m.content) for m in shown] == [
        ("user", "r1"),
        ("assistant", ""),
        ("user", "r2"),
        ("assistant", "Done."),
        ("user", "r3"),
        ("assistant", ""),
    ]
    assert with_unfinished([q("r4")], ended=set()) == [q("r4")]
