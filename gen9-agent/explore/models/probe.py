"""Probe: LiteLLM Proxy as Gen9's model router, called the way gen9-agent would.

Needs the router from compose.yaml (./env.sh && docker compose up -d --wait) and gen9-langfuse up.
Run from gen9-agent/: uv run python explore/models/probe.py
Prints what each step observed; secrets never printed.
"""

import asyncio
import base64
import json
import struct
import time
import zlib
from pathlib import Path

import httpx
from deepagents import create_deep_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from openai import APIStatusError, AsyncOpenAI

HERE = Path(__file__).parent
BASE = "http://127.0.0.1:19000"


def env_file(path: Path) -> dict[str, str]:
    lines = path.read_text().splitlines()
    return dict(
        line.split("=", 1) for line in lines if "=" in line and not line.startswith("#")
    )


PROBE = env_file(HERE / ".env")
LANGFUSE = env_file(HERE.parent.parent / "langfuse.local.env")
MASTER = PROBE["LITELLM_MASTER_KEY"]


def red_png(size: int = 32) -> str:
    """A solid red square as a PNG data URL, built in memory."""
    row = b"\x00" + b"\xff\x00\x00" * size
    raw = zlib.compress(row * size)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data))
        )

    png = b"\x89PNG\r\n\x1a\n" + chunk(
        b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    )
    png += chunk(b"IDAT", raw) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(png).decode()


@tool
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


async def main() -> None:
    async with httpx.AsyncClient(
        base_url=BASE, timeout=60, headers={"Authorization": f"Bearer {MASTER}"}
    ) as admin:
        # 0. gen9-agent's virtual key: only the aliases, never vendor names
        r = await admin.post(
            "/key/generate",
            json={
                "key_alias": f"gen9-agent-{int(time.time())}",
                "models": [
                    "chat",
                    "chat-broken",
                    "vision",
                    "embed",
                    "speak",
                    "transcribe",
                ],
            },
        )
        key = r.json()["key"]
        print("0 virtual key:", r.status_code, "models", r.json()["models"])

        chat = ChatOpenAI(
            model="chat",
            base_url=f"{BASE}/v1",
            api_key=key,
            model_kwargs={"user": "probe-sub-1"},
        )

        # 1. a Deep Agent with a tool, streamed
        agent = create_deep_agent(
            model=chat, tools=[add], system_prompt="Use the add tool for arithmetic."
        )
        chunks, tool_calls, t0 = 0, [], time.monotonic()
        async for message, _meta in agent.astream(
            {
                "messages": [
                    {"role": "user", "content": "What is 1234 + 4321? Use the tool."}
                ]
            },
            stream_mode="messages",
        ):
            chunks += 1
            tool_calls += [
                c["name"]
                for c in getattr(message, "tool_call_chunks", []) or []
                if c.get("name")
            ]
        final = (
            await agent.ainvoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": "What is 2 + 40? Use the tool, answer with the number only.",
                        }
                    ]
                }
            )
        )["messages"][-1].content
        print(
            f"1 deep agent: {chunks} streamed chunks, tool calls {tool_calls}, second answer {final!r}, {time.monotonic() - t0:.1f}s"
        )

        # 2. vision input
        vision = ChatOpenAI(model="vision", base_url=f"{BASE}/v1", api_key=key)
        answer = await vision.ainvoke(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "One word: what colour is this image?",
                        },
                        {"type": "image_url", "image_url": {"url": red_png(256)}},
                    ],
                }
            ]
        )
        print("2 vision:", repr(answer.content))

        # 3. embeddings
        embed = OpenAIEmbeddings(
            model="embed",
            base_url=f"{BASE}/v1",
            api_key=key,
            check_embedding_ctx_length=False,
        )
        vectors = await embed.aembed_documents(
            ["Gen9 routes every model.", "A second sentence."]
        )
        print(
            "3 embeddings:", len(vectors), "vectors of", len(vectors[0]), "dimensions"
        )

        # 4. speech, then transcription of that speech
        sdk = AsyncOpenAI(base_url=f"{BASE}/v1", api_key=key)
        speech = await sdk.audio.speech.create(
            model="speak", voice="alloy", input="Gen9 routes every kind of model."
        )
        audio = speech.content
        text = await sdk.audio.transcriptions.create(
            model="transcribe", file=("speech.mp3", audio), response_format="json"
        )
        print(f"4 speech: {len(audio)} bytes; transcribed back: {text.text!r}")

        # 5. fallback: chat-broken always fails upstream, the router falls back to chat
        raw = await sdk.chat.completions.with_raw_response.create(
            model="chat-broken", messages=[{"role": "user", "content": "Say ok."}]
        )
        headers = {
            k: v
            for k, v in raw.headers.items()
            if k.startswith("x-litellm")
            and ("fallback" in k or "model" in k or "retries" in k)
        }
        print("5 fallback:", repr(raw.parse().choices[0].message.content), headers)

        # 6. an end user over budget; another user unaffected
        r = await admin.post(
            "/customer/new", json={"user_id": "probe-over", "max_budget": 0.0000001}
        )
        print("6 customer created:", r.status_code)
        for attempt in (1, 2, 3):
            try:
                await sdk.chat.completions.create(
                    model="chat",
                    user="probe-over",
                    messages=[{"role": "user", "content": "Say ok."}],
                )
                print(f"  attempt {attempt} for probe-over: allowed")
            except APIStatusError as e:
                body = (
                    e.response.json()
                    if e.response.headers.get("content-type", "").startswith(
                        "application/json"
                    )
                    else e.response.text
                )
                print(
                    f"  attempt {attempt} for probe-over: HTTP {e.status_code}",
                    json.dumps(body)[:300],
                )
            await asyncio.sleep(1.5)  # spend is written asynchronously
        ok = await sdk.chat.completions.create(
            model="chat",
            user="probe-ok",
            messages=[{"role": "user", "content": "Say ok."}],
        )
        print("  probe-ok:", repr(ok.choices[0].message.content))
        info = await admin.get("/customer/info", params={"end_user_id": "probe-sub-1"})
        print(
            "  spend recorded for probe-sub-1 (the agent's user):",
            info.status_code,
            info.json().get("spend") if info.status_code == 200 else info.text[:200],
        )

        # 7. Langfuse: what the router sends, and whether a traceparent from the app nests it
        from langfuse import Langfuse
        from langfuse.langchain import CallbackHandler

        langfuse = Langfuse(
            public_key=LANGFUSE["LANGFUSE_PUBLIC_KEY"],
            secret_key=LANGFUSE["LANGFUSE_SECRET_KEY"],
            host=LANGFUSE["LANGFUSE_BASE_URL"],
        )
        with langfuse.start_as_current_observation(
            name="router-probe", as_type="span"
        ) as span:
            trace_id, span_id = span.trace_id, span.id
            handler = CallbackHandler()
            await chat.ainvoke("Say ok.", config={"callbacks": [handler]})
            traced = ChatOpenAI(
                model="chat",
                base_url=f"{BASE}/v1",
                api_key=key,
                default_headers={"traceparent": f"00-{trace_id}-{span_id}-01"},
            )
            await traced.ainvoke("Say ok again.")
        langfuse.flush()
        print("7 langfuse app trace:", trace_id)


if __name__ == "__main__":
    asyncio.run(main())
