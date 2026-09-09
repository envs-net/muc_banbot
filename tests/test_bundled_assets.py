from pathlib import Path

from banbot import bundled_assets


def test_resolve_bundled_asset_prefers_working_directory_copy(monkeypatch, tmp_path):
    working = tmp_path / "working"
    packaged = tmp_path / "packaged"
    working.mkdir()
    packaged.mkdir()
    (working / "avatar.png").write_bytes(b"working")
    (packaged / "avatar.png").write_bytes(b"packaged")

    monkeypatch.chdir(working)
    monkeypatch.setattr(bundled_assets, "_BUNDLED_DIR", packaged)

    assert bundled_assets.resolve_bundled_asset("avatar.png") == Path("avatar.png")


def test_resolve_bundled_asset_falls_back_to_packaged_plain_filename(monkeypatch, tmp_path):
    working = tmp_path / "working"
    packaged = tmp_path / "packaged"
    working.mkdir()
    packaged.mkdir()
    packaged_avatar = packaged / "avatar.png"
    packaged_avatar.write_bytes(b"packaged")

    monkeypatch.chdir(working)
    monkeypatch.setattr(bundled_assets, "_BUNDLED_DIR", packaged)

    assert bundled_assets.resolve_bundled_asset("avatar.png") == packaged_avatar.resolve()


def test_resolve_bundled_asset_does_not_rewrite_operator_subpaths(monkeypatch, tmp_path):
    working = tmp_path / "working"
    packaged = tmp_path / "packaged"
    working.mkdir()
    packaged.mkdir()
    (packaged / "avatar.png").write_bytes(b"packaged")

    monkeypatch.chdir(working)
    monkeypatch.setattr(bundled_assets, "_BUNDLED_DIR", packaged)

    assert bundled_assets.resolve_bundled_asset("data/avatar.png") == Path("data/avatar.png")
