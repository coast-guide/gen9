"""The question tool (questions.py): what the model may ask, what a person's answers must be, and
that the tool pauses the agent and returns the answers when it resumes."""

import uuid

import pytest
from deepagents import create_deep_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import ValidationError

from gen9_agent.questions import (
    MAX_ANSWER_CHARS,
    Question,
    ask_user,
    check_answers,
)

REQUEST = {
    "type": "ask_user",
    "questions": [
        {
            "question": "Which city?",
            "type": "multiple_choice",
            "choices": ["Paris", "Rome"],
        },
        {"question": "Anything else?", "type": "text", "required": False},
    ],
}


def test_a_choice_question_needs_choices_and_a_text_question_none() -> None:
    with pytest.raises(ValidationError):
        Question(question="Which?", type="multiple_choice", choices=["only one"])
    with pytest.raises(ValidationError):
        Question(question="Why?", type="text", choices=["a", "b"])
    Question(question="Which?", type="multiple_choice", choices=["a", "b"])


def test_the_tool_limits_how_much_is_asked() -> None:
    schema = ask_user.tool_call_schema.model_json_schema()
    questions = schema["properties"]["questions"]
    assert (questions["minItems"], questions["maxItems"]) == (1, 4)


def test_answers_are_one_per_question_and_cleaned() -> None:
    assert check_answers(REQUEST, ["  Rome ", ""]) == ["Rome", ""]
    # "Other": any answer to a choice question
    assert check_answers(REQUEST, ["Lisbon", "no"]) == ["Lisbon", "no"]


@pytest.mark.parametrize(
    ("answers", "why"),
    [
        (["Rome"], "expected 2 answers"),
        (["", "fine"], "question 1 needs an answer"),
        (["   ", "fine"], "question 1 needs an answer"),
        (["Rome", "x" * (MAX_ANSWER_CHARS + 1)], "longer than"),
    ],
)
def test_answers_that_dont_fit_are_refused(answers: list[str], why: str) -> None:
    with pytest.raises(ValueError, match=why):
        check_answers(REQUEST, answers)


def test_only_question_requests_can_be_checked() -> None:
    with pytest.raises(ValueError, match="not a question request"):
        check_answers({"action_requests": []}, ["yes"])


class Scripted(BaseChatModel):
    script: list[AIMessage]
    calls: int = 0

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        message = self.script[self.calls]
        self.calls += 1
        return ChatResult(generations=[ChatGeneration(message=message)])

    def bind_tools(self, tools, **kwargs):
        return self

    @property
    def _llm_type(self) -> str:
        return "scripted"


@pytest.mark.asyncio
async def test_the_tool_pauses_the_agent_and_returns_the_answers() -> None:
    questions = [
        {
            "question": "Which city?",
            "type": "multiple_choice",
            "choices": ["Paris", "Rome"],
        }
    ]
    model = Scripted(
        script=[
            AIMessage(
                "",
                tool_calls=[
                    {
                        "name": "ask_user",
                        "args": {"questions": questions},
                        "id": "c1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage("Rome it is."),
        ]
    )
    agent = create_deep_agent(
        model=model, tools=[ask_user], checkpointer=InMemorySaver()
    )
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    paused = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "plan a trip"}]}, config
    )
    [interrupt] = paused["__interrupt__"]
    assert interrupt.value["type"] == "ask_user"
    assert interrupt.value["questions"][0]["choices"] == ["Paris", "Rome"]
    done = await agent.ainvoke(
        Command(resume={interrupt.id: {"answers": ["Rome"]}}), config
    )
    [result] = [m for m in done["messages"] if isinstance(m, ToolMessage)]
    assert result.text == "Q: Which city?\nA: Rome"
    assert done["messages"][-1].text == "Rome it is."
