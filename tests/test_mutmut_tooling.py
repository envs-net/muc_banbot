import json
import os
import shutil
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _install_fake_python(fake_bin: Path) -> None:
    fake_python = fake_bin / "python"
    fake_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_python.chmod(0o755)


def _copy_wrapper(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    wrapper = scripts / "mutmut.sh"
    shutil.copy2(ROOT / "scripts/mutmut.sh", wrapper)
    wrapper.chmod(0o755)
    return wrapper


def test_mutmut_wrapper_unsets_pythonpath_before_exec(tmp_path):
    wrapper = _copy_wrapper(tmp_path)
    repo = wrapper.parents[1]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _install_fake_python(fake_bin)
    output = tmp_path / "output.txt"
    fake_mutmut = fake_bin / "mutmut"
    fake_mutmut.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"${{PYTHONPATH-unset}}|$*\" > {output}\n",
        encoding="utf-8",
    )
    fake_mutmut.chmod(0o755)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    subprocess.run([str(wrapper), "run"], check=True, cwd=repo, env=env)

    assert output.read_text(encoding="utf-8").strip() == "unset|run"


def test_mutmut_wrapper_fresh_removes_cached_tree(tmp_path):
    wrapper = _copy_wrapper(tmp_path)
    repo = wrapper.parents[1]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _install_fake_python(fake_bin)
    output = tmp_path / "output.txt"
    fake_mutmut = fake_bin / "mutmut"
    fake_mutmut.write_text(
        "#!/bin/sh\n"
        f"printf '%s' \"$*\" > {output}\n",
        encoding="utf-8",
    )
    fake_mutmut.chmod(0o755)

    mutants = repo / "mutants"
    mutants.mkdir()
    (mutants / "stale").write_text("stale", encoding="utf-8")

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    subprocess.run([str(wrapper), "fresh"], check=True, cwd=repo, env=env)

    assert not mutants.exists()
    assert output.read_text(encoding="utf-8") == "run"


def test_mutmut_version_pin_matches_regression_baseline():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dev = tuple(pyproject["project"]["optional-dependencies"]["dev"])
    pin = next(requirement for requirement in dev if requirement.startswith("mutmut=="))
    baseline = json.loads((ROOT / "tests/regression-baseline.json").read_text(encoding="utf-8"))

    assert pin == "mutmut==3.6.0"
    assert baseline["mutation"]["mutmut_version"] == pin.removeprefix("mutmut==")
    assert (ROOT / "scripts/mutmut.sh").read_text(encoding="utf-8").count("mutation-tool-check") == 2


def test_mutmut_wrapper_dispatches_regression_commands(tmp_path):
    wrapper = _copy_wrapper(tmp_path)
    repo = wrapper.parents[1]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    output = tmp_path / "python-args.txt"
    fake_python = fake_bin / "python"
    fake_python.write_text(
        "#!/bin/sh\n"
        f"printf '%s' \"$*\" > {output}\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    for command, expected in (
        ("check", "-m envs_xmpp_ops.regression mutation-check"),
        ("accept", "-m envs_xmpp_ops.regression mutation-accept"),
    ):
        subprocess.run([str(wrapper), command], check=True, cwd=repo, env=env)
        assert output.read_text(encoding="utf-8") == expected
