"""A chat's files (chat_files.py): what its environment shared, and what the person attached, kept
by Gen9. Only the chat's owner lists, adds, downloads or removes them; others get 404.

- A file is always served as a download, never shown inline: code in the environment made it, so
  an HTML or SVG file must never run as the web app.
- An attachment is the request's body, named by `?name=` (25 MB a file, 250 MB a chat). The next
  run that names it (`files` in its body) puts it in the environment's /work/in before the
  turn; the API itself never reaches the environment.
"""

import hashlib
import re
from typing import Annotated, Any
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select

from .. import audit
from ..chat_files import BIDI_CONTROLS, IN_DIR, MAX_CHAT, MAX_FILE, media_type
from ..deps import Session
from ..models import ChatFile, Thread
from ..users import CurrentUser
from .threads import ChatFileOut, OwnedThread

router = APIRouter(prefix="/v1/threads/{thread_id}/files", tags=["files"])


@router.get("", summary="The chat's files, without their content")
async def list_files(thread: OwnedThread, session: Session) -> list[ChatFileOut]:
    rows = await session.execute(
        select(
            ChatFile.id,
            ChatFile.name,
            ChatFile.size,
            ChatFile.media_type,
            ChatFile.origin,
        )
        .where(ChatFile.thread_id == thread.id)
        .order_by(ChatFile.updated_at)
    )
    return [
        ChatFileOut(
            id=str(r.id),
            name=r.name,
            size=r.size,
            media_type=r.media_type,
            origin=r.origin,
        )
        for r in rows
    ]


def _size(n: int) -> str:
    """A limit as a person reads it: whole GB, else MB."""
    return f"{n // 1024**3} GB" if n >= 1024**3 else f"{max(n // 1024**2, 1)} MB"


async def _theirs(request: Request, session: Session, file_id: UUID, user: Any) -> None:
    """Another person's file tried under one's own chat: recorded as refused (audit.py)."""
    owner = (
        select(Thread.user_id)
        .join(ChatFile, ChatFile.thread_id == Thread.id)
        .where(ChatFile.id == file_id)
    )
    await audit.theirs_through(request, session, owner, file_id, user, "file")


# A name that is a file's name: no folders, no leading dot, at most 128 characters
NAME = re.compile(r"^[^/\\\x00-\x1f.][^/\\\x00-\x1f]{0,127}$")


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Attach a file: the request's body, named by ?name=",
    responses={
        413: {"description": "Over 25 MB, or the chat's files over 250 MB"},
        503: {"description": "This Gen9 has no environments for files"},
    },
)
async def attach_file(
    thread: OwnedThread,
    name: Annotated[str, Query(min_length=1, max_length=128)],
    session: Session,
    request: Request,
) -> ChatFileOut:
    if not request.app.state.runtime.settings.sandbox_url:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Files need an environment, and this Gen9 has none.",
        )
    if not NAME.match(name):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Name the file without folders."
        )
    if BIDI_CONTROLS.search(name):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "The name has characters that change the order it reads in. Rename the file.",
        )
    content = bytearray()
    async for chunk in request.stream():
        content += chunk
        if len(content) > MAX_FILE:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE, "Files can be up to 25 MB."
            )
    # The chat's row locked until the commit: uploads to one chat take turns here, so parallel
    # ones can't each see room for themselves and go over MAX_CHAT together
    await session.execute(
        select(Thread.id).where(Thread.id == thread.id).with_for_update()
    )
    total = await session.scalar(
        select(func.coalesce(func.sum(ChatFile.size), 0)).where(
            ChatFile.thread_id == thread.id
        )
    )
    if (total or 0) + len(content) > MAX_CHAT:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, "This chat's files are up to 250 MB."
        )
    # And all of a person's chats together (settings.files_max_bytes_per_person), which Postgres
    # keeps: checked under the same lock, so one chat's uploads can't race past it
    cap = request.app.state.runtime.settings.files_max_bytes_per_person
    everyone = await session.scalar(
        select(func.coalesce(func.sum(ChatFile.size), 0))
        .join(Thread, Thread.id == ChatFile.thread_id)
        .where(Thread.user_id == thread.user_id)
    )
    if (everyone or 0) + len(content) > cap:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"Your chats' files are up to {_size(cap)} together."
            " Delete a chat with files to make room.",
        )
    path = f"{IN_DIR}/{name}"
    existing = await session.scalar(
        select(ChatFile).where(
            ChatFile.thread_id == thread.id,
            ChatFile.origin == "upload",
            ChatFile.path == path,
        )
    )
    row = existing or ChatFile(
        thread_id=thread.id, origin="upload", path=path, name=name
    )
    row.media_type = media_type(name)
    row.size = len(content)
    row.sha256 = hashlib.sha256(content).hexdigest()
    row.content = bytes(content)
    row.run_id = (
        None  # not in the environment yet: the next run that names it puts it there
    )
    row.updated_at = func.now()
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return ChatFileOut(
        id=str(row.id),
        name=row.name,
        size=row.size,
        media_type=row.media_type,
        origin="upload",  # this endpoint's
    )


@router.delete(
    "/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove one of the chat's files",
)
async def remove_file(
    thread: OwnedThread,
    file_id: UUID,
    session: Session,
    user: CurrentUser,
    request: Request,
) -> Response:
    row = await session.scalar(
        select(ChatFile).where(ChatFile.id == file_id, ChatFile.thread_id == thread.id)
    )
    if row is None:
        await _theirs(request, session, file_id, user)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such file")
    await session.delete(row)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{file_id}",
    summary="Download one of the chat's files",
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def download_file(
    thread: OwnedThread,
    file_id: UUID,
    session: Session,
    user: CurrentUser,
    request: Request,
) -> Response:
    row = await session.scalar(
        select(ChatFile).where(ChatFile.id == file_id, ChatFile.thread_id == thread.id)
    )
    if row is None:
        await _theirs(request, session, file_id, user)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such file")
    filename = row.name.rsplit("/", 1)[-1]
    return Response(
        content=row.content,
        media_type=row.media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )
