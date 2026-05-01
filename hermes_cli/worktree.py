"""Reusable git worktree helpers for Hermes CLI workflows."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorktreeInfo:
    """Metadata for an isolated git worktree."""

    path: str
    branch: str
    repo_root: str
    base_commit: str | None = None

    def to_dict(self) -> dict[str, str]:
        payload = {"path": self.path, "branch": self.branch, "repo_root": self.repo_root}
        if self.base_commit:
            payload["base_commit"] = self.base_commit
        return payload


def git_repo_root(cwd: str | Path | None = None) -> str | None:
    """Return the git repo root for ``cwd``, or None if not in a repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(cwd) if cwd else None,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        logger.debug("Could not resolve git repo root", exc_info=True)
    return None


def path_is_within_root(path: Path, root: Path) -> bool:
    """Return True when a resolved path stays within the expected root."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_slug(value: str, fallback: str) -> str:
    slug = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value).strip("-")
    return slug or fallback


def _safe_branch_prefix(value: str, fallback: str) -> str:
    parts = [_safe_slug(part, "") for part in value.strip("/").split("/")]
    cleaned = [part for part in parts if part]
    return "/".join(cleaned) or fallback


def _ensure_worktrees_gitignore(repo_root: Path) -> None:
    gitignore = repo_root / ".gitignore"
    ignore_entry = ".worktrees/"
    try:
        existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
        if ignore_entry not in existing.splitlines():
            with gitignore.open("a", encoding="utf-8") as handle:
                if existing and not existing.endswith("\n"):
                    handle.write("\n")
                handle.write(f"{ignore_entry}\n")
    except Exception as exc:
        logger.debug("Could not update .gitignore: %s", exc)


def _copy_worktreeinclude_entries(repo_root: Path, wt_path: Path) -> None:
    include_file = repo_root / ".worktreeinclude"
    if not include_file.exists():
        return

    try:
        repo_root_resolved = repo_root.resolve()
        wt_path_resolved = wt_path.resolve()
        for line in include_file.read_text(encoding="utf-8").splitlines():
            entry = line.strip()
            if not entry or entry.startswith("#"):
                continue

            src = repo_root / entry
            dst = wt_path / entry
            try:
                src_resolved = src.resolve(strict=False)
                dst_resolved = dst.resolve(strict=False)
            except (OSError, ValueError):
                logger.debug("Skipping invalid .worktreeinclude entry: %s", entry)
                continue

            if not path_is_within_root(src_resolved, repo_root_resolved):
                logger.warning("Skipping .worktreeinclude entry outside repo root: %s", entry)
                continue
            if not path_is_within_root(dst_resolved, wt_path_resolved):
                logger.warning("Skipping .worktreeinclude entry that escapes worktree: %s", entry)
                continue

            if src.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dst))
            elif src.is_dir() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(str(src_resolved), str(dst))
    except Exception as exc:
        logger.debug("Error copying .worktreeinclude entries: %s", exc)


def setup_worktree(
    repo_root: str | Path | None = None,
    *,
    prefix: str = "hermes",
    branch_prefix: str = "hermes",
) -> WorktreeInfo | None:
    """Create an isolated git worktree and return its metadata."""
    root = str(Path(repo_root).resolve()) if repo_root else git_repo_root()
    if not root:
        return None

    safe_prefix = _safe_slug(prefix, "hermes")
    safe_branch_prefix = _safe_branch_prefix(branch_prefix, "hermes")
    short_id = uuid.uuid4().hex[:8]
    wt_name = f"{safe_prefix}-{short_id}"
    branch_name = f"{safe_branch_prefix}/{wt_name}"

    try:
        base_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=root,
        )
        base_commit = base_result.stdout.strip() if base_result.returncode == 0 else None
    except Exception:
        base_commit = None

    worktrees_dir = Path(root) / ".worktrees"
    worktrees_dir.mkdir(parents=True, exist_ok=True)
    wt_path = worktrees_dir / wt_name

    _ensure_worktrees_gitignore(Path(root))

    try:
        result = subprocess.run(
            ["git", "worktree", "add", str(wt_path), "-b", branch_name, "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=root,
        )
    except Exception:
        logger.debug("Failed to create worktree", exc_info=True)
        return None

    if result.returncode != 0:
        logger.error("Failed to create worktree: %s", result.stderr.strip())
        return None

    _copy_worktreeinclude_entries(Path(root), wt_path)
    return WorktreeInfo(path=str(wt_path), branch=branch_name, repo_root=root, base_commit=base_commit)


def cleanup_worktree(info: WorktreeInfo) -> bool:
    """Remove a worktree and branch unless it has unpushed commits.

    Returns True when the worktree is absent after cleanup and False when it is
    intentionally preserved.
    """
    wt_path = info.path
    branch = info.branch
    repo_root = info.repo_root

    if not Path(wt_path).exists():
        return True

    has_unpushed = False
    try:
        if info.base_commit:
            result = subprocess.run(
                ["git", "rev-list", "--count", f"{info.base_commit}..HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=wt_path,
            )
            has_unpushed = result.returncode != 0 or int((result.stdout or "0").strip() or "0") > 0
        else:
            result = subprocess.run(
                ["git", "log", "--oneline", "HEAD", "--not", "--remotes"],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=wt_path,
            )
            has_unpushed = bool(result.stdout.strip())
    except Exception:
        has_unpushed = True

    if has_unpushed:
        return False

    try:
        subprocess.run(
            ["git", "worktree", "remove", wt_path, "--force"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=repo_root,
        )
    except Exception as exc:
        logger.debug("Failed to remove worktree: %s", exc)

    try:
        subprocess.run(
            ["git", "branch", "-D", branch],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=repo_root,
        )
    except Exception as exc:
        logger.debug("Failed to delete branch %s: %s", branch, exc)

    return not Path(wt_path).exists()


def changed_files(path: str | Path) -> list[str]:
    """Return changed tracked and untracked files for a worktree."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(path),
        )
    except Exception:
        return []
    if result.returncode != 0:
        return []

    files: list[str] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        file_part = line[3:].strip()
        if " -> " in file_part:
            file_part = file_part.split(" -> ", 1)[1].strip()
        files.append(file_part)
    return sorted(set(files))


