"""What Gen9 generated is marked so, machine-readable, wherever a message or a file is given out:
the API and the export (AI Act Art. 50(2); docs/ai-act.md; manual-e2e.md, P5-B2)."""

import uuid
from types import SimpleNamespace

from gen9_agent.api.export import README
from gen9_agent.api.threads import ChatFileOut, MessageOut, with_files


def test_answers_are_marked_as_generated_and_questions_are_not() -> None:
    assert MessageOut(role="assistant", content="An answer").model_dump(mode="json")[
        "ai_generated"
    ]
    asked = MessageOut(role="user", content="A question").model_dump(mode="json")
    assert asked["ai_generated"] is False
    # A background task's notice is Gen9's words, but templated, not generated
    notice = MessageOut(role="user", content="Task done", notice=True)
    assert notice.model_dump(mode="json")["ai_generated"] is False


def test_files_gen9_made_are_marked_and_attachments_are_not() -> None:
    run = str(uuid.uuid4())
    row = lambda name, origin: SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        size=1,
        media_type="text/plain",
        run_id=run,
        origin=origin,
    )
    messages = with_files(
        [
            MessageOut(role="user", content="Summarize it", run_id=run),
            MessageOut(role="assistant", content="Done: summary.md", run_id=run),
        ],
        [row("notes.txt", "upload"), row("summary.md", "output")],
    )
    attached = messages[0].model_dump(mode="json")["files"]
    made = messages[1].model_dump(mode="json")["files"]
    assert [(f["name"], f["origin"], f["ai_generated"]) for f in attached] == [
        ("notes.txt", "upload", False)
    ]
    assert [(f["name"], f["origin"], f["ai_generated"]) for f in made] == [
        ("summary.md", "output", True)
    ]


def test_the_export_says_what_the_mark_means() -> None:
    assert '"ai_generated": true' in README
    assert "Art. 50(2)" in README
    assert ChatFileOut(id="1", name="a", size=1, media_type="t").origin == "upload"
