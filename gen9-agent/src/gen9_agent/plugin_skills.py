"""A person's plugins in their chats (docs/plans/harness.md, milestone 4): the skills of the plugins
they have, read-only at ROUTE, as Deep Agents' "Skills" page does per-user skills (one fixed source
whose backend picks its files by the run).

- **Whose.** A person has a plugin that loaded when an admin installed it for everyone, or made it
  available and they added it (`plugin_installs`).
- **Per run.** Each turn, the executor loads an index of that person's skills' files (`load`:
  where each is kept and its size, not its content) into the run's context
  (`Gen9Context.plugin_files`), and the backend serves them: one folder per skill, named as the
  skill (`/plugins/<skill>/SKILL.md` and what it refers to). A file's content is read from the
  database when the agent reads it (or the skills middleware reads a SKILL.md's frontmatter), as
  Deep Agents' progressive disclosure intends; a search reads at most GREP_BYTES. Another
  person's run sees their own, or none. The turn also resets the thread's `skills_metadata`, so
  adding or removing a plugin applies from the next message, even in an open chat.
- **Names.** A built-in skill wins over a plugin's skill of the same name, and between plugins the
  first by name wins (Deep Agents keeps one skill per name).
- **Read-only.** A permission denies writes, and the backend refuses them anyway.
"""

