"""A chat's files in the terminal (gen9 files): how they're listed, and a download never writes
over a file that's there."""

import asyncio

import pytest

from gen9_cli.main import format_files, size_of, write_new

pytestmark = pytest.mark.asyncio


async def test_files_are_listed_with_their_size_and_how_to_get_one() -> None:
    files = [
        {"name": "report.csv", "size": 12_300},
        {"name": "charts/a.png", "size": 3_500_000},
    ]
    assert format_files(files, "t1") == (
        "Files:\n  report.csv (12 KB)\n  charts/a.png (3.3 MB)\n  gen9 files t1 NAME   downloads one"
    )
    assert format_files([], "t1") == "This chat has no files."
    assert size_of(512) == "512 B"


async def test_a_download_never_writes_over_a_file(tmp_path) -> None:
    target = tmp_path / "report.csv"
    await asyncio.to_thread(write_new, target, b"a,b\n")
    assert await asyncio.to_thread(target.read_bytes) == b"a,b\n"
    with pytest.raises(FileExistsError):
        await asyncio.to_thread(write_new, target, b"other")
    assert await asyncio.to_thread(target.read_bytes) == b"a,b\n"
