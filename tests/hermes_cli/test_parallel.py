import json
import subprocess
from argparse import Namespace
from pathlib import Path

from hermes_cli.parallel import (
    AgentSpec,
    build_agent_prompt,
    build_child_command,
    detect_changed_file_overlaps,
    mission_id_for,
    parse_agent_spec,
    run_parallel_mission,
)
from hermes_cli.worktree import WorktreeInfo


def test_parse_agent_spec_with_role_prefix():
    spec = parse_agent_spec("impl::Implement auth fix")
    assert spec.role == "impl"
    assert spec.task == "Implement auth fix"


def test_parse_agent_spec_without_role_uses_agent_index():
    spec = parse_agent_spec("Review the diff", index=2)
    assert spec.role == "agent-2"
    assert spec.task == "Review the diff"


def test_agent_prompt_contains_shared_boundaries_and_output_contract():
    spec = AgentSpec(role="impl", task="Implement fix")
    prompt = build_agent_prompt(
        mission_name="mission-control-fix",
        shared_goal="Fix Mission Control polling",
        spec=spec,
        verification_command="python -m pytest tests/foo.py -q",
        worktree_path="/tmp/repo/.worktrees/hermes-parallel-impl-abc123",
        repo_root="/tmp/repo",
    )

    assert "mission-control-fix" in prompt
    assert "Role: impl" in prompt
    assert "Implement fix" in prompt
    assert "python -m pytest tests/foo.py -q" in prompt
    assert "/tmp/repo/.worktrees/hermes-parallel-impl-abc123" in prompt
    assert "Do not edit files outside your assigned scope" in prompt
    assert "Final response contract" in prompt


def test_mission_id_for_is_stable_shape():
    mission_id = mission_id_for("Mission Control Fix", now="20260502-012245")
    assert mission_id == "20260502-012245-mission-control-fix"


def test_build_child_command_uses_chat_query_source_and_quiet():
    command = build_child_command(
        prompt="Do work",
        model="gpt-5.3-codex",
        provider="openai-codex",
        toolsets="terminal,file",
        skills=["test-driven-development"],
        pass_session_id=True,
    )

    assert command[:3] == ["hermes", "chat", "-q"]
    assert "Do work" in command
    assert "--source" in command
    assert "parallel" in command
    assert "-Q" in command
    assert "--model" in command
    assert "gpt-5.3-codex" in command
    assert "--provider" in command
    assert "openai-codex" in command
    assert "--toolsets" in command
    assert "terminal,file" in command
    assert command.count("--skills") == 1
    assert "test-driven-development" in command
    assert "--pass-session-id" in command


def test_detect_changed_file_overlaps_reports_shared_files():
    changes = {
        "impl": ["src/a.py", "src/shared.py"],
        "review": ["tests/a_test.py", "src/shared.py"],
        "docs": ["README.md"],
    }

    overlaps = detect_changed_file_overlaps(changes)

    assert overlaps == {"src/shared.py": ["impl", "review"]}


