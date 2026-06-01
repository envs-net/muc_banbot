"""Managed runtime-file helper tests."""

from __future__ import annotations

from pathlib import Path
import os

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


def test_managed_files_empty_and_rejected_paths(tmp_path: Path):
    managed_dir = tmp_path / "managed"

    assert list_managed_files(managed_dir, "*.dat") == []
    assert resolve_managed_file(managed_dir, "latest", []) is None
    assert resolve_managed_file(managed_dir, "", []) is None

    managed_dir.mkdir()
    inside = managed_dir / "item.dat"
    outside = tmp_path / "outside.dat"
    inside.write_text("inside", encoding="utf-8")
    outside.write_text("outside", encoding="utf-8")
    files = list_managed_files(managed_dir, "*.dat")

    assert resolve_managed_file(managed_dir, str(inside.resolve()), files) == inside
    assert resolve_managed_file(managed_dir, str(outside.resolve()), files) is None
    rejected = managed_dir / "item.tmp"
    rejected.write_text("rejected", encoding="utf-8")
    assert resolve_managed_file(
        managed_dir,
        "item.tmp",
        files,
        predicate=lambda path: False,
    ) is None


@pytest.mark.asyncio
async def test_prune_managed_files_deletes_companions(tmp_path: Path):
    managed_dir = tmp_path / "managed"
    managed_dir.mkdir()
    old = managed_dir / "item-old.dat"
    new = managed_dir / "item-new.dat"
    companion = managed_dir / "item-old.dat.config.py"
    old.write_text("old", encoding="utf-8")
    new.write_text("new", encoding="utf-8")
    companion.write_text("config", encoding="utf-8")
    os.utime(old, (100, 100))
    os.utime(companion, (100, 100))
    os.utime(new, (200, 200))

    files = list_managed_files(managed_dir, "item-*.dat")
    removed = await prune_managed_files(
        files,
        keep=1,
        delete_companions=lambda path: [path.with_name(path.name + ".config.py")],
    )

    assert old in removed
    assert companion in removed
    assert not old.exists()
    assert not companion.exists()
    assert new.exists()
