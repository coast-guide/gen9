"""What a capped answer looks like to Gen9's model client (manual-e2e.md, P6-Z1).

Through the running router (gen9-models, the agent's key in models.local.env), one short answer
with a small output cap, streamed and aggregated as the agent's model node does: the stop reason
in `response_metadata`, the text, and the tool calls, plain and with a tool bound. Run from
gen9-agent/: `uv run --env-file models.local.env python explore/models/output_cap.py`
"""

import asyncio
import os

from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

CAP = 60


@tool
def save_note(text: str) -> str:
    """Save a note."""
    return "saved"


async def answer(model: ChatOpenAI, prompt: str) -> None:
    whole = None
    async for chunk in model.astream(prompt):
        whole = chunk if whole is None else whole + chunk
    assert whole is not None
    meta = whole.response_metadata
    print(
        f"  finish_reason={meta.get('finish_reason')!r} status={meta.get('status')!r}"
        f" incomplete={meta.get('incomplete_details')!r}"
    )
    print(f"  usage={whole.usage_metadata}")
    print(f"  text={whole.text[:80]!r}")
    print(
        f"  tool_calls={whole.tool_calls} invalid={[c.get('args', '')[:60] for c in whole.invalid_tool_calls]}"
    )


async def main() -> None:
    model = ChatOpenAI(
        model="chat",
        base_url=f"{os.environ['GEN9_MODELS_URL'].replace('gen9-models', 'localhost').replace(':4000', ':19000')}/v1",
        api_key=os.environ["GEN9_MODELS_KEY"],
        max_tokens=CAP,
        stream_usage=True,
        reasoning_effort="minimal",
    )
    print("text:")
    await answer(model, "Count from 1 to 300, one number per line, nothing else.")
    print("tool:")
    await answer(
        model.bind_tools([save_note], tool_choice="save_note"),
        "Save a note with the numbers from 1 to 300, one per line.",
    )


asyncio.run(main())
