#!/usr/bin/env python3
"""Install the built wheel in isolation and verify packaged runtime assets."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import venv
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSET = "avatar.png"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    wheels = sorted((ROOT / "dist").glob("muc_banbot-*.whl"))
    if len(wheels) != 1:
        print(f"Expected exactly one built muc-banbot wheel, found {len(wheels)}", file=sys.stderr)
        return 1
    wheel = wheels[0]

    source_asset = ROOT / "banbot" / "bundled" / ASSET
    expected = _digest(source_asset)
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        member = f"banbot/bundled/{ASSET}"
        if member not in names:
            print(f"Wheel is missing packaged asset: {member}", file=sys.stderr)
            return 1
        actual = hashlib.sha256(archive.read(member)).hexdigest()
        if actual != expected:
            print("Wheel avatar differs from canonical bundled source", file=sys.stderr)
            return 1

        if "config_sample.py" not in names:
            print("Wheel is missing operator sample: config_sample.py", file=sys.stderr)
            return 1

        entry_points = next((name for name in names if name.endswith(".dist-info/entry_points.txt")), None)
        if entry_points is None:
            print("Wheel is missing entry_points.txt", file=sys.stderr)
            return 1
        entry_text = archive.read(entry_points).decode("utf-8")
        if "muc_banbot = banbot.bot:main" not in entry_text:
            print("Wheel is missing the muc_banbot console entry point", file=sys.stderr)
            return 1

    with tempfile.TemporaryDirectory(prefix="muc-banbot-wheel-") as temp_name:
        temp = Path(temp_name)
        env_dir = temp / "venv"
        venv.EnvBuilder(with_pip=True, system_site_packages=True).create(env_dir)
        python = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-deps",
                "--force-reinstall",
                str(wheel),
            ],
            cwd=temp,
            check=True,
        )
        code = """
from banbot.bundled_assets import bundled_asset

path = bundled_asset("avatar.png")
assert path.is_file(), path
assert "banbot/bundled/avatar.png" in path.as_posix(), path
print("Wheel asset smoke test passed.")
"""
        subprocess.run([str(python), "-c", code], cwd=temp, check=True)

    print(f"Wheel smoke test passed: {wheel.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
