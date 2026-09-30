"""A chat's files (docs/plans/harness.md, Milestone 3, "Files in and out"). Gen9 keeps them in
Postgres (`chat_files`), so they outlive the chat's environment, which lasts 30 minutes unused;
they go with the chat or the account.

- **Out.** The agent shares a file by saving it in OUT_DIR, as its instructions say (agent.py).
  After each turn that used the environment, the worker captures what's new or changed there:
  regular files only, never links, up to three folders deep. The turn's `files.shared` event
  lists them, and its answer offers them for download (api/files.py).
- **In.** A person attaches a file to the chat (api/files.py); the message that names it has
  the worker put it in IN_DIR before its turn, and says so to the model (`attached_note`).
- **Bounds.** 25 MB a file and 250 MB a chat: a file past them is left out, and the log says so.
- Only the worker reaches the environment; the API serves what was captured.
"""

import hashlib
import logging
import mimetypes
import re
import uuid
from typing import Any

from opensandbox.exceptions import SandboxApiException
from opensandbox.models.filesystem import DirectoryListEntry
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from .environments import IN_DIR, OUT_DIR
from .models import ChatFile
from .runtime import Runtime

log = logging.getLogger(__name__)

MAX_FILE = 25 * 1024 * 1024
MAX_CHAT = 250 * 1024 * 1024
# Files a capture looks at, and how deep
MAX_FILES = 100
DEPTH = 3


def media_type(name: str) -> str:
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


async def deliver(
    runtime: Runtime,
    thread_id: uuid.UUID,
    run_id: uuid.UUID,
    user_sub: str,
    ids: list[str],
) -> list[str]:
    """The files a message names, put in the chat's environment (IN_DIR) before its turn: their
    names. Each is marked as the run's once it's there."""
    if not ids or runtime.environments is None:
        return []
    async with runtime.engine.connect() as conn:
        rows = list(
            await conn.execute(
                select(
                    ChatFile.id, ChatFile.path, ChatFile.name, ChatFile.content
                ).where(
                    ChatFile.thread_id == thread_id,
                    ChatFile.origin == "upload",
                    ChatFile.id.in_([uuid.UUID(i) for i in ids]),
                )
            )
        )
    if not rows:
        return []
    sandbox = await runtime.environments.sandbox(str(thread_id), user_sub)
    for row in rows:
        await sandbox.files.write_file(row.path, row.content)
    async with runtime.engine.begin() as conn:
        await conn.execute(
            update(ChatFile)
            .where(ChatFile.id.in_([r.id for r in rows]))
            .values(run_id=run_id)
        )
    return [r.name for r in rows]


def attached_note(names: list[str]) -> str:
    """What the message says about its attachments, for the model (and the history)."""
    return f"\n\nAttached, in {IN_DIR}: {', '.join(names)}" if names else ""


def shareable(entries: list[Any]) -> list[Any]:
    """The entries of OUT_DIR a capture takes: regular files only (a link could point anywhere in
    the environment), at most MAX_FILES."""
    return [e for e in entries if e.entry_type == "file"][:MAX_FILES]


# Unicode's bidirectional controls: in a name they change the order it reads in, so
# "report<U+202E>gpj.exe" shows as "reportexe.jpg" (CWE-451). Right-to-left names (Arabic,
# Hebrew) need none of them
BIDI_CONTROLS = re.compile("[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def name_of(path: str) -> str:
    """A file's name as the person sees it: its path under OUT_DIR, any bidirectional control
    shown as "_" (the environment named it, maybe at a web page's suggestion)."""
    return BIDI_CONTROLS.sub("_", path.removeprefix(OUT_DIR).lstrip("/"))


def shown(file: ChatFile | Any) -> dict[str, Any]:
    """A file as clients see it: never its content."""
    return {
        "id": str(file.id),
        "name": file.name,
        "size": file.size,
        "media_type": file.media_type,
    }


async def capture(
    runtime: Runtime,
    thread_id: uuid.UUID,
    run_id: uuid.UUID,
    user_sub: str,
    since: float,
) -> list[dict[str, Any]]:
    """What the chat's environment has new or changed in OUT_DIR, kept as the chat's files: the
    ones kept. Nothing when environments are off, or the turn (from `since`, monotonic) didn't
    use the environment: a chat that never ran code never gets one for this."""
    environments = runtime.environments
    if environments is None or not environments.used_since(str(thread_id), since):
        return []
    sandbox = await environments.sandbox(str(thread_id), user_sub)
    try:
        entries = await sandbox.files.list_directory(
            DirectoryListEntry(path=OUT_DIR, depth=DEPTH)
        )
    except SandboxApiException as e:
        if "FILE_NOT_FOUND" not in str(e) and "not found" not in str(e).lower():
            log.warning("chat %s: its files not captured: %s", thread_id, e)
        return []
    found = shareable(entries)
    async with runtime.engine.connect() as conn:
        known = {
            r.path: r
            for r in await conn.execute(
                select(
                    ChatFile.path, ChatFile.size, ChatFile.modified_at, ChatFile.sha256
                ).where(ChatFile.thread_id == thread_id, ChatFile.origin == "output")
            )
        }
        total = (
            await conn.scalar(
                select(func.coalesce(func.sum(ChatFile.size), 0)).where(
                    ChatFile.thread_id == thread_id
                )
            )
            or 0
        )
    kept: list[dict[str, Any]] = []
    for entry in found:
        before = known.get(entry.path)
        if (
            before
            and before.size == entry.size
            and before.modified_at == entry.modified_at
        ):
            continue
        if entry.size > MAX_FILE:
            log.info(
                "chat %s: %s left out, over %d bytes", thread_id, entry.path, MAX_FILE
            )
            continue
        content = await sandbox.files.read_bytes(entry.path)
        digest = hashlib.sha256(content).hexdigest()
        name = name_of(entry.path)
        grown = total - (before.size if before else 0) + len(content)
        if before and before.sha256 == digest:
            # Touched, not changed: the answer that shared it still offers it
            async with runtime.engine.begin() as conn:
                await conn.execute(
                    update(ChatFile)
                    .where(ChatFile.thread_id == thread_id, ChatFile.path == entry.path)
                    .values(modified_at=entry.modified_at)
                )
            continue
        if grown > MAX_CHAT:
            log.info(
                "chat %s: %s left out, the chat's files would pass %d bytes",
                thread_id,
                name,
                MAX_CHAT,
            )
            continue
        values = {
            "thread_id": thread_id,
            "run_id": run_id,
            "origin": "output",
            "path": entry.path,
            "name": name,
            "media_type": media_type(name),
            "size": len(content),
            "sha256": digest,
            "modified_at": entry.modified_at,
            "content": content,
        }
        async with runtime.engine.begin() as conn:
            row = (
                await conn.execute(
                    insert(ChatFile)
                    .values(**values)
                    .on_conflict_do_update(
                        constraint="uq_chat_files_thread_path",
                        set_={
                            **{
                                k: v
                                for k, v in values.items()
                                if k not in ("thread_id", "origin", "path")
                            },
                            "updated_at": func.now(),
                        },
                    )
                    .returning(
                        ChatFile.id, ChatFile.name, ChatFile.size, ChatFile.media_type
                    )
                )
            ).one()
        total = grown
        kept.append(shown(row))
    return kept
