"""Managed runtime-file helper tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from banbot.managed_files import (
    format_file_size,
    is_relative_to,
    list_managed_files,
    prune_managed_files,
    resolve_managed_file,
)


def test_managed_files_list_resolve_and_size_format(tmp_path: Path):
    managed_dir = tmp_path / "managed"
    managed_dir.mkdir()
    first = managed_dir / "bans_export_1.csv"
    second = managed_dir / "bans_export_2.csv"
    ignored = managed_dir / "other.txt"
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")
    ignored.write_text("x", encoding="utf-8")

    files = list_managed_files(managed_dir, "bans_export_*.csv")

    assert {item.name for item in files} == {"bans_export_1.csv", "bans_export_2.csv"}
    assert resolve_managed_file(managed_dir, "bans_export_1.csv", files) == first
    assert resolve_managed_file(managed_dir, str(second), files) == second
    assert resolve_managed_file(managed_dir, "../other.txt", files) is None
    assert is_relative_to(first.resolve(), managed_dir.resolve()) is True
    assert format_file_size(42) == "42 B"
    assert format_file_size(2048) == "2.0 KiB"


@pytest.mark.asyncio
async def test_prune_managed_files_keeps_newest_and_preserves(tmp_path: Path):
    managed_dir = tmp_path / "managed"
    managed_dir.mkdir()
    paths = []
    for index in range(4):
        path = managed_dir / f"item-{index}.dat"
        path.write_text(str(index), encoding="utf-8")
        path.touch()
        paths.append(path)

    files = list_managed_files(managed_dir, "item-*.dat")
    removed = await prune_managed_files(files, keep=2, preserve=paths[0])

    assert paths[0].exists()
    assert len(removed) == 1
    assert len(list(managed_dir.glob("item-*.dat"))) == 3
