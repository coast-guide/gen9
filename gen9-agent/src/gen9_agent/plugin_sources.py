"""Plugin sources: git repositories holding a plugin marketplace, added by admins and synced by the
worker (docs/plans/harness.md, milestone 4; Decision Log).

- **Marketplaces** in Codex's format (`.agents/plugins/marketplace.json`) or Claude Code's
  (`.claude-plugin/marketplace.json`): a list of plugins, each with a `name` and a `source`.
- **Where a plugin is**: a folder of the marketplace's repository (`./path`, Codex's `local`,
  a bare name under Claude's `metadata.pluginRoot`), or another git repository (`url`,
  `github`, `git-subdir`, pinned by `ref` or `sha`). `npm`, `archive` and `command` sources are
  listed as unsupported: Gen9 fetches only git, and runs nothing a plugin names.
- **Fetching** runs `git` hardened, from git's own documentation: no system or global config,
  no prompts, https only (`GIT_ALLOW_PROTOCOL`), no redirects (`http.followRedirects`), the host
  checked public as connectors' are and then pinned (`http.curloptResolve`, so git can't resolve
  it elsewhere), shallow, no tags or submodules, objects checked (`transfer.fsckObjects`), a time
  limit, and the checkout's size bounded. Its environment holds nothing of the worker's.
- **Kept**: each plugin's load report (plugins.py) and the files of the skills it brings, bounded,
  in Postgres. A plugin's commit and its files' digest let a sync skip what hasn't changed
  (`git ls-remote` for one kept elsewhere). A failed sync keeps what was there and says why.
- A plugin the marketplace no longer lists is removed. New ones start "off": an admin makes them
  available (api/plugins.py).
"""

import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from . import plugin_connectors, plugins
from .connector_net import is_public
from .models import Plugin, PluginFile, PluginSource
from .runtime import Runtime
from .settings import Settings

log = logging.getLogger(__name__)

MARKETPLACES = (
    (".agents/plugins/marketplace.json", "codex"),
    (".claude-plugin/marketplace.json", "claude"),
)
GIT_TIMEOUT_S = 120
MAX_CHECKOUT = 200 * 1024 * 1024  # a repository's checkout, .git aside
MAX_PLUGINS = 500  # a marketplace's entries read
# A skill's files, as the leading products bound a skill: OpenAI's skills take 500 files and
# 25 MB uncompressed, Anthropic's 30 MB. Chats read them only when used (plugin_skills.py)
MAX_SKILL_FILES = 500
MAX_SKILL_BYTES = 25 * 1024 * 1024
# A plugin's skills' files together, kept in the database
MAX_PLUGIN_FILES = 2000
MAX_PLUGIN_BYTES = 100 * 1024 * 1024
FETCHING = 4  # plugins kept elsewhere, fetched at once
_SHA = re.compile(r"^[0-9a-f]{40}$")
_REF = re.compile(r"^(?!-)[A-Za-z0-9._/-]{1,200}$")
_GITHUB = re.compile(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$")


class SourceError(Exception):
    """What went wrong with a source or a plugin's fetch, in words for the admin."""


@dataclass(frozen=True)
class Remote:
    """A repository git may fetch: its URL, the address its host is pinned to, and whether it's a
    host the operator named (http allowed)."""

    url: str
    pinned: str | None
    named: bool


def _named(settings: Settings, host: str, port: int | None) -> bool:
    hosts = settings.plugin_sources_allowed_hosts
    return host in hosts or f"{host}:{port}" in hosts


async def remote(url: str, settings: Settings) -> Remote:
    """`url` if git may fetch it: https, no credentials, query or fragment, and a host that
    resolves only to public addresses, unless the operator names it
    (PLUGIN_SOURCES_ALLOWED_HOSTS: private, and over http too). Raises SourceError."""
    url = url.strip()
    parts = urlsplit(url)
    host = parts.hostname or ""
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as e:
        raise SourceError("That isn't a repository address.") from e
    named = bool(host) and _named(settings, host, parts.port)
    if parts.scheme != "https" and not (named and parts.scheme == "http"):
        raise SourceError("Use an https:// address.")
    if not host or parts.username or parts.password or parts.query or parts.fragment:
        raise SourceError("That isn't a repository address.")
    try:
        found = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )
    except socket.gaierror as e:
        raise SourceError(f"Gen9 couldn't find {host}.") from e
    addresses = [ipaddress.ip_address(sockaddr[0]) for *_, sockaddr in found]
    if not named and not all(is_public(a) for a in addresses):
        raise SourceError(
            f"{host} is on a private network, which Gen9 doesn't fetch from."
        )
    first = addresses[0]
    pinned = f"{host}:{port}:{f'[{first}]' if first.version == 6 else first}"
    return Remote(url, pinned, named)


