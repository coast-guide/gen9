"""Questions the agent asks the person mid-task (human in the loop, docs/plans/harness.md).

`ask_user` pauses the run with a LangGraph interrupt and returns the person's answers when the run
resumes. The run waits in Temporal meanwhile, for days if need be (workflows/runs.py). The shape
follows Deep Agents Code's own `ask_user` (libs/code/deepagents_code/ask_user.py, MIT): an interrupt
value `{"type": "ask_user", "questions": [...]}`, resumed with `{"answers": [...]}`, one answer
per question (explore/hitl/NOTES.md).
"""

from typing import Annotated, Any, Literal

from langchain_core.tools import tool
from langgraph.types import interrupt
from pydantic import BaseModel, Field, ValidationError, model_validator

# The interrupt's `type`, and the kind of input it asks for (`run_inputs.kind`)
INTERRUPT_TYPE = "ask_user"
KIND = "question"
MAX_QUESTIONS = 4
MAX_CHOICES = 6
MAX_QUESTION_CHARS = 500
MAX_ANSWER_CHARS = 2000


class Question(BaseModel):
    question: str = Field(
        min_length=1,
        max_length=MAX_QUESTION_CHARS,
        description="The question, short and specific",
    )
    type: Literal["text", "multiple_choice"] = Field(
        default="text",
        description='"text" for a free answer, "multiple_choice" to pick one of `choices`',
    )
    choices: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list,
        max_length=MAX_CHOICES,
        description='For "multiple_choice": 2 to 6 options. The person can always answer '
        "something else instead.",
    )
    required: bool = Field(
        default=True, description="False if the person may leave it unanswered"
    )

    @model_validator(mode="after")
    def _choices_match_type(self) -> "Question":
        if self.type == "multiple_choice" and len(self.choices) < 2:
            raise ValueError("a multiple_choice question needs 2 to 6 choices")
        if self.type == "text" and self.choices:
            raise ValueError("a text question has no choices")
        return self


DESCRIPTION = f"""Ask the person one or more questions, then wait for their answers. The run \
pauses until they answer, however long that takes.

Ask when you can't go on well without them:
- the request is ambiguous in a way that changes the result;
- there are several reasonable ways to go and the choice is theirs;
- you need a fact only they know.

Don't ask:
- what you can find out yourself, by searching or from the conversation;
- for permission to do what they already asked for;
- about trivial choices: make a sensible one and say so.

Ask at most {MAX_QUESTIONS} questions in one call, related ones together. Use "multiple_choice" when \
there are clear options; the person can always answer something else."""


@tool(description=DESCRIPTION)
async def ask_user(
    questions: Annotated[list[Question], Field(min_length=1, max_length=MAX_QUESTIONS)],
) -> str:
    response = interrupt(
        {"type": INTERRUPT_TYPE, "questions": [q.model_dump() for q in questions]}
    )
    answers = response.get("answers") if isinstance(response, dict) else None
    if not isinstance(answers, list) or len(answers) != len(questions):
        return (
            "The person's answers could not be read. Go on without them, or ask again."
        )
    return "\n\n".join(
        f"Q: {q.question}\nA: {a if a else '(not answered)'}"
        for q, a in zip(questions, answers, strict=True)
    )


def check_answers(request: dict[str, Any], answers: list[str]) -> list[str]:
    """The answers to a question request, cleaned, or ValueError saying what is wrong: one per
    question, each at most MAX_ANSWER_CHARS, and none empty where required. A choice question
    takes any answer, since "Other" is always allowed."""
    try:
        questions = [Question.model_validate(q) for q in request["questions"]]
    except (KeyError, TypeError, ValidationError) as e:
        raise ValueError("not a question request") from e
    if len(answers) != len(questions):
        raise ValueError(f"expected {len(questions)} answers, got {len(answers)}")
    cleaned = []
    for number, (question, answer) in enumerate(
        zip(questions, answers, strict=True), 1
    ):
        answer = answer.strip()
        if len(answer) > MAX_ANSWER_CHARS:
            raise ValueError(
                f"answer {number} is longer than {MAX_ANSWER_CHARS} characters"
            )
        if question.required and not answer:
            raise ValueError(f"question {number} needs an answer")
        cleaned.append(answer)
    return cleaned
