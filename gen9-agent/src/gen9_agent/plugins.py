"""Plugins in the Agent Plugins format (agent-plugins.org/specification, 1.0.0): Gen9's loader
(docs/plans/harness.md, milestone 4).

A plugin is a folder: a closed `plugin.json`, skills under `skills/<name>/SKILL.md` (the Agent
Skills format) and MCP servers in `mcp.json`. `load` checks one as the spec says a client must:

- **The manifest** against the official schema (`plugin_schemas/`, never fetched). An unknown
  top-level field, or an `extensions` that isn't an object, is reported and ignored. Anything
  else wrong rejects the plugin, and none of it is read: a namespace whose value isn't an object
  too (§5.2 and §8.1 as written; 1.1 goes further, spec PR #82). Inside another client's
  namespace, nothing is checked.
- **Every path stays in the folder**, links included: a link that leaves it rejects the plugin
  (the manifest), drops a component type (`skills/`, `mcp.json`) or one skill.
- **Skills** are the immediate folders of `skills/` holding a file named exactly `SKILL.md`. One
  that breaks the Agent Skills rules is skipped. Frontmatter keys beyond the documented ones are
  noted, not fatal: the Agent Skills spec doesn't close the set, and plugins written for other
  clients carry theirs.
- **MCP servers**: `mcp.json` must be the same spec version and closed; each entry must be exactly
  one transport's shape and pass the rules the schema can't express (a single-token command, a
  working directory inside the plugin, an https URL unless loopback, no user info or fragment,
  valid headers). A bad entry is skipped; a bad file skips them all.
- **Gen9's own part** is the `io.gen9` namespace: `extensions["io.gen9"]` and an `io.gen9/`
  folder. Other clients' namespaces are ignored.
- **Without a root `plugin.json`**, a plugin published in Codex's or Claude Code's own format
  loads for the same two parts, as those clients read them (`_client_plugin`): today's official
  marketplaces use nothing else (explore/plugins/NOTES.md).

What's skipped is named as the conformance kit names it (`skills`, `skills/<folder>`, `mcp.json`,
`mcp.json#<server>`), with why. Loading only reads: run `load` in a thread
(`asyncio.to_thread`). `python -m gen9_agent.plugins <folder>` prints the report as JSON.
"""

import asyncio
import ipaddress
import json
import os
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass, field
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from jsonschema import Draft202012Validator
from jsonschema.protocols import Validator

VERSION = "1.0.0"
PLUGIN_SCHEMA = f"https://agent-plugins.org/schemas/{VERSION}/plugin.schema.json"
MCP_SCHEMA = f"https://agent-plugins.org/schemas/{VERSION}/mcp.schema.json"
NAMESPACE = "io.gen9"
# The transports Gen9 connects to: its server never runs a plugin's processes (stdio), and the
# legacy HTTP+SSE transport is optional (Decision Log)
CONNECTS = frozenset({"streamable-http"})