def test_parallel_command_help_available():
    result = subprocess.run(
        ["python", "-m", "hermes_cli.main", "parallel", "--help"],
        cwd="/Users/liebe/.hermes/hermes-agent",
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0
    assert "parallel" in result.stdout.lower()
    assert "run" in result.stdout.lower()


class FakeProcess:
    def __init__(self, code=0):
        self.pid = 12345
        self._code = code
        self.terminated = False

    def wait(self):
        return self._code

    def terminate(self):
        self.terminated = True


def test_run_parallel_mission_writes_manifest_without_real_children(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("# Repo\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)

    hermes_home = tmp_path / "hermes-home"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.chdir(repo)
    monkeypatch.setattr("hermes_cli.parallel._spawn_child_process", lambda *args, **kwargs: FakeProcess(0))
    monkeypatch.setattr("hermes_cli.parallel.run_verification", lambda *args, **kwargs: 0)
    monkeypatch.setattr("hermes_cli.parallel.mission_id_for", lambda name: "20260502-012245-test")

    args = Namespace(
        agent=["impl::Implement", "review::Review"],
        name="Test",
        goal="Shared goal",
        verify="python -m pytest -q",
        repo=str(repo),
        no_worktrees=False,
        model=None,
        provider=None,
        toolsets="terminal,file",
        skills=[],
    )

    code = run_parallel_mission(args)

    assert code == 0
    manifest = hermes_home / "parallel" / "20260502-012245-test" / "manifest.json"
    assert manifest.exists()
    payload = json.loads(manifest.read_text())
    assert [agent["role"] for agent in payload["agents"]] == ["impl", "review"]


def _base_args(repo: Path, *, no_worktrees: bool = False) -> Namespace:
    return Namespace(
        agent=["impl::Implement", "review::Review"],
        name="Test",
        goal="Shared goal",
        verify="python -m pytest -q",
        repo=str(repo),
        no_worktrees=no_worktrees,
        model=None,
        provider=None,
        toolsets="terminal,file",
        skills=[],
    )


def test_no_worktrees_uses_requested_repo_as_child_cwd(tmp_path, monkeypatch):
    repo = tmp_path / "plain-repo"
    repo.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    monkeypatch.setattr("hermes_cli.parallel._spawn_child_process", lambda run, **kwargs: FakeProcess(0))
    monkeypatch.setattr("hermes_cli.parallel.run_verification", lambda *args, **kwargs: 0)
    monkeypatch.setattr("hermes_cli.parallel.mission_id_for", lambda name: "20260502-012245-test")

    captured_cwds = []

    def fake_spawn(run, **kwargs):
        captured_cwds.append(run.cwd)
        return FakeProcess(0)

    monkeypatch.setattr("hermes_cli.parallel._spawn_child_process", fake_spawn)

    code = run_parallel_mission(_base_args(repo, no_worktrees=True))

    assert code == 0
    assert captured_cwds == [repo.resolve(), repo.resolve()]


def test_run_parallel_mission_cleans_clean_worktrees(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    monkeypatch.setattr("hermes_cli.parallel.git_repo_root", lambda cwd: str(repo))
    monkeypatch.setattr("hermes_cli.parallel._spawn_child_process", lambda *args, **kwargs: FakeProcess(0))
    monkeypatch.setattr("hermes_cli.parallel.run_verification", lambda *args, **kwargs: 0)
    monkeypatch.setattr("hermes_cli.parallel.mission_id_for", lambda name: "20260502-012245-test")

    created = []

    def fake_setup(repo_root, *, prefix, branch_prefix):
        role = prefix.rsplit("-", 1)[-1]
        info = WorktreeInfo(path=str(tmp_path / f"wt-{role}"), branch=f"{branch_prefix}/{role}", repo_root=str(repo))
        created.append(info)
        return info

    cleaned = []
    monkeypatch.setattr("hermes_cli.parallel.setup_worktree", fake_setup)
    monkeypatch.setattr("hermes_cli.parallel.cleanup_worktree", lambda info: cleaned.append(info) or True)

    code = run_parallel_mission(_base_args(repo))

    assert code == 0
    assert cleaned == created


def test_setup_failure_rolls_back_created_worktrees(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    monkeypatch.setattr("hermes_cli.parallel.git_repo_root", lambda cwd: str(repo))
    monkeypatch.setattr("hermes_cli.parallel.mission_id_for", lambda name: "20260502-012245-test")

    first = WorktreeInfo(path=str(tmp_path / "wt-impl"), branch="hermes/impl", repo_root=str(repo))
    calls = [first, None]
    cleaned = []
    monkeypatch.setattr("hermes_cli.parallel.setup_worktree", lambda *args, **kwargs: calls.pop(0))
    monkeypatch.setattr("hermes_cli.parallel.cleanup_worktree", lambda info: cleaned.append(info) or True)

    code = run_parallel_mission(_base_args(repo))

    assert code == 1
    assert cleaned == [first]


def test_worktree_with_changes_is_preserved(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    monkeypatch.setattr("hermes_cli.parallel.git_repo_root", lambda cwd: str(repo))
    monkeypatch.setattr("hermes_cli.parallel._spawn_child_process", lambda *args, **kwargs: FakeProcess(0))
    monkeypatch.setattr("hermes_cli.parallel.run_verification", lambda *args, **kwargs: 0)
    monkeypatch.setattr("hermes_cli.parallel.mission_id_for", lambda name: "20260502-012245-test")

    impl = WorktreeInfo(path=str(tmp_path / "wt-impl"), branch="hermes/impl", repo_root=str(repo))
    review = WorktreeInfo(path=str(tmp_path / "wt-review"), branch="hermes/review", repo_root=str(repo))
    calls = [impl, review]
    cleaned = []
    monkeypatch.setattr("hermes_cli.parallel.setup_worktree", lambda *args, **kwargs: calls.pop(0))
    monkeypatch.setattr(
        "hermes_cli.parallel.build_changed_file_summary",
        lambda runs: {"by_role": {"impl": ["src/app.py"], "review": []}, "overlaps": {}},
    )
    monkeypatch.setattr("hermes_cli.parallel.cleanup_worktree", lambda info: cleaned.append(info) or True)

    code = run_parallel_mission(_base_args(repo))

    assert code == 0
    assert cleaned == [review]


def test_spawn_failure_cleans_worktrees_and_terminates_started_process(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    monkeypatch.setattr("hermes_cli.parallel.git_repo_root", lambda cwd: str(repo))
    monkeypatch.setattr("hermes_cli.parallel.mission_id_for", lambda name: "20260502-012245-test")

    impl = WorktreeInfo(path=str(tmp_path / "wt-impl"), branch="hermes/impl", repo_root=str(repo))
    review = WorktreeInfo(path=str(tmp_path / "wt-review"), branch="hermes/review", repo_root=str(repo))
    setup_calls = [impl, review]
    first_process = FakeProcess(0)
    spawn_count = {"value": 0}
    cleaned = []

    def fake_spawn(*args, **kwargs):
        spawn_count["value"] += 1
        if spawn_count["value"] == 2:
            raise FileNotFoundError("missing hermes")
        return first_process

    monkeypatch.setattr("hermes_cli.parallel.setup_worktree", lambda *args, **kwargs: setup_calls.pop(0))
    monkeypatch.setattr("hermes_cli.parallel._spawn_child_process", fake_spawn)
    monkeypatch.setattr("hermes_cli.parallel.cleanup_worktree", lambda info: cleaned.append(info) or True)

    code = run_parallel_mission(_base_args(repo))

    assert code == 1
    assert first_process.terminated is True
    assert cleaned == [impl, review]
