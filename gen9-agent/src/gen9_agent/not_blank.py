"""Text that must say something: `min_length=1` lets a message of spaces through, and one started
a run the model answered and whose end then failed, three times (gen9-learn.md, M9, F8). Django's
forms strip a field before its required check for the same reason (CharField, strip=True).

The value itself is kept as sent: a message's own spacing is the person's. The refusal is a
ValueError whose text is for people, which gen9-ui shows as it is (lib/agent.ts)."""

from typing import Annotated

from pydantic import AfterValidator, Field


def not_blank(what: str):
    """A validator refusing text that is only whitespace, saying `what` to write."""

    def check(value: str | None) -> str | None:
        # None is an optional field left out, not a blank one
        if value is not None and not value.strip():
            raise ValueError(what)
        return value

    return AfterValidator(check)


# A message to Gen9: 1 to 8,000 characters, not only whitespace. The length comes before the
# check, as a string's, so a longer one is refused in Pydantic's words for text ("String should
# have at most 8000 characters"); after it, Pydantic counted "items" (manual-e2e.md, P8-Z4f)
MessageText = Annotated[
    str, Field(min_length=1, max_length=8000), not_blank("Write a message first.")
]
TASK_NAME = not_blank("Name the task.")
TASK_PROMPT = not_blank("Say what Gen9 should do.")
