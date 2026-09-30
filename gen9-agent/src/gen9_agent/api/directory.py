"""The connector directory (directory.py): search Gen9's copy of the MCP registry. Its entries
are the registry's, not reviewed by Gen9; adding one goes through `POST /v1/me/connectors` like
any other connector."""

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import and_, func, select

from ..deps import Session
from ..models import RegistryServer
from ..users import CurrentUser

router = APIRouter(prefix="/v1/directory", tags=["directory"])


class DirectoryEntry(BaseModel):
    name: str
    title: str | None
    description: str
    url: str
    header: str | None
    header_description: str | None
    repository_url: str | None
    website_url: str | None
    status: str


# The registry's namespace for a publisher who signed in with GitHub to publish
# (modelcontextprotocol.io/registry, "Authentication"): it says nothing of what the server does,
# and half the names start with it, so "github" matched them all and put GitHub's own server
# 123rd (gen9-learn.md, M9, F11). Matching and ranking leave it out
SIGNED_IN_WITH_GITHUB = r"^io\.github\."


def _text():
    """Name, title and description, as the trigram index holds them."""
    s = RegistryServer
    return func.lower(s.name + " " + func.coalesce(s.title, "") + " " + s.description)


def _name():
    return func.regexp_replace(RegistryServer.name, SIGNED_IN_WITH_GITHUB, "")


def _words():
    """Name, title and description, the name without `io.github.`."""
    s = RegistryServer
    return func.lower(_name() + " " + func.coalesce(s.title, "") + " " + s.description)


def _publisher():
    """The publisher's namespace as words between spaces: " com cloudflare mcp ", " github "."""
    namespace = func.regexp_replace(
        func.split_part(RegistryServer.name, "/", 1), SIGNED_IN_WITH_GITHUB, ""
    )
    return " " + func.lower(func.replace(namespace, ".", " ")) + " "


def _escaped(word: str) -> str:
    """`word` for a LIKE pattern, its own %, _ and \\ taken literally."""
    return word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _contains(word: str) -> str:
    """A LIKE pattern for `word` anywhere."""
    return f"%{_escaped(word)}%"


@router.get(
    "",
    summary="Servers from the MCP registry a connector can use, matching the words, if any",
)
async def search_directory(
    _: CurrentUser,
    session: Session,
    q: Annotated[str, Query(max_length=200)] = "",
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[DirectoryEntry]:
    """Every word must appear in the name (without `io.github.`), title or description; pg_trgm's
    index serves the match on the whole text, which the rest narrows. Active servers come first;
    then a publisher's own, whose namespace holds every word (`com.cloudflare.mcp/mcp` for
    "cloudflare", `io.github.github/…` for "github"); then a title that is the words; then those
    whose name and title look most like them (pg_trgm's similarity); then by name."""
    words = [w for w in q.lower().split() if w][:8]
    query = select(RegistryServer)
    order = [RegistryServer.status != "active"]
    if words:
        query = query.where(
            *[_text().like(_contains(w), escape="\\") for w in words],
            *[_words().like(_contains(w), escape="\\") for w in words],
        )
        own = and_(
            *[_publisher().like(f"% {_escaped(w)} %", escape="\\") for w in words]
        )
        titled = func.lower(func.coalesce(RegistryServer.title, "")) == " ".join(words)
        named = func.lower(_name() + " " + func.coalesce(RegistryServer.title, ""))
        order += [
            own.desc(),
            titled.desc(),
            func.similarity(named, " ".join(words)).desc(),
        ]
    query = query.order_by(*order, RegistryServer.name).limit(limit)
    rows = await session.scalars(query)
    return [
        DirectoryEntry(
            name=r.name,
            title=r.title,
            description=r.description,
            url=r.url,
            header=r.header,
            header_description=r.header_description,
            repository_url=r.repository_url,
            website_url=r.website_url,
            status=r.status,
        )
        for r in rows
    ]