def _environment(home: str, remote: Remote | None) -> dict[str, str]:
    """git's environment: nothing of the worker's (it holds secrets), no config but the command
    line's, no prompts, and only https (http for a named host)."""
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": home,
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PROTOCOL_FROM_USER": "0",
        "GIT_ALLOW_PROTOCOL": "https:http" if remote and remote.named else "https",
        "GIT_LFS_SKIP_SMUDGE": "1",
    }


async def git(args: list[str], home: str, remote: Remote | None = None) -> str:
    """Runs git with `args`; its output. Raises SourceError with git's last words."""
    command = [
        "git",
        "-c",
        "http.followRedirects=false",
        "-c",
        "transfer.fsckObjects=true",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "submodule.recurse=false",
    ]
    if remote and remote.pinned:
        command += ["-c", f"http.curloptResolve={remote.pinned}"]
    process = await asyncio.create_subprocess_exec(
        *command,
        *args,
        env=_environment(home, remote),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(process.communicate(), GIT_TIMEOUT_S)
    except TimeoutError as e:
        process.kill()
        await process.wait()
        raise SourceError(f"git took longer than {GIT_TIMEOUT_S} s.") from e
    if process.returncode:
        lines = [x for x in err.decode(errors="replace").splitlines() if x.strip()]
        said = lines[-1].removeprefix("fatal: ").strip() if lines else "no reason"
        raise SourceError(f"git failed: {said[:300]}")
    return out.decode(errors="replace")


async def head_of(remote: Remote, ref: str | None, home: str) -> str:
    """The commit `ref` (the default branch if none) names in the repository."""
    out = await git(["ls-remote", "--", remote.url, ref or "HEAD"], home, remote)
    refs = {}
    for line in out.splitlines():
        sha, _, name = line.partition("\t")
        if _SHA.match(sha):
            refs[name] = sha
    # An annotated tag's own line names the tag; `^{}` names its commit, which a checkout gets
    for name in (
        "HEAD" if not ref else f"refs/heads/{ref}",
        f"refs/tags/{ref}^{{}}",
        f"refs/tags/{ref}",
    ):
        if name in refs:
            return refs[name]
    raise SourceError(f"{ref or 'Its default branch'} isn't in that repository.")


async def checkout(
    remote: Remote,
    dest: Path,
    home: str,
    ref: str | None = None,
    sha: str | None = None,
    subdir: str | None = None,
) -> str:
    """One commit of the repository in `dest`, shallow (only `subdir` of it, when given, through
    a sparse partial clone); its commit."""
    await git(["init", "-q", str(dest)], home)
    await git(["-C", str(dest), "remote", "add", "origin", remote.url], home)
    fetch = ["-C", str(dest), "fetch", "-q", "--depth", "1", "--no-tags"]
    fetch += ["--no-recurse-submodules"]
    if subdir:
        fetch.append("--filter=blob:none")
        await git(
            ["-C", str(dest), "sparse-checkout", "set", "--no-cone", f"/{subdir}/"],
            home,
        )
    await git([*fetch, "origin", sha or ref or "HEAD"], home, remote)
    await git(
        ["-C", str(dest), "checkout", "-q", "--detach", "FETCH_HEAD"], home, remote
    )
    commit = (await git(["-C", str(dest), "rev-parse", "HEAD"], home)).strip()
    size = await asyncio.to_thread(_size, dest)
    if size > MAX_CHECKOUT:
        raise SourceError(f"It is over {MAX_CHECKOUT // 2**20} MB.")
    return commit


def _size(folder: Path) -> int:
    total = 0
    for top, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d != ".git"]
        total += sum(os.lstat(os.path.join(top, f)).st_size for f in files)
    return total


