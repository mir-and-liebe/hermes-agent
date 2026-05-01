#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: hermes-custom-update [--merge|--rebase] [--autostash] [--no-push] [--skip-verify]

Safely update Mir's customized Hermes distribution:
  official upstream main -> fork origin/main -> local main

Defaults:
  --merge       integrate upstream/main with a merge commit when needed
  --verify      run targeted Hermes verification
  --push        push updated custom main to origin
  clean tree    abort if uncommitted work exists unless --autostash is passed

Environment:
  HERMES_CUSTOM_VERIFY="cmd"  override verification command
USAGE
}

mode="merge"
autostash="false"
push_after="true"
verify_after="true"

for arg in "$@"; do
  case "$arg" in
    --merge) mode="merge" ;;
    --rebase) mode="rebase" ;;
    --autostash) autostash="true" ;;
    --no-push) push_after="false" ;;
    --skip-verify) verify_after="false" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

current_branch="$(git branch --show-current)"
if [[ "$current_branch" != "main" ]]; then
  echo "Switching to main from $current_branch"
  git checkout main
fi

origin_url="$(git remote get-url origin 2>/dev/null || true)"
upstream_url="$(git remote get-url upstream 2>/dev/null || true)"

if [[ "$origin_url" != *"mir-and-liebe/hermes-agent"* ]]; then
  echo "ERROR: origin is not Mir's fork: $origin_url" >&2
  echo "Expected origin to be https://github.com/mir-and-liebe/hermes-agent.git" >&2
  exit 1
fi

if [[ "$upstream_url" != *"NousResearch/hermes-agent"* ]]; then
  echo "ERROR: upstream is not official Hermes: $upstream_url" >&2
  echo "Expected upstream to be https://github.com/NousResearch/hermes-agent.git" >&2
  exit 1
fi

stash_ref=""
if ! git diff --quiet || ! git diff --cached --quiet || [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
  if [[ "$autostash" != "true" ]]; then
    echo "ERROR: working tree is dirty. Commit/stash it or rerun with --autostash." >&2
    git status --short >&2
    exit 1
  fi
  before_stash="$(git rev-parse -q --verify refs/stash 2>/dev/null || true)"
  git stash push --include-untracked -m "hermes-custom-update-autostash-$(date +%Y%m%d-%H%M%S)"
  after_stash="$(git rev-parse -q --verify refs/stash 2>/dev/null || true)"
  if [[ "$after_stash" != "$before_stash" ]]; then
    stash_ref="stash@{0}"
  fi
fi

restore_stash() {
  if [[ -n "$stash_ref" ]]; then
    echo "Restoring autostash $stash_ref"
    git stash pop "$stash_ref" || {
      echo "WARNING: autostash restore had conflicts. Stash was kept by git; resolve manually." >&2
      exit 1
    }
  fi
}
trap restore_stash EXIT

git fetch upstream main
git fetch origin main

case "$mode" in
  merge)
    git merge --no-edit upstream/main
    ;;
  rebase)
    git rebase upstream/main
    ;;
esac

if [[ "$verify_after" == "true" ]]; then
  if [[ -n "${HERMES_CUSTOM_VERIFY:-}" ]]; then
    bash -lc "$HERMES_CUSTOM_VERIFY"
  elif [[ -x venv/bin/python && -f tests/hermes_cli/test_parallel.py ]]; then
    venv/bin/python -m pytest \
      tests/hermes_cli/test_parallel.py \
      tests/cli/test_worktree.py \
      tests/cli/test_worktree_security.py \
      -q
    venv/bin/python -m py_compile \
      hermes_cli/worktree.py \
      hermes_cli/parallel.py \
      cli.py \
      hermes_cli/main.py \
      hermes_cli/tips.py
  elif [[ -x venv/bin/python ]]; then
    venv/bin/python -m py_compile hermes_cli/main.py cli.py
  else
    python3 -m py_compile hermes_cli/main.py cli.py
  fi
fi

if [[ "$push_after" == "true" ]]; then
  git push origin main
fi

echo "Hermes custom update complete."
