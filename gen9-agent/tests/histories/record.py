"""Save a workflow's history as a replay fixture: tests/test_run_workflow.py replays every
fixture here against the current workflow code, so a change that would break workflows already
in flight (or waiting) fails CI. Record one after a workflow of each kind has run on the stacks:

    uv run --env-file .env --env-file postgres.local.env --env-file keycloak.local.env \
      --env-file models.local.env python tests/histories/record.py <workflow id> <name>

Payloads are stored encrypted (codec.py); they are decrypted here, with this machine's keys, so
the tests replay them without any key. Histories hold IDs and model names, never messages
(docs/temporal.md, rule 2).

A fixture runs on a synthetic clock: its first event is moved to 2000-01-01T00:00:00Z, and every
other time in it (event times, times inside payloads, the time-ordered run IDs) keeps its distance
from that. A replay only needs the distances, and a fixture then says nothing about when or where
it was recorded.
"""

import asyncio
import base64
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from google.protobuf.message import Message
from temporalio.converter import PayloadCodec

from gen9_agent.keycloak_admin import KeycloakAdmin
from gen9_agent.settings import get_settings
from gen9_agent.temporal import connect

PAYLOAD = "temporal.api.common.v1.Payload"
CLOCK_START = datetime(2000, 1, 1, tzinfo=UTC)
EVENT_TIME = re.compile(r'"eventTime":\s*"([^".]+)')
TIME = re.compile(r"(20\d\d-\d\d-\d\d)([T ])(\d\d:\d\d:\d\d)")
UUID7 = re.compile(
    r"\b([0-9a-f]{8})-([0-9a-f]{4})(-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\b"
)
PAYLOAD_DATA = re.compile(r'("data":\s*")([A-Za-z0-9+/]{4,}={0,2})(")')


def on_synthetic_clock(history: str) -> str:
    """The history's JSON with every time moved back by the same whole number of seconds, so
    fractions of a second stay as they were."""
    starts = [
        datetime.fromisoformat(f"{t[:19]}+00:00") for t in EVENT_TIME.findall(history)
    ]
    back = min(starts) - CLOCK_START

    def moved(text: str) -> str:
        def time(match: re.Match[str]) -> str:
            at = datetime.fromisoformat(f"{match[1]}T{match[3]}+00:00") - back
            return f"{at:%Y-%m-%d}{match[2]}{at:%H:%M:%S}"

        def run_id(match: re.Match[str]) -> str:
            # UUID version 7: the first 48 bits are milliseconds since 1970
            ms = int(match[1] + match[2], 16) - back // timedelta(milliseconds=1)
            return f"{ms:012x}"[:8] + "-" + f"{ms:012x}"[8:] + match[3]

        return UUID7.sub(run_id, TIME.sub(time, text))

    def payload(match: re.Match[str]) -> str:
        try:
            text = base64.b64decode(match[2]).decode()
        except UnicodeDecodeError:  # not text: no time to move
            return match[0]
        return match[1] + base64.b64encode(moved(text).encode()).decode() + match[3]

    return moved(PAYLOAD_DATA.sub(payload, history))


async def decode_all(message: Message, codec: PayloadCodec) -> None:
    """Decodes, in place, every Payload in `message` and the messages inside it."""
    for field, value in message.ListFields():
        if field.type != field.TYPE_MESSAGE:
            continue
        options = field.message_type.GetOptions()
        if options.map_entry:
            value_field = field.message_type.fields_by_name["value"]
            if value_field.message_type is None:
                continue
            for key in list(value):
                if value_field.message_type.full_name == PAYLOAD:
                    [decoded] = await codec.decode([value[key]])
                    value[key].CopyFrom(decoded)
                else:
                    await decode_all(value[key], codec)
        elif field.message_type.full_name == PAYLOAD:
            items = [value] if isinstance(value, Message) else list(value)
            for item, decoded in zip(items, await codec.decode(items), strict=True):
                item.CopyFrom(decoded)
        elif not isinstance(value, Message):  # a repeated field
            for item in value:
                await decode_all(item, codec)
        else:
            await decode_all(value, codec)


async def main(workflow_id: str, name: str) -> None:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10) as http:
        keycloak = KeycloakAdmin(settings, http)  # the service token Temporal requires
        client = await connect(settings, "history-recorder", keycloak)
        history = await client.get_workflow_handle(workflow_id).fetch_history()
    codec = client.data_converter.payload_codec
    if codec is not None:
        for event in history.events:
            await decode_all(event, codec)
    out = Path(__file__).with_name(f"{name}.json")
    await asyncio.to_thread(out.write_text, on_synthetic_clock(history.to_json()))
    print(f"wrote {out} ({len(history.events)} events)")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
