from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "hermes-custom-update.sh"
HELPER = ROOT / "scripts" / "hermes_custom_update_state.py"


def _run(cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    full_env = os.environ.copy()
    full_env.update(
        {
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "TZ": "UTC",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        }
    )
    if env:
        full_env.update(env)
    result = subprocess.run(
        cmd,
        cwd=cwd,
        env=full_env,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"command failed: {cmd}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _init_topology(tmp_path: Path) -> dict[str, Path]:
    remotes = tmp_path / "remotes"
    origin_bare = remotes / "mir-and-liebe" / "hermes-agent.git"
    upstream_bare = remotes / "NousResearch" / "hermes-agent.git"
    seed = tmp_path / "seed"
    work = tmp_path / "work"
    origin_bare.parent.mkdir(parents=True)
    upstream_bare.parent.mkdir(parents=True)

    _run(["git", "init", "--bare", str(origin_bare)], cwd=tmp_path)
    _run(["git", "init", "--bare", str(upstream_bare)], cwd=tmp_path)
    _run(["git", "init", str(seed)], cwd=tmp_path)
    _run(["git", "checkout", "-b", "main"], cwd=seed)
    _write(seed / "README.md", "initial\n")
    _run(["git", "add", "README.md"], cwd=seed)
    _run(["git", "commit", "-m", "init"], cwd=seed)
    _run(["git", "remote", "add", "origin", str(origin_bare)], cwd=seed)
    _run(["git", "remote", "add", "upstream", str(upstream_bare)], cwd=seed)
    _run(["git", "push", "origin", "main"], cwd=seed)
    _run(["git", "push", "upstream", "main"], cwd=seed)

    _run(["git", "clone", str(origin_bare), str(work)], cwd=tmp_path)
    _run(["git", "checkout", "-B", "main", "origin/main"], cwd=work)
    _run(["git", "remote", "add", "upstream", str(upstream_bare)], cwd=work)

    scripts_dir = work / "scripts"
    scripts_dir.mkdir()
    shutil.copy2(SCRIPT, scripts_dir / "hermes-custom-update.sh")
    shutil.copy2(HELPER, scripts_dir / "hermes_custom_update_state.py")
    _run(["git", "add", "scripts"], cwd=work)
    _run(["git", "commit", "-m", "add update scripts"], cwd=work)

    return {
        "origin_bare": origin_bare,
        "upstream_bare": upstream_bare,
        "work": work,
        "script": scripts_dir / "hermes-custom-update.sh",
    }


def _commit_upstream_change(tmp_path: Path, upstream_bare: Path) -> str:
    clone = tmp_path / "upstream-work"
    _run(["git", "clone", str(upstream_bare), str(clone)], cwd=tmp_path)
    _run(["git", "checkout", "-B", "main", "origin/main"], cwd=clone)
    _write(clone / "UPSTREAM.md", "new upstream change\n")
    _run(["git", "add", "UPSTREAM.md"], cwd=clone)
    _run(["git", "commit", "-m", "upstream change"], cwd=clone)
    _run(["git", "push", "origin", "main"], cwd=clone)
    return _run(["git", "rev-parse", "HEAD"], cwd=clone).stdout.strip()


def _read_state(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_custom_update_script_writes_noop_success_state(tmp_path):
    topology = _init_topology(tmp_path)
    state_path = tmp_path / "home" / ".hermes" / "ops" / "state" / "hermes-custom-update.json"

    result = _run(
        ["bash", str(topology["script"]), "--no-push", "--skip-verify"],
        cwd=topology["work"],
        env={"HOME": str(tmp_path / "home"), "HERMES_CUSTOM_UPDATE_STATE": str(state_path)},
    )

    assert result.returncode == 0
    state = _read_state(state_path)
    assert state["status"] == "success"
    assert state["changed"] is False
    assert state["pushed"] is False
    assert state["verified"] is False
    assert state["before_head"] == state["after_head"]
    assert state["local_vs_upstream_left_right_count"]
    assert state["origin_vs_upstream_left_right_count"]
    assert state["branch"] == "main"


def test_custom_update_script_writes_changed_success_state(tmp_path):
    topology = _init_topology(tmp_path)
    state_path = tmp_path / "home" / ".hermes" / "ops" / "state" / "hermes-custom-update.json"
    upstream_head = _commit_upstream_change(tmp_path, topology["upstream_bare"])

    result = _run(
        ["bash", str(topology["script"]), "--no-push"],
        cwd=topology["work"],
        env={
            "HOME": str(tmp_path / "home"),
            "HERMES_CUSTOM_UPDATE_STATE": str(state_path),
            "HERMES_CUSTOM_VERIFY": "true",
        },
    )

    assert result.returncode == 0
    state = _read_state(state_path)
    assert state["status"] == "success"
    assert state["changed"] is True
    assert state["verified"] is True
    assert state["pushed"] is False
    assert state["before_head"] != state["after_head"]
    assert state["upstream_main"] == upstream_head
    counts = state["local_vs_upstream_left_right_count"].split()
    assert len(counts) == 2
    assert int(counts[0]) >= 1
    assert counts[1] == "0"


def test_custom_update_script_rejects_expected_slug_on_wrong_https_host(tmp_path):
    topology = _init_topology(tmp_path)
    state_path = tmp_path / "home" / ".hermes" / "ops" / "state" / "hermes-custom-update.json"
    _run(
        [
            "git",
            "remote",
            "set-url",
            "origin",
            "https://evil.example/mir-and-liebe/hermes-agent.git",
        ],
        cwd=topology["work"],
    )

    result = _run(
        ["bash", str(topology["script"]), "--no-push", "--skip-verify"],
        cwd=topology["work"],
        env={"HOME": str(tmp_path / "home"), "HERMES_CUSTOM_UPDATE_STATE": str(state_path)},
        check=False,
    )

    assert result.returncode != 0
    state = _read_state(state_path)
    assert state["status"] == "failed"
    assert state["failed_step"] == "validate_origin_remote"
    assert "evil.example" not in state_path.read_text(encoding="utf-8")


def test_custom_update_script_writes_failed_state_for_bad_remote(tmp_path):
    topology = _init_topology(tmp_path)
    state_path = tmp_path / "home" / ".hermes" / "ops" / "state" / "hermes-custom-update.json"
    _run(["git", "remote", "set-url", "upstream", str(tmp_path / "wrong" / "repo.git")], cwd=topology["work"])

    result = _run(
        ["bash", str(topology["script"]), "--no-push", "--skip-verify"],
        cwd=topology["work"],
        env={"HOME": str(tmp_path / "home"), "HERMES_CUSTOM_UPDATE_STATE": str(state_path)},
        check=False,
    )

    assert result.returncode != 0
    state = _read_state(state_path)
    assert state["status"] == "failed"
    assert state["exit_code"] == result.returncode
    assert state["failed_step"] == "validate_upstream_remote"
    raw = state_path.read_text(encoding="utf-8")
    assert "://" not in raw
    assert "stderr" not in raw
