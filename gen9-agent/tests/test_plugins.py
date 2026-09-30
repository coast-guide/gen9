"""The plugin loader (plugins.py). The Agent Plugins conformance kit covers the spec
(`e2e/plugins-conformance.mjs`); these cover what's Gen9's own: which servers it connects to,
what it tells the admin, its namespace, and the reasons it gives."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from gen9_agent import plugins

pytestmark = pytest.mark.asyncio


def _plugin(root: Path, manifest: dict[str, Any] | None = None, **files: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.json").write_text(
        json.dumps(
            {"$schema": plugins.PLUGIN_SCHEMA, "name": "demo", **(manifest or {})}
        )
    )
    for name, text in files.items():
        path = root / name.replace("__", "/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def _mcp(servers: dict[str, Any]) -> str:
    return json.dumps({"$schema": plugins.MCP_SCHEMA, "mcpServers": servers})


async def _load(root: Path) -> plugins.Loaded:
    return await asyncio.to_thread(plugins.load, root)


async def test_gen9_connects_only_to_streamable_http_servers(tmp_path: Path) -> None:
    loaded = await _load(
        _plugin(
            tmp_path / "p",
            **{
                "mcp.json": _mcp(
                    {
                        "remote": {
                            "type": "streamable-http",
                            "url": "https://x.example/mcp",
                        },
                        "local": {"type": "stdio", "command": "node", "args": ["s.js"]},
                        "legacy": {"type": "sse", "url": "https://x.example/sse"},
                    }
                )
            },
        )
    )
    assert {s.name: s.connects for s in loaded.mcp_servers} == {
        "remote": True,
        "local": False,
        "legacy": False,
    }
    assert plugins.report(loaded)["mcp_servers"][0]["connects"] is True


async def test_a_skipped_part_says_why(tmp_path: Path) -> None:
    loaded = await _load(
        _plugin(
            tmp_path / "p",
            **{
                "skills__Wrong__SKILL.md": "---\nname: Wrong\ndescription: d\n---\n",
                "skills__ok__SKILL.md": "---\nname: ok\ndescription: fine\n---\nBody",
                "mcp.json": _mcp(
                    {
                        "plain": {
                            "type": "streamable-http",
                            "url": "http://x.example/mcp",
                        },
                        "twice": {
                            "type": "streamable-http",
                            "url": "https://x.example/mcp",
                            "headers": {"X-A": "1", "x-a": "2"},
                        },
                        "shell": {"type": "stdio", "command": "node server.js"},
                    }
                ),
            },
        )
    )
    why = {s.what: s.why for s in loaded.skipped}
    assert "not lowercase" in why["skills/Wrong"]
    assert "plain http" in why["mcp.json#plain"]
    assert "given twice" in why["mcp.json#twice"]
    assert "one executable name" in why["mcp.json#shell"]
    assert [s.name for s in loaded.skills] == ["ok"]
    assert loaded.skills[0].path == "skills/ok"


async def test_a_rejected_plugin_says_why_and_loads_nothing(tmp_path: Path) -> None:
    root = _plugin(
        tmp_path / "p",
        {"name": "Bad_Name", "hooks": {}},
        **{"skills__ok__SKILL.md": "---\nname: ok\ndescription: fine\n---\n"},
    )
    loaded = await _load(root)
    assert loaded.rejected and loaded.rejected.startswith("its name 'Bad_Name' is not")
    assert not loaded.skills and not loaded.reported


async def test_other_clients_parts_are_reported_or_noted_not_fatal(
    tmp_path: Path,
) -> None:
    loaded = await _load(
        _plugin(
            tmp_path / "p",
            {"hooks": "hooks.json", "extensions": {"com.example": {"x": [1]}}},
            **{
                "skills__ok__SKILL.md": (
                    "---\nname: ok\ndescription: fine\nargument-hint: <file>\n---\n"
                )
            },
        )
    )
    assert loaded.rejected is None
    assert [r.field for r in loaded.reported] == ["hooks"]
    assert "hooks" not in loaded.manifest
    assert loaded.notes == [
        "skills/ok: frontmatter argument-hint is not Agent Skills' own; kept"
    ]
    assert [s.name for s in loaded.skills] == ["ok"]


async def test_gen9_reads_its_own_namespace(tmp_path: Path) -> None:
    root = _plugin(tmp_path / "p", {"extensions": {"io.gen9": {"agents": True}}})
    (root / "io.gen9" / "agents").mkdir(parents=True)
    loaded = await _load(root)
    assert loaded.extension == {"agents": True}
    assert loaded.extension_dir is True
    assert (await _load(_plugin(tmp_path / "q"))).extension_dir is False


async def test_a_link_out_of_the_plugin_is_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside" / "evil"
    outside.mkdir(parents=True)
    (outside / "SKILL.md").write_text("---\nname: evil\ndescription: d\n---\n")
    root = _plugin(tmp_path / "p")
    (root / "skills").mkdir()
    (root / "skills" / "evil").symlink_to(outside)
    loaded = await _load(root)
    assert not loaded.skills
    assert [(s.what, s.why) for s in loaded.skipped] == [
        ("skills/evil", "it is outside the plugin's folder")
    ]


async def test_skill_md_must_be_named_exactly(tmp_path: Path) -> None:
    loaded = await _load(
        _plugin(
            tmp_path / "p",
            **{"skills__a__skill.md": "---\nname: a\ndescription: d\n---\n"},
        )
    )
    assert not loaded.skills and not loaded.skipped


async def test_an_mcp_json_of_another_version_is_skipped_whole(tmp_path: Path) -> None:
    config = {
        "$schema": "https://agent-plugins.org/schemas/1.1.0/mcp.schema.json",
        "mcpServers": {
            "r": {"type": "streamable-http", "url": "https://x.example/mcp"}
        },
    }
    loaded = await _load(_plugin(tmp_path / "p", **{"mcp.json": json.dumps(config)}))
    assert not loaded.mcp_servers
    assert [s.what for s in loaded.skipped] == ["mcp.json"]


def _client(root: Path, place: str, manifest: dict[str, Any], **files: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / place).mkdir()
    (root / place / "plugin.json").write_text(json.dumps(manifest))
    for name, text in files.items():
        path = root / name.replace("__", "/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


async def test_a_codex_plugin_brings_its_skills_and_remote_servers(
    tmp_path: Path,
) -> None:
    root = _client(
        tmp_path / "p",
        ".codex-plugin",
        {
            "name": "tracker",
            "version": "5.0.1",
            "skills": "./skills/",
            "mcpServers": "./.mcp.json",
            "apps": "./.app.json",
            "interface": {"displayName": "Tracker"},
        },
        **{
            "skills__triage__SKILL.md": "---\nname: triage-issues\ndescription: d\n---\n",
            ".mcp.json": json.dumps(
                {
                    "mcpServers": {
                        "tracker": {
                            "type": "http",
                            "url": "https://mcp.tracker.example/mcp",
                            "oauth": {"client_id": "x"},
                        },
                        "local": {"command": "npx", "args": ["tracker-mcp"]},
                    }
                }
            ),
        },
    )
    loaded = await _load(root)
    assert loaded.rejected is None and loaded.format == "codex"
    assert loaded.title == "Tracker"
    assert loaded.manifest == {"name": "tracker", "version": "5.0.1"}
    # Named unlike its folder, as Codex allows: kept, with a note
    assert [s.name for s in loaded.skills] == ["triage-issues"]
    assert {s.name: (s.type, s.connects) for s in loaded.mcp_servers} == {
        "tracker": ("streamable-http", True),
        "local": ("stdio", False),
    }
    assert "Gen9 doesn't use its apps" in loaded.notes
    assert any("not its folder's" in n for n in loaded.notes)
    assert any("registered for codex" in n for n in loaded.notes)


async def test_the_agent_plugins_format_keeps_its_strict_skill_rules(
    tmp_path: Path,
) -> None:
    loaded = await _load(
        _plugin(
            tmp_path / "p",
            **{"skills__a__SKILL.md": "---\nname: b\ndescription: d\n---\n"},
        )
    )
    assert not loaded.skills
    assert "not its folder's" in loaded.skipped[0].why


async def test_a_claude_plugin_reads_flat_mcp_json_and_extra_skill_paths(
    tmp_path: Path,
) -> None:
    root = _client(
        tmp_path / "p",
        ".claude-plugin",
        {
            "name": "helper",
            "author": "Someone",
            "skills": ["./extra"],
            "commands": "./c",
        },
        **{
            "skills__one__SKILL.md": "---\nname: one\ndescription: d\n---\n",
            "extra__SKILL.md": "---\nname: extra\ndescription: d\n---\n",
            ".mcp.json": json.dumps(
                {
                    "docs": {"type": "http", "url": "https://docs.example/mcp"},
                    "token": {
                        "type": "http",
                        "url": "https://api.example/mcp",
                        "headers": {"Authorization": "Bearer ${API_TOKEN}"},
                    },
                    "helper": {
                        "type": "http",
                        "url": "https://h.example/mcp",
                        "headersHelper": "./get-token.sh",
                    },
                }
            ),
        },
    )
    loaded = await _load(root)
    assert loaded.format == "claude"
    assert loaded.manifest["author"] == {"name": "Someone"}
    assert sorted(s.name for s in loaded.skills) == ["extra", "one"]
    assert [s.name for s in loaded.mcp_servers] == ["docs"]
    why = {s.what: s.why for s in loaded.skipped}
    assert "person's computer" in why[".mcp.json#token"]
    assert "running a command" in why[".mcp.json#helper"]
    assert "Gen9 doesn't use its commands" in loaded.notes


async def test_without_a_manifest_the_marketplace_entry_is_one(tmp_path: Path) -> None:
    root = tmp_path / "p"
    (root / "skills" / "one").mkdir(parents=True)
    (root / "skills" / "one" / "SKILL.md").write_text(
        "---\nname: one\ndescription: d\n---\n"
    )
    assert (await _load(root)).rejected
    entry = {
        "name": "bare",
        "source": "./p",
        "description": "From the entry",
        "strict": False,
    }
    loaded = await asyncio.to_thread(plugins.load, root, entry)
    assert loaded.rejected is None and loaded.format == "claude"
    assert loaded.manifest == {"name": "bare", "description": "From the entry"}
    assert [s.name for s in loaded.skills] == ["one"]


async def test_a_root_plugin_json_wins_over_a_clients_own(tmp_path: Path) -> None:
    root = _plugin(tmp_path / "p")
    (root / ".codex-plugin").mkdir()
    (root / ".codex-plugin" / "plugin.json").write_text(json.dumps({"name": "other"}))
    loaded = await _load(root)
    assert loaded.format == "agent-plugins" and loaded.name == "demo"


async def test_a_rejection_is_said_in_words(tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    (root / "plugin.json").write_text(json.dumps({"$schema": plugins.PLUGIN_SCHEMA}))
    assert (await _load(root)).rejected == "plugin.json has no name"
    (root / "plugin.json").write_text(
        json.dumps({"$schema": plugins.PLUGIN_SCHEMA, "name": "ok", "description": 3})
    )
    assert (await _load(root)).rejected == "description is not 'string'"
