"""Probe: when does Deep Agents summarize with a model profile, what reaches the stream, and what
stays in state? The router's `chat` alias with `profile={"max_input_tokens": BUDGET}`, and long
user messages until it summarizes.

    uv run --env-file .env --env-file models.local.env python explore/context/summarize_probe.py
"""

import asyncio
from collections import Counter

from deepagents import create_deep_agent
from langgraph.checkpoint.memory import InMemorySaver

from gen9_agent.model_router import chat_model, http_client
from gen9_agent.settings import get_settings

BUDGET = 12_000
FILLER = " ".join(f"line {i}: the tide came in and went out again." for i in range(420))


async def main() -> None:
    settings = get_settings()
    model = chat_model(settings, http_client(), "chat")
    model.profile = {"max_input_tokens": BUDGET}
    agent = create_deep_agent(model=model, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "probe"}}
    for turn in range(4):
        text = (
            f"Turn {turn}. The code word is heron-{turn}. Here is a long note to keep: {FILLER}"
            " Reply with only: noted."
        )
        sources: Counter = Counter()
        answer = ""
        async for part in agent.astream(
            {"messages": [{"role": "user", "content": text}]},
            config,
            stream_mode=["messages", "updates"],
            version="v2",
        ):
            if part["type"] == "messages":
                chunk, meta = part["data"]
                source = meta.get("lc_source") or "-"
                sources[(meta.get("langgraph_node"), source)] += 1
                if source == "-" and getattr(chunk, "text", ""):
                    answer += chunk.text
        state = await agent.aget_state(config)
        event = state.values.get("_summarization_event")
        print(
            f"turn {turn}: messages in state {len(state.values['messages'])}; "
            f"summarization event: {bool(event)}"
            + (f" (cutoff {event.get('cutoff_index')})" if event else "")
            + f"; stream sources {dict(sources)}; answer {answer.strip()[:30]!r}"
        )
    final = await agent.ainvoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "What was the code word in turn 0? Reply with it only.",
                }
            ]
        },
        config,
    )
    print("recall of turn 0:", final["messages"][-1].text[:60])


asyncio.run(main())