def _within(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def read_marketplace(root: Path) -> tuple[str, dict[str, Any]]:
    """The marketplace file in a repository's checkout: its format and contents."""
    for place, fmt in MARKETPLACES:
        path = root / place
        if not os.path.lexists(path):
            continue
        if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
            raise SourceError(f"{place} is not a file in the repository.")
        try:
            index = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            raise SourceError(f"{place} is not valid JSON: {e}") from e
        if not isinstance(index, dict) or not isinstance(index.get("plugins"), list):
            raise SourceError(f"{place} has no list of plugins.")
        return fmt, index
    raise SourceError(
        "It has no .agents/plugins/marketplace.json or .claude-plugin/marketplace.json."
    )


@dataclass(frozen=True)
class Where:
    """Where an entry's plugin is: `here` (a folder of the marketplace's repository), `git`
    (another repository) or `unsupported`."""

    kind: str
    path: str | None = None  # here: its folder; git: the folder in that repository
    url: str | None = None
    ref: str | None = None
    sha: str | None = None
    reason: str | None = None


def _relative(path: str) -> str | None:
    """`./a/b` as `a/b` ("" for the root itself), or None if it isn't a ./ path inside."""
    if path != "." and not path.startswith("./"):
        return None
    clean = os.path.normpath(path)
    if clean == ".":
        return ""
    if clean.startswith("..") or os.path.isabs(clean) or "\\" in clean:
        return None
    return clean


def where(entry: dict[str, Any], index: dict[str, Any]) -> Where:
    source = entry.get("source")
    if isinstance(source, str):
        root = (index.get("metadata") or {}).get("pluginRoot")
        if isinstance(root, str) and source and "/" not in source and source != ".":
            source = f"{root.rstrip('/')}/{source}"
        path = _relative(source)
        if path is None:
            return Where(
                "unsupported", reason=f"its source {source!r} is not a ./ path"
            )
        return Where("here", path=path)
    if not isinstance(source, dict):
        return Where("unsupported", reason="it has no source")
    kind = source.get("source")
    if kind == "local":
        path = _relative(str(source.get("path", "")))
        if path is None:
            return Where("unsupported", reason="its path is not a ./ path")
        return Where("here", path=path)
    if kind in ("npm", "archive"):
        return Where("unsupported", reason=f"Gen9 fetches plugins from git, not {kind}")
    if kind == "command":
        return Where("unsupported", reason="Gen9 runs no command a plugin names")
    ref, sha = source.get("ref"), source.get("sha")
    if ref is not None and (not isinstance(ref, str) or not _REF.match(ref)):
        return Where("unsupported", reason=f"its ref {ref!r} is not a branch or tag")
    if sha is not None and (not isinstance(sha, str) or not _SHA.match(sha)):
        return Where("unsupported", reason="its sha is not a full commit id")
    if kind == "github":
        repo = source.get("repo")
        if not isinstance(repo, str) or not _GITHUB.match(repo):
            return Where("unsupported", reason="its repo is not owner/name")
        return Where("git", url=f"https://github.com/{repo}.git", ref=ref, sha=sha)
    if kind in ("url", "git-subdir"):
        url = source.get("url")
        if isinstance(url, str) and _GITHUB.match(url):
            url = f"https://github.com/{url}.git"
        if not isinstance(url, str):
            return Where("unsupported", reason="it has no url")
        path = None
        if kind == "git-subdir":
            raw = str(source.get("path", ""))
            path = _relative(raw if raw.startswith(".") else f"./{raw}")
            if not path:
                return Where("unsupported", reason="its path is not a folder inside")
        return Where("git", path=path, url=url, ref=ref, sha=sha)
    return Where("unsupported", reason=f"Gen9 doesn't read {kind!r} sources")


def skill_files(root: Path, loaded: plugins.Loaded) -> dict[str, bytes]:
    """The files of the skills `loaded` brings, by path from the plugin's root: regular files (a
    link only when it stays in the plugin), bounded per skill and per plugin."""
    base = root.resolve()
    files: dict[str, bytes] = {}
    total = 0
    for skill in loaded.skills:
        folder = base / skill.path if skill.path else base
        count = size = 0
        for top, dirs, names in os.walk(folder):
            dirs[:] = sorted(d for d in dirs if d != ".git")
            for name in sorted(names):
                path = Path(top) / name
                resolved = path.resolve()
                if not resolved.is_relative_to(base) or not resolved.is_file():
                    continue
                relative = path.relative_to(base).as_posix()
                content = files.get(relative) or resolved.read_bytes()
                count, size = count + 1, size + len(content)
                if count > MAX_SKILL_FILES or size > MAX_SKILL_BYTES:
                    raise SourceError(
                        f"Its skill {skill.name} is over {MAX_SKILL_FILES} files or"
                        f" {MAX_SKILL_BYTES // 2**20} MB."
                    )
                if relative in files:
                    continue  # another skill's too: kept once
                total += len(content)
                if len(files) >= MAX_PLUGIN_FILES or total > MAX_PLUGIN_BYTES:
                    raise SourceError(
                        f"Its skills are over {MAX_PLUGIN_FILES} files or"
                        f" {MAX_PLUGIN_BYTES // 2**20} MB."
                    )
                files[relative] = content
    return files


def digest(files: dict[str, bytes]) -> str:
    h = hashlib.sha256()
    for path in sorted(files):
        h.update(path.encode() + b"\0" + hashlib.sha256(files[path]).digest())
    return h.hexdigest()


@dataclass
class Found:
    """A plugin as a sync found it: the row's values, and its files when they changed."""

    name: str
    values: dict[str, Any]
    files: dict[str, bytes] | None = None  # None: keep what's kept
    unchanged: bool = False
    problem: str | None = None  # kept as it was, because this went wrong


@dataclass
class SyncSummary:
    plugins: int = 0
    loaded: int = 0
    changed: int = 0
    removed: int = 0
    problems: list[str] = field(default_factory=list)


class Syncer:
    """One sync of one source."""

    def __init__(self, runtime: Runtime, home: str) -> None:
        self.runtime = runtime
        self.settings = runtime.settings
        self.home = home
        self.fetching = asyncio.Semaphore(FETCHING)

    async def plugin(
        self,
        i: int,
        entry: dict[str, Any],
        index: dict[str, Any],
        here: Path,
        commit: str,
        known: dict[str, Any],
    ) -> Found | None:
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 128:
            return None
        base: dict[str, Any] = {
            "source": entry.get("source") if entry.get("source") is not None else {},
            "description": entry.get("description")
            if isinstance(entry.get("description"), str)
            else None,
            "title": entry.get("displayName")
            if isinstance(entry.get("displayName"), str)
            else None,
        }
        at = where(entry, index)
        if at.kind == "unsupported":
            return Found(name, {**base, "status": "unsupported", "reason": at.reason})
        if at.kind == "here":
            folder, plugin_commit = here / (at.path or ""), commit
            # A link may point anywhere on the worker: its target must be in the repository
            if not await asyncio.to_thread(_within, folder, here):
                return Found(
                    name,
                    {**base, "status": "rejected", "reason": "its folder is outside"},
                )
        else:
            try:
                async with self.fetching:
                    repo = await remote(at.url or "", self.settings)
                    plugin_commit = at.sha or await head_of(repo, at.ref, self.home)
                    before = known.get(name)
                    if (
                        before is not None
                        and before.commit == plugin_commit
                        and before.status == "loaded"
                        and before.source == base["source"]
                    ):
                        return Found(name, {}, unchanged=True)
                    dest = Path(self.home) / f"plugin-{i}"
                    plugin_commit = await checkout(
                        repo, dest, self.home, at.ref, at.sha, at.path
                    )
                    folder = dest / (at.path or "")
            except SourceError as e:
                before = known.get(name)
                if before is not None and before.status == "loaded":
                    # Keep the last version fetched; the source says what failed
                    return Found(name, {}, unchanged=True, problem=f"{name}: {e}")
                return Found(name, {**base, "status": "failed", "reason": str(e)})
        loaded = await asyncio.to_thread(plugins.load, folder, entry)
        values = {
            **base,
            "commit": plugin_commit,
            "format": loaded.format,
            "report": plugins.report(loaded),
        }
        if loaded.rejected:
            return Found(
                name, {**values, "status": "rejected", "reason": loaded.rejected}
            )
        try:
            files = await asyncio.to_thread(skill_files, folder, loaded)
        except SourceError as e:
            return Found(name, {**values, "status": "failed", "reason": str(e)})
        manifest = loaded.manifest
        values.update(
            status="loaded",
            reason=None,
            title=loaded.title or base["title"] or None,
            description=manifest.get("description") or base["description"],
            version=manifest.get("version"),
            digest=digest(files),
        )
        before = known.get(name)
        same = before is not None and before.digest == values["digest"]
        return Found(name, values, files=None if same else files)

    async def run(self, source_id: uuid.UUID) -> SyncSummary:
        engine = self.runtime.engine
        async with engine.connect() as conn:
            source = (
                await conn.execute(
                    select(PluginSource).where(PluginSource.id == source_id)
                )
            ).first()
            if source is None:
                return SyncSummary()
            known = {
                row.name: row
                for row in await conn.execute(
                    select(
                        Plugin.name,
                        Plugin.commit,
                        Plugin.status,
                        Plugin.digest,
                        Plugin.source,
                    ).where(Plugin.source_id == source_id)
                )
            }
        summary = SyncSummary()
        try:
            repo = await remote(source.url, self.settings)
            here = Path(self.home) / "marketplace"
            commit = await checkout(repo, here, self.home, source.ref)
            fmt, index = await asyncio.to_thread(read_marketplace, here)
        except SourceError as e:
            async with engine.begin() as conn:
                await conn.execute(
                    update(PluginSource)
                    .where(PluginSource.id == source_id)
                    .values(status="failed", error=str(e), synced_at=func.now())
                )
            summary.problems.append(str(e))
            return summary
        entries = [e for e in index["plugins"] if isinstance(e, dict)][:MAX_PLUGINS]
        found = await asyncio.gather(
            *(
                self.plugin(i, entry, index, here, commit, known)
                for i, entry in enumerate(entries)
            )
        )
        seen: dict[str, Found] = {}
        for f in found:
            if f is not None and f.name not in seen:
                seen[f.name] = f
        summary.problems = [f.problem for f in seen.values() if f.problem]
        # Plugins the marketplace no longer lists: their connectors' tokens revoked first
        async with engine.connect() as conn:
            leaving = list(
                (
                    await conn.execute(
                        select(Plugin.id).where(
                            Plugin.source_id == source_id,
                            Plugin.name.not_in(list(seen)),
                        )
                    )
                ).scalars()
            )
        await plugin_connectors.revoke_for_plugins(self.runtime, leaving)
        async with engine.begin() as conn:
            for f in seen.values():
                if f.unchanged:
                    continue
                values = {
                    "title": None,
                    "description": None,
                    "version": None,
                    "format": None,
                    "reason": None,
                    "commit": None,
                    "report": {},
                    "digest": None,
                    **f.values,
                }
                row_id = (
                    await conn.execute(
                        insert(Plugin)
                        .values(source_id=source_id, name=f.name, **values)
                        .on_conflict_do_update(
                            constraint="uq_plugins_source_name",
                            set_={**values, "synced_at": func.now()},
                        )
                        .returning(Plugin.id)
                    )
                ).scalar_one()
                if f.files is not None or values["status"] != "loaded":
                    await conn.execute(
                        delete(PluginFile).where(PluginFile.plugin_id == row_id)
                    )
                    summary.changed += 1
                if f.files:
                    await conn.execute(
                        insert(PluginFile),
                        [
                            {"plugin_id": row_id, "path": p, "content": c}
                            for p, c in f.files.items()
                        ],
                    )
            gone = await conn.execute(
                delete(Plugin)
                .where(Plugin.source_id == source_id, Plugin.name.not_in(list(seen)))
                .returning(Plugin.id)
            )
            summary.removed = len(gone.all())
            name = index.get("name")
            description = index.get("description") or (index.get("metadata") or {}).get(
                "description"
            )
            await conn.execute(
                update(PluginSource)
                .where(PluginSource.id == source_id)
                .values(
                    name=name if isinstance(name, str) else None,
                    description=description if isinstance(description, str) else None,
                    format=fmt,
                    commit=commit,
                    status="synced",
                    error="; ".join(summary.problems) or None,
                    synced_at=func.now(),
                    succeeded_at=func.now(),
                )
            )
        summary.plugins = len(seen)
        summary.loaded = sum(
            f.unchanged or f.values.get("status") == "loaded" for f in seen.values()
        )
        return summary


async def sync(runtime: Runtime, source_id: uuid.UUID) -> SyncSummary:
    """Syncs one source in a folder of its own, removed after."""
    home: str = await asyncio.to_thread(
        lambda: tempfile.mkdtemp(prefix="gen9-plugins-")
    )
    try:
        summary = await Syncer(runtime, home).run(source_id)
        log.info(
            "plugin source %s: %d plugins, %d loaded, %d changed, %d removed",
            source_id,
            summary.plugins,
            summary.loaded,
            summary.changed,
            summary.removed,
        )
        return summary
    finally:
        await asyncio.to_thread(shutil.rmtree, home, True)
