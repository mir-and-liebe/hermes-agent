"""Coordinate multiple Hermes child sessions for parallel missions."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Sequence

from hermes_cli.config import get_hermes_home
from hermes_cli.worktree import (
    WorktreeInfo,
    changed_files_between_ref,
    cleanup_worktree,
    git_repo_root,
    setup_worktree,
)


@dataclass(frozen=True)
class AgentSpec:
    role: str
    task: str


@dataclass(frozen=True)
class AgentRun:
    spec: AgentSpec
    worktree: WorktreeInfo | None
    command: list[str]
    log_path: Path
    cwd: Path


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower()).strip("-")
    return slug[:64] or "parallel-mission"


def parse_agent_spec(raw: str, index: int = 1) -> AgentSpec:
    if "::" in raw:
        role, task = raw.split("::", 1)
        role_slug = slugify(role)
        task_text = task.strip()
    else:
        role_slug = f"agent-{index}"
        task_text = raw.strip()
    if not task_text:
        raise ValueError("Agent task cannot be empty")
    return AgentSpec(role=role_slug, task=task_text)


def build_agent_prompt(
    *,
    mission_name: str,
    shared_goal: str,
    spec: AgentSpec,
    verification_command: str | None,
    worktree_path: str,
    repo_root: str,
) -> str:
    verify_line = verification_command or "Run the smallest relevant verification command for your task and report it."
    return f"""You are one Hermes worker in a coordinated parallel mission.

Mission: {mission_name}
Shared goal: {shared_goal}
Role: {spec.role}
Your task: {spec.task}

Workspace:
- Original repo: {repo_root}
- Your isolated worktree: {worktree_path}

Coordination rules:
- Work only inside your isolated worktree.
- Do not edit files outside your assigned scope unless your task explicitly requires it.
- If your task needs a file owned by another role, stop and report the conflict instead of silently editing it.
- Prefer small focused commits if you change code.
- Never hardcode secrets.
- Use TDD for code changes: write or update a failing test first, implement, then verify.

Verification:
- Required verification command: {verify_line}
- If this command is not applicable to your role, explain why and run the nearest targeted check.

