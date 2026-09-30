"""A tiny MCP server for the harness probes: one plain tool, one that asks the user mid-call.

    uv run --no-project --with fastmcp==4.0.9 python explore/harness/mcp_server.py   # -> http://127.0.0.1:18999/mcp

`book_table` asks for a date in the way each protocol era allows: on 2026-07-28 connections it
returns an `InputRequiredResult` and reads the answer when the client retries the call
(multi round-trip requests, SEP-2322); on 2025-11-25 connections it sends `elicitation/create`.
"""

from dataclasses import dataclass

import mcp_types as mt
from fastmcp import Context, FastMCP

mcp = FastMCP("gen9-probe")


@mcp.tool
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


@dataclass
class Booking:
    date: str


@mcp.tool
async def book_table(people: int, ctx: Context) -> str | mt.InputRequiredResult:
    """Book a restaurant table; asks the user which date."""
    # Probe only: the era check is private in FastMCP 4.0.9
    if ctx._is_modern_protocol():
        answers = ctx.input_responses
        if not answers:
            return mt.InputRequiredResult(
                input_requests={
                    "date": mt.ElicitRequest(
                        params=mt.ElicitRequestFormParams(
                            message=f"Which date for {people} people?",
                            requested_schema={
                                "type": "object",
                                "properties": {"date": {"type": "string"}},
                                "required": ["date"],
                            },
                        )
                    )
                }
            )
        answer = answers["date"]
        if answer.action != "accept":
            return f"Booking {answer.action}ed"
        return f"Booked a table for {people} on {answer.content['date']}"
    answer = await ctx.elicit(f"Which date for {people} people?", response_type=Booking)
    if answer.action != "accept":
        return f"Booking {answer.action}ed"
    return f"Booked a table for {people} on {answer.data.date}"


if __name__ == "__main__":
    mcp.run(transport="http", host="127.0.0.1", port=18999)
