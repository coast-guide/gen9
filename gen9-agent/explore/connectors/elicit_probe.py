"""Probe: FastMCP 4.0.9 guard tools (return InputRequiredResult, read ctx.input_responses) called
through langchain.mcp's adapter in a LangGraph graph: the interrupt payload, and the resume.

    uv run python explore/connectors/elicit_probe.py
"""

import asyncio
import json
import uuid
import warnings

warnings.filterwarnings("ignore")
from fastmcp import Client, Context, FastMCP
from langchain.mcp import MCPAdapter
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from mcp import types as mt
from typing_extensions import TypedDict

server = FastMCP("probe")


@server.tool
async def plan_trip(ctx: Context) -> str | mt.InputRequiredResult:
    """Plan a trip: asks the person where, and for how many nights."""
    answers = ctx.input_responses
    if not answers:
        form = mt.ElicitRequest(
            params=mt.ElicitRequestFormParams(
                message="Where to, and for how many nights?",
                requested_schema={
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "title": "City"},
                        "nights": {"type": "integer", "minimum": 1, "title": "Nights"},
                    },
                    "required": ["city", "nights"],
                },
            )
        )
        link = mt.ElicitRequest(
            params=mt.ElicitRequestURLParams(
                mode="url",
                message="Connect your calendar",
                url="https://calendar.example.com/connect",
                elicitation_id="cal-1",
            )
        )
        return mt.InputRequiredResult(
            input_requests={"trip": form, "calendar": link}, request_state="round-1"
        )
    trip = answers["trip"]
    return f"state={ctx.request_state} trip={trip.action}:{trip.content} calendar={answers['calendar'].action}"


class S(TypedDict):
    out: str


async def main():
    tools = {t.name: t for t in await MCPAdapter(Client(server)).list_tools()}

    async def node(state: S):
        return {"out": await tools["plan_trip"].ainvoke({})}

    g = StateGraph(S)
    g.add_node("call", node)
    g.add_edge(START, "call")
    g.add_edge("call", END)
    graph = g.compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}
    first = await graph.ainvoke({"out": ""}, cfg)
    interrupts = first.get("__interrupt__") or []
    print(
        "interrupt payload:",
        json.dumps(interrupts[0].value, default=str)[:700] if interrupts else first,
    )
    if interrupts:
        done = await graph.ainvoke(
            Command(
                resume={
                    "responses": {
                        "trip": {
                            "action": "accept",
                            "content": {"city": "Lisbon", "nights": 3},
                        },
                        "calendar": {"action": "decline"},
                    }
                }
            ),
            cfg,
        )
        print("after the answers:", done.get("out"))


asyncio.run(main())
