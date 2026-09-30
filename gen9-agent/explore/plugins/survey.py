"""Loads every plugin kept inside a marketplace repository with Gen9's loader (plugins.py), and
tallies what loaded and why the rest didn't (NOTES.md).

    uv run python explore/plugins/survey.py <marketplace repository> [...]

Each repository is a clone of a marketplace, in Codex's format (`.agents/plugins/marketplace.json`)
or Claude Code's (`.claude-plugin/marketplace.json`). Plugins from other sources (other
repositories, npm) are counted, not fetched.
"""

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

from gen9_agent import plugins

MARKETPLACES = (".agents/plugins/marketplace.json", ".claude-plugin/marketplace.json")


def _local(repo: Path, source: object) -> Path | None:
    if isinstance(source, str) and source.startswith("./"):
        return repo / source
    if isinstance(source, dict) and source.get("source") == "local":
        return repo / source["path"]
    return None


def _survey(repo: Path) -> dict:
    index = next(repo / m for m in MARKETPLACES if (repo / m).is_file())
    entries = json.loads(index.read_text())["plugins"]
    tally: Counter[str] = Counter()
    formats: Counter[str] = Counter()
    rejected: Counter[str] = Counter()
    skipped: Counter[str] = Counter()
    notes: Counter[str] = Counter()
    for entry in entries:
        folder = _local(repo, entry["source"])
        if folder is None:
            source = entry["source"]
            tally[
                f"elsewhere ({source.get('source') if isinstance(source, dict) else source})"
            ] += 1
            continue
        loaded = plugins.load(folder, entry)
        tally["local"] += 1
        if loaded.rejected:
            rejected[loaded.rejected] += 1
            continue
        formats[loaded.format] += 1
        tally["skills"] += len(loaded.skills)
        tally["servers connected"] += sum(s.connects for s in loaded.mcp_servers)
        tally["servers not run"] += sum(not s.connects for s in loaded.mcp_servers)
        tally["with a skill or a connected server"] += bool(
            loaded.skills or any(s.connects for s in loaded.mcp_servers)
        )
        for s in loaded.skipped:
            skipped[s.why] += 1
        for n in loaded.notes:
            notes[n.split(": ", 1)[-1] if "#" in n else n] += 1
    return {
        "marketplace": str(index.relative_to(repo)),
        "entries": len(entries),
        "tally": dict(tally),
        "formats": dict(formats),
        "rejected": dict(rejected),
        "skipped": dict(skipped.most_common()),
        "notes": dict(notes.most_common(12)),
    }


async def main(repos: list[str]) -> None:
    for repo in repos:
        result = await asyncio.to_thread(_survey, Path(repo))
        print(json.dumps({"repository": Path(repo).name, **result}, indent=2))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