import base64
import logging
import uuid
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from deepagents import FilesystemPermission
from deepagents.backends import StateBackend
from deepagents.backends.protocol import (
    BackendProtocol,
    DeleteResult,
    EditResult,
    FileDownloadResponse,
    FileInfo,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.backends.utils import create_file_data
from langgraph.runtime import get_runtime
from sqlalchemy import Text, and_, cast, exists, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncEngine

from .models import Plugin, PluginFile, PluginInstall, User

log = logging.getLogger(__name__)

ROUTE = "/plugins/"
PERMISSIONS = [
    FilesystemPermission(operations=["write"], paths=[f"{ROUTE}**"], mode="deny")
]
# What one search reads at most: a large skill's worth (plugin_sources.MAX_SKILL_BYTES). A
# search over more says it stopped early (truncated), as a backend's time limit would
GREP_BYTES = 25 * 1024 * 1024
_READ_ONLY = "Plugins' skills are read-only"


@dataclass(frozen=True)
class SkillFile:
    """Where one of a person's skill files is kept, and its size: what a run holds of it until
    the agent reads it."""

    plugin_id: uuid.UUID
    path: str  # in plugin_files: from the plugin's root
    size: int


# Reads the content of kept files: those it finds
Contents = Callable[[Sequence[SkillFile]], Awaitable[dict[SkillFile, bytes]]]


@dataclass(frozen=True)
class PersonSkills:
    """What a person's chats get from their plugins: the files at ROUTE, and which plugin each
    skill came from (its title, or name)."""

    files: dict[str, SkillFile]  # by path under ROUTE: /<skill>/SKILL.md
    plugin_of: dict[str, str]


def fingerprint():
    """What a plugin is, as one hash: its kept files (`digest`) and what it brings (its report:
    manifest, skills, MCP servers). An admin choosing who may have it agrees to this one
    (`Plugin.reviewed`). A sync that changes the plugin gives another, and nobody gets it until
    an admin looks again (docs/plans/manual-e2e.md, P5-C4)."""
    return func.encode(
        func.sha256(
            func.convert_to(
                func.concat(
                    func.coalesce(Plugin.digest, ""), cast(Plugin.report, Text)
                ),
                "UTF8",
            )
        ),
        "hex",
    )


def has_plugin(user_sub: str, reviewed: bool = True):
    """The condition a plugin meets when the person has it. `reviewed`: only as an admin last
    agreed to it; False for keeping its connectors while a change waits for them, so the person
    needn't sign in to them again."""
    added = exists().where(
        PluginInstall.plugin_id == Plugin.id,
        PluginInstall.user_id == User.id,
        User.sub == user_sub,
    )
    return and_(
        Plugin.status == "loaded",
        or_(
            Plugin.availability == "installed",
            and_(Plugin.availability == "available", added),
        ),
        *([Plugin.reviewed == fingerprint()] if reviewed else []),
    )


# A file listed but not read: StateBackend lists and filters it as an empty one
_EMPTY: dict[str, Any] = dict(create_file_data(""))


def _file_data(content: bytes) -> dict[str, Any]:
    try:
        return dict(create_file_data(content.decode("utf-8")))
    except UnicodeDecodeError:
        return dict(
            create_file_data(
                base64.standard_b64encode(content).decode(), encoding="base64"
            )
        )


def skills_chosen(
    plugins: Iterable[Any], builtin: frozenset[str]
) -> list[tuple[Any, dict[str, Any]]]:
    """Each (plugin, skill) the person's chats get: built-ins keep their names, and the first
    plugin by name keeps a shared one."""
    taken = set(builtin)
    chosen = []
    for plugin in sorted(plugins, key=lambda p: (p.name, str(p.id))):
        for skill in (plugin.report or {}).get("skills", []):
            if skill["name"] in taken:
                continue
            taken.add(skill["name"])
            chosen.append((plugin, skill))
    return chosen


async def load(
    engine: AsyncEngine, user_sub: str, builtin: frozenset[str]
) -> PersonSkills:
    """The person's plugins' skills: where each file is kept and its size, by the path the
    backend serves it at. Their content stays in the database until read."""
    async with engine.connect() as conn:
        plugins = list(
            await conn.execute(
                select(Plugin.id, Plugin.name, Plugin.title, Plugin.report).where(
                    has_plugin(user_sub)
                )
            )
        )
        chosen = skills_chosen(plugins, builtin)
        if not chosen:
            return PersonSkills({}, {})
        rows = await conn.execute(
            select(
                PluginFile.plugin_id,
                PluginFile.path,
                func.octet_length(PluginFile.content).label("size"),
            ).where(PluginFile.plugin_id.in_({p.id for p, _ in chosen}))
        )
        by_plugin: dict[Any, list[Any]] = {}
        for row in rows:
            by_plugin.setdefault(row.plugin_id, []).append(row)
    files: dict[str, SkillFile] = {}
    plugin_of: dict[str, str] = {}
    for plugin, skill in chosen:
        base = skill["path"].rstrip("/")
        prefix = f"{base}/" if base else ""
        for row in by_plugin.get(plugin.id, []):
            if not row.path.startswith(prefix):
                continue
            # Keyed as the route's backend sees them: CompositeBackend strips the route
            path = f"/{skill['name']}/{row.path.removeprefix(prefix)}"
            files[path] = SkillFile(plugin.id, row.path, row.size)
        plugin_of[skill["name"]] = plugin.title or plugin.name
    return PersonSkills(files, plugin_of)


def contents(engine: AsyncEngine) -> Contents:
    """Reads kept files from plugin_files, in one query."""

    async def read(files: Sequence[SkillFile]) -> dict[SkillFile, bytes]:
        if not files:
            return {}
        async with engine.connect() as conn:
            rows = await conn.execute(
                select(PluginFile.plugin_id, PluginFile.path, PluginFile.content).where(
                    tuple_(PluginFile.plugin_id, PluginFile.path).in_(
                        [(f.plugin_id, f.path) for f in files]
                    )
                )
            )
            found = {(r.plugin_id, r.path): r.content for r in rows}
        return {
            f: found[(f.plugin_id, f.path)]
            for f in files
            if (f.plugin_id, f.path) in found
        }

    return read


class _Files(StateBackend):
    """StateBackend's listing, search and reading over files held for one call: placeholders to
    list and filter, or the contents just read."""

    def __init__(self, files: dict[str, Any]) -> None:
        super().__init__()
        self._files = files

    def _read_files(self) -> dict[str, Any]:
        return self._files


class PluginSkillsBackend(BackendProtocol):
    """The run's person's plugins' skills (`Gen9Context.plugin_files`, an index), read-only, their
    content read when asked. Async only, as Gen9 runs its agent."""

    def __init__(self, contents: Contents) -> None:
        self._contents = contents

    def _index(self) -> dict[str, SkillFile]:
        try:
            context = get_runtime().context
        except RuntimeError:
            return {}
        return dict(getattr(context, "plugin_files", None) or {})

    @staticmethod
    def _placeholders(index: dict[str, SkillFile]) -> _Files:
        return _Files({path: _EMPTY for path in index})

    @staticmethod
    def _sized(info: FileInfo, index: dict[str, SkillFile]) -> FileInfo:
        file = index.get(info["path"])
        if info.get("is_dir") or file is None:
            return info
        return FileInfo(path=info["path"], is_dir=False, size=file.size, modified_at="")

    async def _read(
        self, index: dict[str, SkillFile], paths: Iterable[str]
    ) -> dict[str, bytes]:
        wanted = {path: index[path] for path in paths if path in index}
        found = await self._contents(list(wanted.values()))
        return {path: found[f] for path, f in wanted.items() if f in found}

    async def als(self, path: str) -> LsResult:
        index = self._index()
        listed = self._placeholders(index).ls(path)
        return LsResult(
            entries=[self._sized(e, index) for e in listed.entries or []],
            error=listed.error,
        )

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        index = self._index()
        found = self._placeholders(index).glob(pattern, path)
        return GlobResult(
            matches=[self._sized(m, index) for m in found.matches or []],
            error=found.error,
        )

    async def aread(
        self, file_path: str, offset: int = 0, limit: int = 2000
    ) -> ReadResult:
        content = (await self._read(self._index(), [file_path])).get(file_path)
        if content is None:
            return ReadResult(error=f"File '{file_path}' not found")
        return _Files({file_path: _file_data(content)}).read(file_path, offset, limit)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        index = self._index()
        # Which files the path and glob take, as StateBackend decides: every line of an empty
        # placeholder matches "", so each such file comes back once
        taken = self._placeholders(index).grep("", path, glob)
        if taken.error:
            return taken
        paths, total = [], 0
        for match in taken.matches or []:
            total += index[match["path"]].size
            if total > GREP_BYTES:
                break
            paths.append(match["path"])
        stopped = len(paths) < len(taken.matches or [])
        read = await self._read(index, paths)
        found = _Files({p: _file_data(c) for p, c in read.items()}).grep(
            pattern, path, glob, max_count=max_count
        )
        return GrepResult(
            matches=found.matches,
            error=found.error,
            truncated=found.truncated or stopped,
        )

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        read = await self._read(self._index(), paths)
        return [
            FileDownloadResponse(path=p, content=read[p], error=None)
            if p in read
            else FileDownloadResponse(path=p, content=None, error="file_not_found")
            for p in paths
        ]

    def write(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error=_READ_ONLY)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return EditResult(error=_READ_ONLY)

    def delete(self, file_path: str) -> DeleteResult:
        return DeleteResult(error=_READ_ONLY)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=p, error="permission_denied") for p, _ in files]