Final response contract:
Return exactly these sections:
1. Summary
2. Files changed
3. Verification run
4. Blockers or conflicts
5. Next handoff
""".strip()


def mission_id_for(name: str, now: str | None = None) -> str:
    stamp = now or time.strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{slugify(name)}"


def mission_dir_for(mission_id: str) -> Path:
    return get_hermes_home() / "parallel" / mission_id


def build_child_command(
    *,
    prompt: str,
    model: str | None,
    provider: str | None,
    toolsets: str | None,
    skills: Sequence[str] | None,
    pass_session_id: bool,
) -> list[str]:
    hermes_bin = os.getenv("HERMES_PARALLEL_CHILD_BIN", "hermes")
    command = [hermes_bin, "chat", "-q", prompt, "--source", "parallel", "-Q"]
    if model:
        command.extend(["--model", model])
    if provider:
        command.extend(["--provider", provider])
    if toolsets:
        command.extend(["--toolsets", toolsets])
    for skill in skills or []:
        command.extend(["--skills", skill])
    if pass_session_id:
        command.append("--pass-session-id")
    return command


def write_manifest(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def detect_changed_file_overlaps(changes_by_role: dict[str, list[str]]) -> dict[str, list[str]]:
    owners: dict[str, list[str]] = {}
    for role, files in changes_by_role.items():
        for file_path in files:
            owners.setdefault(file_path, []).append(role)
    return {file_path: roles for file_path, roles in sorted(owners.items()) if len(roles) > 1}


def build_changed_file_summary(runs: Sequence[AgentRun]) -> dict[str, object]:
    changes_by_role: dict[str, list[str]] = {}
    for run in runs:
        if run.worktree:
            ref = run.worktree.base_commit or "HEAD"
            changes_by_role[run.spec.role] = changed_files_between_ref(run.worktree.path, ref=ref)
        else:
            changes_by_role[run.spec.role] = []
    return {
        "by_role": changes_by_role,
        "overlaps": detect_changed_file_overlaps(changes_by_role),
    }


def print_changed_file_summary(summary: dict[str, object]) -> None:
    print("Changed files:")
    by_role = summary.get("by_role", {})
    if isinstance(by_role, dict):
        for role, files in by_role.items():
            if isinstance(files, list) and files:
                print(f"  {role}: {', '.join(str(file_path) for file_path in files)}")
            else:
                print(f"  {role}: no changes detected")
    overlaps = summary.get("overlaps", {})
    if isinstance(overlaps, dict) and overlaps:
        print("Potential conflicts:")
        for file_path, roles in overlaps.items():
            if isinstance(roles, list):
                print(f"  {file_path}: {', '.join(str(role) for role in roles)}")
    else:
        print("Potential conflicts: none")


def run_verification(command: str, *, cwd: str | Path, mission_dir: Path) -> int:
    log_path = mission_dir / "verify.log"
    print(f"Running verification: {command}")
    shell_command = ["cmd", "/c", command] if os.name == "nt" else ["/bin/sh", "-lc", command]
    with log_path.open("w", encoding="utf-8") as log_file:
        proc = subprocess.run(
            shell_command,
            cwd=str(cwd),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
    print(f"Verification exit={proc.returncode} log={log_path}")
    return int(proc.returncode)


def _mission_agent_payload(run: AgentRun) -> dict[str, object]:
    return {
        "role": run.spec.role,
        "task": run.spec.task,
        "worktree": run.worktree.to_dict() if run.worktree else None,
        "log_path": str(run.log_path),
        "cwd": str(run.cwd),
        "command": run.command,
    }


def _spawn_child_process(run: AgentRun, *, env: dict[str, str], log_file: object) -> object:
    return subprocess.Popen(
        run.command,
        cwd=str(run.cwd),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _terminate_started_processes(processes: Sequence[tuple[AgentRun, object, object]]) -> None:
    for _run, proc, log_file in processes:
        try:
            terminate = getattr(proc, "terminate", None)
            if callable(terminate):
                terminate()
            wait = getattr(proc, "wait", None)
            if callable(wait):
                wait()
        except Exception:
            pass
        finally:
            try:
                log_file.close()
            except Exception:
                pass


def cleanup_parallel_worktrees(
    runs: Sequence[AgentRun],
    summary: dict[str, object],
    *,
    print_status: bool = True,
) -> dict[str, str]:
    """Clean up per-agent worktrees that have no changed files.

    Worktrees with tracked, untracked, or committed changes are intentionally
    preserved so agents never lose handoff artifacts.
    """
    by_role = summary.get("by_role", {})
    changes_by_role = by_role if isinstance(by_role, dict) else {}
    cleanup_status: dict[str, str] = {}
    for run in runs:
        if not run.worktree:
            cleanup_status[run.spec.role] = "skipped"
            continue
        role_changes = changes_by_role.get(run.spec.role, [])
        if isinstance(role_changes, list) and role_changes:
            cleanup_status[run.spec.role] = "preserved-changes"
            if print_status:
                print(f"Preserved {run.spec.role} worktree with changes: {run.worktree.path}")
            continue
        removed = cleanup_worktree(run.worktree)
        cleanup_status[run.spec.role] = "removed" if removed else "preserved"
        if print_status:
            verb = "Removed" if removed else "Preserved"
            print(f"{verb} {run.spec.role} worktree: {run.worktree.path}")
    return cleanup_status


def _cleanup_runs_after_failure(runs: Sequence[AgentRun]) -> None:
    summary = {"by_role": {run.spec.role: [] for run in runs}}
    cleanup_parallel_worktrees(runs, summary)


def run_parallel_mission(args: object) -> int:
    raw_agents = getattr(args, "agent", None) or []
    try:
        specs = [parse_agent_spec(raw, index=index + 1) for index, raw in enumerate(raw_agents)]
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if len(specs) < 2:
        print("Error: hermes parallel run requires at least two --agent entries", file=sys.stderr)
        return 2

    requested_repo = getattr(args, "repo", None)
    repo_root = git_repo_root(requested_repo or Path.cwd())
    no_worktrees = bool(getattr(args, "no_worktrees", False))
    if not repo_root and not no_worktrees:
        print("Error: hermes parallel run requires a git repo unless --no-worktrees is set", file=sys.stderr)
        return 2
    execution_root = Path(repo_root or requested_repo or Path.cwd()).resolve()
    repo_label = str(Path(repo_root).resolve()) if repo_root else str(execution_root)

    mission_name = getattr(args, "name", None) or "parallel-mission"
    mission_id = mission_id_for(mission_name)
    mission_dir = mission_dir_for(mission_id)
    mission_dir.mkdir(parents=True, exist_ok=True)

    runs: list[AgentRun] = []
    for spec in specs:
        worktree = None
        worktree_path = str(execution_root)
        run_cwd = execution_root
        if not no_worktrees:
            worktree = setup_worktree(
                repo_root,
                prefix=f"hermes-parallel-{slugify(mission_name)}-{spec.role}",
                branch_prefix=f"hermes/parallel/{slugify(mission_name)}",
            )
            if not worktree:
                print(f"Error: failed to create worktree for {spec.role}", file=sys.stderr)
                _cleanup_runs_after_failure(runs)
                return 1
            worktree_path = worktree.path
            run_cwd = Path(worktree.path)

        prompt = build_agent_prompt(
            mission_name=mission_name,
            shared_goal=getattr(args, "goal", None) or mission_name,
            spec=spec,
            verification_command=getattr(args, "verify", None),
            worktree_path=worktree_path,
            repo_root=repo_label,
        )
        log_path = mission_dir / f"{spec.role}.log"
        command = build_child_command(
            prompt=prompt,
            model=getattr(args, "model", None),
            provider=getattr(args, "provider", None),
            toolsets=getattr(args, "toolsets", None),
            skills=getattr(args, "skills", None) or [],
            pass_session_id=True,
        )
        runs.append(AgentRun(spec=spec, worktree=worktree, command=command, log_path=log_path, cwd=run_cwd))

    manifest = {
        "mission_id": mission_id,
        "name": mission_name,
        "goal": getattr(args, "goal", None) or mission_name,
        "repo_root": repo_label,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "agents": [_mission_agent_payload(run) for run in runs],
    }
    write_manifest(mission_dir / "manifest.json", manifest)

    print(f"Mission: {mission_id}")
    print(f"Mission dir: {mission_dir}")

    processes: list[tuple[AgentRun, object, object]] = []
    for run in runs:
        env = os.environ.copy()
        env["HERMES_PARALLEL_MISSION_ID"] = mission_id
        env["HERMES_PARALLEL_AGENT_ROLE"] = run.spec.role
        if run.worktree:
            env["TERMINAL_CWD"] = run.worktree.path
        log_file = run.log_path.open("w", encoding="utf-8")
        try:
            proc = _spawn_child_process(run, env=env, log_file=log_file)
        except OSError as exc:
            log_file.close()
            print(f"Error: failed to start {run.spec.role}: {exc}", file=sys.stderr)
            _terminate_started_processes(processes)
            _cleanup_runs_after_failure(runs)
            return 1
        processes.append((run, proc, log_file))
        print(f"Started {run.spec.role}: pid={proc.pid} log={run.log_path}")

    exit_codes: dict[str, int] = {}
    for run, proc, log_file in processes:
        code = proc.wait()
        log_file.close()
        exit_codes[run.spec.role] = int(code)
        print(f"Finished {run.spec.role}: exit={code}")

    summary = build_changed_file_summary(runs)
    cleanup_status = cleanup_parallel_worktrees(runs, summary)
    write_manifest(
        mission_dir / "summary.json",
        {"exit_codes": exit_codes, "changes": summary, "cleanup": cleanup_status},
    )
    print_changed_file_summary(summary)

    verify_command = getattr(args, "verify", None)
    verify_code = run_verification(verify_command, cwd=execution_root, mission_dir=mission_dir) if verify_command else 0
    return 0 if all(code == 0 for code in exit_codes.values()) and verify_code == 0 else 1


def _split_skills(raw_skills: Sequence[str] | None) -> list[str]:
    result: list[str] = []
    for value in raw_skills or []:
        result.extend(part.strip() for part in value.split(",") if part.strip())
    return result


def show_status(mission_id: str) -> int:
    mission_dir = mission_dir_for(mission_id)
    manifest_path = mission_dir / "manifest.json"
    summary_path = mission_dir / "summary.json"
    if not manifest_path.exists():
        print(f"Mission not found: {mission_id}", file=sys.stderr)
        return 1
    print(manifest_path.read_text(encoding="utf-8"))
    if summary_path.exists():
        print(summary_path.read_text(encoding="utf-8"))
    return 0


def parallel_command(args: object) -> int:
    action = getattr(args, "parallel_action", None)
    if action == "run":
        setattr(args, "skills", _split_skills(getattr(args, "skills", [])))
        return run_parallel_mission(args)
    if action == "status":
        return show_status(getattr(args, "mission_id"))
    print("Usage: hermes parallel run --agent role::task --agent role::task", file=sys.stderr)
    return 2
