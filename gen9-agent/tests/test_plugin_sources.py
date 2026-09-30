"""Plugin sources (plugin_sources.py): where a marketplace entry's plugin is, reading marketplaces,
the files kept, and the checks before git runs. Fetching from a real git server is e2e's
(`e2e/plugins.mjs`)."""

import asyncio
import json
import os
from pathlib import Path

import pytest

from gen9_agent import plugin_sources, plugins
from gen9_agent.plugin_sources import SourceError, where
from gen9_agent.settings import Settings

pytestmark = pytest.mark.asyncio


def _settings(**kw: object) -> Settings:
    return Settings.model_construct(**{"plugin_sources_allowed_hosts": [], **kw})


async def test_where_a_plugin_is() -> None:
    index = {"metadata": {"pluginRoot": "./plugins"}}
    assert where({"source": "./plugins/a"}, index).path == "plugins/a"
    assert where({"source": "a"}, index).path == "plugins/a"
    assert where({"source": {"source": "local", "path": "./x"}}, {}).path == "x"
    github = where({"source": {"source": "github", "repo": "org/p", "ref": "v1"}}, {})
    assert (github.kind, github.url, github.ref) == (
        "git",
        "https://github.com/org/p.git",
        "v1",
    )
    sub = where(
        {"source": {"source": "git-subdir", "url": "org/mono", "path": "tools/p"}}, {}
    )
    assert (sub.url, sub.path) == ("https://github.com/org/mono.git", "tools/p")
    for source, why in [
        ("../escape", "not a ./ path"),
        ({"source": "npm", "package": "p"}, "not npm"),
        ({"source": "command", "command": "make"}, "runs no command"),
        ({"source": "url", "url": "https://x.example/p", "ref": "-x"}, "branch or tag"),
        ({"source": "url", "url": "https://x.example/p", "sha": "abc"}, "full commit"),
        ({"source": "git-subdir", "url": "org/m", "path": "../x"}, "folder inside"),
    ]:
        found = where({"source": source}, {})
        assert found.kind == "unsupported" and why in (found.reason or ""), source


async def test_a_marketplace_in_either_format(tmp_path: Path) -> None:
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({"name": "m", "owner": {"name": "o"}, "plugins": []})
    )
    fmt, index = await asyncio.to_thread(plugin_sources.read_marketplace, tmp_path)
    assert (fmt, index["name"]) == ("claude", "m")
    (tmp_path / ".agents" / "plugins").mkdir(parents=True)
    (tmp_path / ".agents" / "plugins" / "marketplace.json").write_text("not json")
    with pytest.raises(SourceError, match="not valid JSON"):
        await asyncio.to_thread(plugin_sources.read_marketplace, tmp_path)


async def test_no_marketplace_says_where_it_looked(tmp_path: Path) -> None:
    with pytest.raises(SourceError, match=r"\.agents/plugins/marketplace\.json"):
        await asyncio.to_thread(plugin_sources.read_marketplace, tmp_path)


async def test_only_the_skills_files_are_kept_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "p"
    (root / "skills" / "a" / "references").mkdir(parents=True)
    (root / "plugin.json").write_text(
        json.dumps({"$schema": plugins.PLUGIN_SCHEMA, "name": "p"})
    )
    (root / "skills" / "a" / "SKILL.md").write_text(
        "---\nname: a\ndescription: d\n---\n"
    )
    (root / "skills" / "a" / "references" / "r.md").write_text("ref")
    (root / "README.md").write_text("not a skill's")
    (tmp_path / "secret").write_text("outside")
    os.symlink(tmp_path / "secret", root / "skills" / "a" / "leak")
    loaded = await asyncio.to_thread(plugins.load, root)
    files = await asyncio.to_thread(plugin_sources.skill_files, root, loaded)
    assert sorted(files) == ["skills/a/SKILL.md", "skills/a/references/r.md"]
    assert plugin_sources.digest(files) == plugin_sources.digest(dict(files))
    # Bounded per skill, as the leading products bound one, and per plugin
    monkeypatch.setattr(plugin_sources, "MAX_SKILL_BYTES", 10)
    with pytest.raises(SourceError, match="Its skill a is over 500 files or 0 MB"):
        await asyncio.to_thread(plugin_sources.skill_files, root, loaded)
    monkeypatch.setattr(plugin_sources, "MAX_SKILL_BYTES", 25 * 2**20)
    monkeypatch.setattr(plugin_sources, "MAX_PLUGIN_FILES", 1)
    with pytest.raises(SourceError, match="Its skills are over 1 files"):
        await asyncio.to_thread(plugin_sources.skill_files, root, loaded)


async def test_a_source_must_be_public_https() -> None:
    settings = _settings()
    for url, why in [
        ("http://203.0.113.9/repo.git", "https"),
        ("https://user:pw@203.0.113.9/repo.git", "isn't a repository address"),
        ("https://127.0.0.1/repo.git", "private network"),
        ("https://10.1.2.3/repo.git", "private network"),
        # An IPv4 address in NAT64's prefix is the IPv4 address (M9, F15)
        ("https://[64:ff9b::a9fe:a9fe]/repo.git", "private network"),
        ("https://203.0.113.9/repo.git?x=1", "isn't a repository address"),
    ]:
        with pytest.raises(SourceError, match=why):
            await plugin_sources.remote(url, settings)
    checked = await plugin_sources.remote("https://8.8.8.8/repo.git", settings)
    assert checked.pinned == "8.8.8.8:443:8.8.8.8" and not checked.named


async def test_a_named_host_may_be_private_and_http() -> None:
    settings = _settings(plugin_sources_allowed_hosts=["127.0.0.1:17805"])
    checked = await plugin_sources.remote("http://127.0.0.1:17805/m.git", settings)
    assert checked.named and checked.pinned == "127.0.0.1:17805:127.0.0.1"
    with pytest.raises(SourceError, match="private network"):
        await plugin_sources.remote("https://127.0.0.1:17806/m.git", settings)


async def test_git_runs_with_nothing_of_the_workers_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEN9_SECRET_KEYS", "k:secret")
    env = plugin_sources._environment("/tmp/h", None)
    assert "GEN9_SECRET_KEYS" not in env
    assert env["GIT_ALLOW_PROTOCOL"] == "https"
    assert env["GIT_CONFIG_GLOBAL"] == "/dev/null" and env["GIT_TERMINAL_PROMPT"] == "0"
    named = plugin_sources.Remote("http://h:1/x", "h:1:127.0.0.1", named=True)
    assert (
        plugin_sources._environment("/tmp/h", named)["GIT_ALLOW_PROTOCOL"]
        == "https:http"
    )


async def test_a_ref_names_its_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    tag, commit, main = "a" * 40, "b" * 40, "c" * 40
    listing = (
        f"{main}\tHEAD\n{main}\trefs/heads/main\n"
        f"{tag}\trefs/tags/v1\n{commit}\trefs/tags/v1^{{}}\n"
    )

    async def fake_git(args: list[str], home: str, remote: object = None) -> str:
        return listing

    monkeypatch.setattr(plugin_sources, "git", fake_git)
    repo = plugin_sources.Remote("https://x.example/r.git", None, named=False)
    assert await plugin_sources.head_of(repo, None, "/tmp") == main
    assert await plugin_sources.head_of(repo, "main", "/tmp") == main
    # An annotated tag: the commit it points to, which a checkout gets
    assert await plugin_sources.head_of(repo, "v1", "/tmp") == commit
    with pytest.raises(SourceError, match="isn't in that repository"):
        await plugin_sources.head_of(repo, "v2", "/tmp")