def changed_files_between_ref(path: str | Path, ref: str = "HEAD") -> list[str]:
    """Return files changed against ``ref``, including untracked files."""
    files = set(changed_files(path))
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", ref],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(path),
        )
    except Exception:
        return sorted(files)
    if result.returncode == 0:
        files.update(line.strip() for line in result.stdout.splitlines() if line.strip())
    return sorted(files)


def prune_stale_worktrees(repo_root: str | Path, max_age_hours: int = 24) -> None:
    """Remove stale Hermes worktrees and orphaned local branches."""
    root = str(repo_root)
    worktrees_dir = Path(root) / ".worktrees"
    if not worktrees_dir.exists():
        prune_orphaned_branches(root)
        return

    now = time.time()
    soft_cutoff = now - (max_age_hours * 3600)
    hard_cutoff = now - (max_age_hours * 3 * 3600)

    for entry in worktrees_dir.iterdir():
        if not entry.is_dir() or not entry.name.startswith("hermes-"):
            continue

        try:
            mtime = entry.stat().st_mtime
            if mtime > soft_cutoff:
                continue
        except Exception:
            continue

        force = mtime <= hard_cutoff
        if not force:
            try:
                result = subprocess.run(
                    ["git", "log", "--oneline", "HEAD", "--not", "--remotes"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    cwd=str(entry),
                )
                if result.stdout.strip():
                    continue
            except Exception:
                continue

        try:
            branch_result = subprocess.run(
                ["git", "branch", "--show-current"],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=str(entry),
            )
            branch = branch_result.stdout.strip()
            subprocess.run(
                ["git", "worktree", "remove", str(entry), "--force"],
                capture_output=True,
                text=True,
                timeout=15,
                cwd=root,
            )
            if branch:
                subprocess.run(
                    ["git", "branch", "-D", branch],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    cwd=root,
                )
            logger.debug("Pruned stale worktree: %s (force=%s)", entry.name, force)
        except Exception as exc:
            logger.debug("Failed to prune worktree %s: %s", entry.name, exc)

    prune_orphaned_branches(root)


def prune_orphaned_branches(repo_root: str | Path) -> None:
    """Delete auto-generated local branches with no active worktree."""
    root = str(repo_root)
    try:
        result = subprocess.run(
            ["git", "branch", "--format=%(refname:short)"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=root,
        )
        if result.returncode != 0:
            return
        all_branches = [branch.strip() for branch in result.stdout.splitlines() if branch.strip()]
    except Exception:
        return

    active_branches: set[str] = set()
    try:
        wt_result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=root,
        )
        for line in wt_result.stdout.splitlines():
            if line.startswith("branch refs/heads/"):
                active_branches.add(line.split("branch refs/heads/", 1)[-1].strip())
    except Exception:
        return

    try:
        head_result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=root,
        )
        current = head_result.stdout.strip()
        if current:
            active_branches.add(current)
    except Exception:
        pass
    active_branches.add("main")

    orphaned = [
        branch
        for branch in all_branches
        if branch not in active_branches
        and (branch.startswith("hermes/hermes-") or branch.startswith("pr-"))
    ]
    if not orphaned:
        return

    for index in range(0, len(orphaned), 50):
        batch = orphaned[index : index + 50]
        try:
            subprocess.run(
                ["git", "branch", "-D", *batch],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=root,
            )
        except Exception as exc:
            logger.debug("Failed to prune orphaned branches: %s", exc)

    logger.debug("Pruned %d orphaned branches", len(orphaned))