_SCHEMAS = Path(__file__).with_name("plugin_schemas") / VERSION
_SKILL_FIELDS = frozenset(
    {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
)
# RFC 9110 token (a header's name) and field value (no controls but tab)
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_HEADER_VALUE = re.compile(r"^[\t\x20-\x7e\x80-\U0010ffff]*$")
_ROOT = "${PLUGIN_ROOT}"
_DATA = "${PLUGIN_DATA}"


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: str  # its folder, relative to the plugin's root: skills/<name>


@dataclass(frozen=True)
class McpServer:
    name: str
    type: str  # stdio, streamable-http or sse
    config: dict[str, Any]  # the entry as written

    @property
    def connects(self) -> bool:
        return self.type in CONNECTS


@dataclass(frozen=True)
class Skipped:
    what: str  # skills, skills/<folder>, mcp.json or mcp.json#<server>
    why: str


@dataclass(frozen=True)
class Reported:
    field: str  # a manifest field reported and ignored
    why: str


@dataclass
class Loaded:
    """What loading a folder found. `rejected` says why it isn't a plugin; then nothing else is
    set."""

    rejected: str | None = None
    manifest: dict[str, Any] = field(default_factory=dict)
    skills: list[Skill] = field(default_factory=list)
    mcp_servers: list[McpServer] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    reported: list[Reported] = field(default_factory=list)
    notes: list[str] = field(
        default_factory=list
    )  # accepted, but worth telling the admin
    extension: dict[str, Any] = field(default_factory=dict)  # extensions["io.gen9"]
    extension_dir: bool = False  # an io.gen9/ folder
    # agent-plugins, or the client's own format it was published in: codex or claude
    format: str = "agent-plugins"
    title: str | None = None  # a display name, where the client's format has one

    @property
    def name(self) -> str:
        return self.manifest.get("name", "")


@cache
def _schema(kind: str) -> dict[str, Any]:
    return json.loads((_SCHEMAS / f"{kind}.schema.json").read_text())


@cache
def _validator(kind: str, ref: str | None = None) -> Validator:
    schema = _schema(kind)
    if ref:
        schema = {"$ref": ref, "$defs": schema["$defs"]}
    return Draft202012Validator(schema)


def _inside(path: Path, root: Path) -> bool:
    """Whether `path`, links resolved, is `root` or under it (root already resolved)."""
    return path.resolve().is_relative_to(root)


def _is_exactly(folder: Path, name: str) -> bool:
    """Whether `folder` has an entry named exactly `name` (a case-insensitive disk would find
    `skill.md` for `SKILL.md`)."""
    try:
        return name in os.listdir(folder)
    except OSError:
        return False


def _read_json(path: Path) -> tuple[Any, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        return None, f"not valid JSON: {e}"


def _manifest(root: Path, loaded: Loaded) -> None:
    path = root / "plugin.json"
    if not os.path.lexists(path) or not _is_exactly(root, "plugin.json"):
        loaded.rejected = "no plugin.json"
        return
    if not _inside(path, root):
        loaded.rejected = "plugin.json is outside the plugin's folder"
        return
    if not path.is_file():
        loaded.rejected = "plugin.json is not a file"
        return
    manifest, error = _read_json(path)
    if error:
        loaded.rejected = f"plugin.json is {error}"
        return
    if not isinstance(manifest, dict):
        loaded.rejected = "plugin.json is not a JSON object"
        return
    if "$schema" not in manifest:
        loaded.rejected = "plugin.json has no $schema"
        return
    if manifest["$schema"] != PLUGIN_SCHEMA:
        loaded.rejected = (
            f"plugin.json targets {manifest['$schema']!r}; Gen9 reads {PLUGIN_SCHEMA}"
        )
        return
    allowed = set(_schema("plugin")["properties"])
    for error in _validator("plugin").iter_errors(manifest):
        at = list(error.absolute_path)
        if not at and error.validator == "additionalProperties":
            continue  # unknown fields: reported below
        if at == ["extensions"] and error.validator == "type":
            continue  # reported below
        loaded.rejected = _in_words(at, error)
        return
    for unknown in sorted(set(manifest) - allowed):
        loaded.reported.append(Reported(unknown, "not a field of plugin.json; ignored"))
        del manifest[unknown]
    if "extensions" in manifest and not isinstance(manifest["extensions"], dict):
        loaded.reported.append(Reported("extensions", "not an object; ignored"))
        del manifest["extensions"]
    loaded.manifest = manifest
    loaded.extension = manifest.get("extensions", {}).get(NAMESPACE, {})


def _in_words(at: list[Any], error: Any) -> str:
    """Why a manifest is rejected, for the admin who reads it; jsonschema's message names a
    pattern, not a rule."""
    if at == ["name"]:
        return (
            f"its name {error.instance!r} is not lowercase letters, digits, hyphens and"
            " periods (at most 64, starting and ending with a letter or digit)"
        )
    where = ".".join(str(p) for p in at) or "plugin.json"
    if error.validator == "type":
        return f"{where} is not {error.validator_value!r}"
    if error.validator == "required":
        return f"plugin.json has no {error.message.split(chr(39))[1]}"
    return f"{where}: {error.message}"


def _frontmatter(text: str) -> tuple[dict[str, Any] | None, str | None]:
    lines = text.removeprefix("\ufeff").splitlines()
    if not lines or lines[0].strip() != "---":
        return None, "SKILL.md has no frontmatter"
    try:
        end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration:
        return None, "SKILL.md's frontmatter never ends"
    try:
        meta = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as e:
        return None, f"SKILL.md's frontmatter is not valid YAML: {e}"
    if not isinstance(meta, dict):
        return None, "SKILL.md's frontmatter is not a mapping"
    return meta, None


# Marks a rule that the clients' own formats don't hold a skill to: Codex and Claude Code load
# such a skill, and Deep Agents keeps it with a warning (a longer description cut to 1024)
_SOFT = "~"


def skill_problems(meta: dict[str, Any], folder: str) -> list[str]:
    """What breaks the Agent Skills rules (agentskills.io/specification) in a skill's
    frontmatter, for a skill in `folder`. A problem starting with `_SOFT` doesn't keep a
    client's own format from loading it."""
    problems = []
    name = meta.get("name")
    if not isinstance(name, str) or not name.strip():
        problems.append("no name")
    else:
        name = unicodedata.normalize("NFKC", name.strip())
        if len(name) > 64:
            problems.append("a name longer than 64 characters")
        if name != name.lower() or not all(c.isalnum() or c == "-" for c in name):
            problems.append(
                f"the name {name!r} is not lowercase letters, digits and hyphens"
            )
        if name.startswith("-") or name.endswith("-") or "--" in name:
            problems.append(
                f"the name {name!r} starts or ends with a hyphen, or repeats one"
            )
        if name != unicodedata.normalize("NFKC", folder):
            problems.append(f"{_SOFT}the name {name!r} is not its folder's, {folder!r}")
    description = meta.get("description")
    if not isinstance(description, str) or not description.strip():
        problems.append("no description")
    elif len(description) > 1024:
        problems.append(f"{_SOFT}a description longer than 1024 characters")
    compatibility = meta.get("compatibility")
    if compatibility is not None and (
        not isinstance(compatibility, str) or len(compatibility) > 500
    ):
        problems.append("a compatibility that is not text of at most 500 characters")
    return problems


def _skill(root: Path, loaded: Loaded, folder: Path, what: str) -> None:
    """The skill in `folder` (holding a SKILL.md), if it's one: added, skipped with why, or left
    alone when it isn't a skill at all."""
    if not _inside(folder, root):
        loaded.skipped.append(Skipped(what, "it is outside the plugin's folder"))
        return
    if not folder.is_dir() or not _is_exactly(folder, "SKILL.md"):
        return  # not a skill, and not an error
    skill_md = folder / "SKILL.md"
    if not _inside(skill_md, root):
        loaded.skipped.append(
            Skipped(what, "its SKILL.md is outside the plugin's folder")
        )
        return
    if not skill_md.is_file():
        return
    try:
        text = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        loaded.skipped.append(Skipped(what, f"SKILL.md can't be read: {e}"))
        return
    meta, error = _frontmatter(text)
    if meta is None:
        loaded.skipped.append(Skipped(what, error or "no frontmatter"))
        return
    problems = skill_problems(meta, folder.name)
    if loaded.format != "agent-plugins" and all(p.startswith(_SOFT) for p in problems):
        loaded.notes.extend(f"{what}: {p.removeprefix(_SOFT)}; kept" for p in problems)
        problems = []
    if problems:
        loaded.skipped.append(
            Skipped(what, "; ".join(p.removeprefix(_SOFT) for p in problems))
        )
        return
    name = meta["name"].strip()
    if any(s.name == name for s in loaded.skills):
        loaded.skipped.append(Skipped(what, f"another skill is named {name!r}"))
        return
    if extra := sorted(set(meta) - _SKILL_FIELDS, key=str):
        loaded.notes.append(
            f"{what}: frontmatter {', '.join(map(str, extra))} is not Agent Skills'"
            " own; kept"
        )
    loaded.skills.append(
        Skill(name=name, description=meta["description"].strip(), path=what)
    )


def _skills(root: Path, loaded: Loaded, place: str = "skills") -> None:
    """The skills in `place` (relative to the root, without `./`): each immediate folder holding
    a SKILL.md. A client format's own place may be a skill itself (Claude Code's `skills`)."""
    folder = root / place if place else root
    if not os.path.lexists(folder):
        return
    if not _inside(folder, root):
        loaded.skipped.append(
            Skipped(place, f"{place}/ is outside the plugin's folder")
        )
        return
    if not folder.is_dir():
        loaded.skipped.append(Skipped(place, f"{place} is not a folder"))
        return
    if place != "skills" and _is_exactly(folder, "SKILL.md"):
        _skill(root, loaded, folder, place)
        return
    for child in sorted(folder.iterdir()):
        _skill(root, loaded, child, f"{place}/{child.name}" if place else child.name)


def _within(path: str, base: str) -> bool:
    """Whether `path` stays under `base` once `..` are resolved on paper."""
    return os.path.normpath(path) == base or os.path.normpath(path).startswith(
        base.rstrip("/") + "/"
    )


def _stdio_problem(entry: dict[str, Any], root: Path) -> str | None:
    command = entry["command"]
    if command.startswith("./"):
        if not _inside(root / command, root):
            return "its command is outside the plugin's folder"
    elif any(c.isspace() for c in command) or "/" in command or "\\" in command:
        return "its command is not one executable name or a ./ path in the plugin"
    cwd = entry.get("cwd")
    if cwd is not None:
        if cwd.startswith("./"):
            if not _inside(root / cwd, root):
                return "its cwd is outside the plugin's folder"
        elif cwd == _ROOT or cwd.startswith(_ROOT + "/"):
            if not _inside(root / cwd.removeprefix(_ROOT).lstrip("/"), root):
                return "its cwd is outside the plugin's folder"
        elif cwd == _DATA or cwd.startswith(_DATA + "/"):
            data = "/plugin-data"  # wherever it is, a path must stay in it
            if not _within(data + cwd.removeprefix(_DATA), data):
                return "its cwd is outside the plugin's data folder"
        else:
            return "its cwd is not ./…, ${PLUGIN_ROOT}… or ${PLUGIN_DATA}…"
    return None


def _remote_problem(entry: dict[str, Any]) -> str | None:
    url = entry["url"]
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "its url is not an absolute http or https URL"
    if "@" in parts.netloc:
        return "its url carries user information"
    if "#" in url:
        return "its url carries a fragment"
    if parts.scheme == "http":
        host = parts.hostname
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host == "localhost"
        if not loopback:
            return "its url is plain http to a host other than this computer"
    seen: set[str] = set()
    for name, value in entry.get("headers", {}).items():
        if not _HEADER_NAME.match(name) or not _HEADER_VALUE.match(value):
            return f"its header {name!r} is not a valid HTTP header"
        if name.lower() in seen:
            return f"its header {name!r} is given twice"
        seen.add(name.lower())
    return None


def _mcp(root: Path, loaded: Loaded) -> None:
    path = root / "mcp.json"
    if not os.path.lexists(path):
        return
    if not _inside(path, root):
        loaded.skipped.append(Skipped("mcp.json", "it is outside the plugin's folder"))
        return
    if not path.is_file():
        loaded.skipped.append(Skipped("mcp.json", "it is not a file"))
        return
    config, error = _read_json(path)
    if error:
        loaded.skipped.append(Skipped("mcp.json", f"it is {error}"))
        return
    if not isinstance(config, dict):
        loaded.skipped.append(Skipped("mcp.json", "it is not a JSON object"))
        return
    if config.get("$schema") != MCP_SCHEMA:
        loaded.skipped.append(
            Skipped(
                "mcp.json",
                f"its $schema is {config.get('$schema')!r}, not {MCP_SCHEMA} as plugin.json's",
            )
        )
        return
    if extra := sorted(set(config) - {"$schema", "mcpServers"}):
        loaded.skipped.append(
            Skipped("mcp.json", f"it has fields of no MCP configuration: {extra}")
        )
        return
    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        loaded.skipped.append(Skipped("mcp.json", "its mcpServers is not an object"))
        return
    for name, entry in servers.items():
        what = f"mcp.json#{name}"
        errors = list(_validator("mcp", "#/$defs/server").iter_errors(entry))
        if errors:
            loaded.skipped.append(Skipped(what, _server_error(entry)))
            continue
        problem = (
            _stdio_problem(entry, root)
            if entry["type"] == "stdio"
            else _remote_problem(entry)
        )
        if problem:
            loaded.skipped.append(Skipped(what, problem))
            continue
        loaded.mcp_servers.append(McpServer(name, entry["type"], entry))


def _server_error(entry: Any) -> str:
    """Why an entry matches no transport's shape, in words (jsonschema's `oneOf` message names
    no field)."""
    if not isinstance(entry, dict):
        return "it is not an object"
    kind = entry.get("type")
    if kind not in ("stdio", "streamable-http", "sse"):
        return f"its type {kind!r} is not stdio, streamable-http or sse"
    ref = {
        "stdio": "stdioServer",
        "streamable-http": "streamableHttpServer",
        "sse": "sseServer",
    }[kind]
    error = next(iter(_validator("mcp", f"#/$defs/{ref}").iter_errors(entry)), None)
    if error is None:
        return "it matches no transport"
    at = ".".join(str(p) for p in error.absolute_path)
    return f"{at + ': ' if at else ''}{error.message}"


# Plugins published in a client's own format, read for the same two parts (Decision Log):
# skills (`skills/`, and the manifest's `skills` paths, as Claude Code adds them) and MCP
# servers (`.mcp.json`, and the manifest's `mcpServers`: files or inline, later names replacing
# earlier ones). What else they hold is noted as unused.
CLIENT_FORMATS = (
    ("codex", ".codex-plugin/plugin.json"),
    ("claude", ".claude-plugin/plugin.json"),
)
_NAME = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
_METADATA = ("version", "description", "homepage", "repository", "license")
_UNUSED = frozenset(
    {
        "commands",
        "agents",
        "hooks",
        "lspServers",
        "outputStyles",
        "apps",
        "userConfig",
        "channels",
        "monitors",
        "workflows",
        "themes",
        "experimental",
        "dependencies",
        "settings",
    }
)
# A marketplace entry's own fields, not the plugin's
_ENTRY = frozenset(
    {"source", "category", "tags", "strict", "relevance", "defaultEnabled", "policy"}
)
_VARIABLE = re.compile(r"\$\{[^}]*\}")


def _client_plugin(root: Path, loaded: Loaded, entry: dict[str, Any] | None) -> Loaded:
    for fmt, place in CLIENT_FORMATS:
        path = root / place
        if os.path.lexists(path):
            loaded.format = fmt
            if not _inside(path, root) or not path.is_file():
                loaded.rejected = f"{place} is not a file in the plugin's folder"
                return loaded
            manifest, error = _read_json(path)
            if error or not isinstance(manifest, dict):
                loaded.rejected = f"{place} is {error or 'not a JSON object'}"
                return loaded
            break
    else:
        if entry is None:
            loaded.rejected = "no plugin.json, .codex-plugin/plugin.json or .claude-plugin/plugin.json"
            return loaded
        loaded.format = "claude"
        manifest = {k: v for k, v in entry.items() if k not in _ENTRY}
    name = manifest.get("name")
    if not isinstance(name, str) or len(name) > 64 or not _NAME.match(name):
        loaded.rejected = (
            f"its name {name!r} is not lowercase letters, digits, hyphens and periods"
        )
        return loaded
    kept: dict[str, Any] = {"name": name}
    for key in _METADATA:
        if isinstance(manifest.get(key), str):
            kept[key] = manifest[key]
    author = manifest.get("author")
    if isinstance(author, str):
        kept["author"] = {"name": author}
    elif isinstance(author, dict):
        kept["author"] = {k: v for k, v in author.items() if isinstance(v, str)}
    keywords = manifest.get("keywords")
    if isinstance(keywords, list):
        kept["keywords"] = [k for k in keywords if isinstance(k, str)]
    loaded.manifest = kept
    title = manifest.get("displayName") or (manifest.get("interface") or {}).get(
        "displayName"
    )
    loaded.title = title if isinstance(title, str) else None
    if unused := sorted(set(manifest) & _UNUSED):
        loaded.notes.append(f"Gen9 doesn't use its {', '.join(unused)}")
    places = ["skills"]
    declared = manifest.get("skills")
    for path in declared if isinstance(declared, list) else [declared]:
        if path is None:
            continue
        if not isinstance(path, str) or not (path == "." or path.startswith("./")):
            loaded.skipped.append(Skipped("skills", f"{path!r} is not a ./ path"))
            continue
        place = os.path.normpath(path).removeprefix("./")
        place = "" if place == "." else place
        if place not in places:
            places.append(place)
    for place in places:
        _skills(root, loaded, place)
    _client_mcp(root, loaded, manifest.get("mcpServers"))
    return loaded


def _client_mcp(root: Path, loaded: Loaded, declared: Any) -> None:
    found: dict[str, tuple[str, Any]] = {}  # name: (where it was declared, its config)

    def read(place: str, default: bool = False) -> None:
        path = root / place
        if not os.path.lexists(path):
            if not default:
                loaded.skipped.append(Skipped(place, "it doesn't exist"))
            return
        if not _inside(path, root) or not path.is_file():
            loaded.skipped.append(
                Skipped(place, "it is not a file in the plugin's folder")
            )
            return
        config, error = _read_json(path)
        if error or not isinstance(config, dict):
            loaded.skipped.append(
                Skipped(place, f"it is {error or 'not a JSON object'}")
            )
            return
        servers = config.get("mcpServers", config)
        if not isinstance(servers, dict):
            loaded.skipped.append(Skipped(place, "its mcpServers is not an object"))
            return
        for name, server in servers.items():
            found[name] = (place, server)

    read(".mcp.json", default=True)
    for item in declared if isinstance(declared, list) else [declared]:
        if item is None:
            continue
        if isinstance(item, dict):
            for name, server in item.items():
                found[name] = ("plugin.json", server)
        elif isinstance(item, str) and item.endswith((".mcpb", ".dxt")):
            loaded.notes.append(f"Gen9 doesn't use MCP bundles ({item})")
        elif isinstance(item, str) and item.startswith("./"):
            place = os.path.normpath(item).removeprefix("./")
            if place != ".mcp.json":
                read(place)
        else:
            loaded.skipped.append(Skipped("mcpServers", f"{item!r} is not a ./ path"))
    for name, (place, server) in found.items():
        config, problem = _client_server(server)
        if problem or config is None:
            loaded.skipped.append(Skipped(f"{place}#{name}", problem or "?"))
            continue
        if isinstance(server, dict) and "oauth" in server:
            loaded.notes.append(
                f"{place}#{name}: signs in with a client registered for"
                f" {loaded.format}; Gen9 registers its own"
            )
        loaded.mcp_servers.append(McpServer(name, config["type"], config))


def _client_server(server: Any) -> tuple[dict[str, Any] | None, str | None]:
    """A server as a client's format writes it, in the Agent Plugins shape: `http` is streamable
    HTTP, and a server with a command but no type is stdio."""
    if not isinstance(server, dict):
        return None, "it is not an object"
    kind = server.get("type") or ("stdio" if "command" in server else None)
    if kind == "stdio":
        command = server.get("command")
        if not isinstance(command, str) or not command:
            return None, "it has no command"
        return {"type": "stdio", "command": command}, None
    if kind not in ("http", "streamable-http", "sse"):
        return None, f"its type {kind!r} is not stdio, http or sse"
    url, headers = server.get("url"), server.get("headers") or {}
    if not isinstance(url, str) or not isinstance(headers, dict):
        return None, "it has no url, or its headers are not an object"
    if "headersHelper" in server:
        return None, "it gets its headers by running a command on the person's computer"
    if not all(isinstance(v, str) for v in headers.values()):
        return None, "its headers are not all text"
    if _VARIABLE.search(url) or any(_VARIABLE.search(v) for v in headers.values()):
        return None, "its url or headers need a value from the person's computer (${…})"
    config: dict[str, Any] = {
        "type": "sse" if kind == "sse" else "streamable-http",
        "url": url,
    }
    if headers:
        config["headers"] = headers
    return (None, problem) if (problem := _remote_problem(config)) else (config, None)


def load(folder: Path, entry: dict[str, Any] | None = None) -> Loaded:
    """The plugin in `folder`, as far as the spec lets a client load it. Without a root
    `plugin.json`, a client's own format (`CLIENT_FORMATS`); without that either, its
    marketplace `entry`, as Claude Code reads one."""
    loaded = Loaded()
    root = folder.resolve()
    if not os.path.lexists(root / "plugin.json"):
        return _client_plugin(root, loaded, entry)
    _manifest(root, loaded)
    if loaded.rejected:
        return loaded
    _skills(root, loaded)
    _mcp(root, loaded)
    own = root / NAMESPACE
    loaded.extension_dir = os.path.lexists(own) and _inside(own, root) and own.is_dir()
    return loaded


def report(loaded: Loaded) -> dict[str, Any]:
    """The load report, as JSON."""
    out = asdict(loaded)
    for server, row in zip(loaded.mcp_servers, out["mcp_servers"], strict=True):
        row["connects"] = server.connects
    return out


async def _main(folder: str) -> None:
    loaded = await asyncio.to_thread(load, Path(folder))
    print(json.dumps(report(loaded), indent=2))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m gen9_agent.plugins <plugin folder>")
    asyncio.run(_main(sys.argv[1]))
