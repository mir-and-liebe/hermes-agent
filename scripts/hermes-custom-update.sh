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
  HERMES_CUSTOM_UPDATE_STATE="path"  write structured update state
USAGE
}

now_utc() {
  date -u +%Y-%m-%dT%H:%M:%SZ
}

bool_word() {
  case "${1:-false}" in
    true|1|yes|y) echo "true" ;;
    *) echo "false" ;;
  esac
}

resolve_path() {
  python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).expanduser().resolve())' "$1"
}

remote_is_expected() {
  local url="$1"
  local owner="$2"
  local repo="$3"
  case "$url" in
    "https://github.com/${owner}/${repo}"|"https://github.com/${owner}/${repo}.git"|"git@github.com:${owner}/${repo}"|"git@github.com:${owner}/${repo}.git"|"ssh://git@github.com/${owner}/${repo}"|"ssh://git@github.com/${owner}/${repo}.git")
      return 0
      ;;
  esac
  if [[ "$url" == *"://"* ]]; then
    return 1
  fi
  [[ "$url" =~ (^|/)${owner}/${repo}(\.git)?$ ]]
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

started_epoch="$(date -u +%s)"
started_at="$(now_utc)"
failed_step="init"
failure_code=""
failure_summary=""
stash_ref=""
stash_created="false"
stash_restored="false"
stash_restore_conflicted="false"
verified="false"
pushed="false"
before_head=""
repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
script_dir="$repo_root/scripts"
state_helper="$script_dir/hermes_custom_update_state.py"
state_path="${HERMES_CUSTOM_UPDATE_STATE:-$HOME/.hermes/ops/state/hermes-custom-update.json}"
state_path="$(resolve_path "$state_path")"
allowed_state_root="$(resolve_path "$HOME/.hermes/ops/state")"
if [[ "$state_path" != "$allowed_state_root" && "$state_path" != "$allowed_state_root"/* ]]; then
  if [[ "${HERMES_CUSTOM_UPDATE_ALLOW_STATE_PATH_OUTSIDE_HOME:-false}" != "true" ]]; then
    echo "ERROR: custom update state path must stay under ~/.hermes/ops/state." >&2
    echo "Set HERMES_CUSTOM_UPDATE_ALLOW_STATE_PATH_OUTSIDE_HOME=true only for isolated tests." >&2
    exit 1
  fi
fi

safe_git_stdout() {
  git "$@" 2>/dev/null || true
}

safe_rev_count() {
  local left="$1"
  local right="$2"
  if [[ -z "$left" || -z "$right" ]]; then
    return 0
  fi
  git rev-list --left-right --count "$left...$right" 2>/dev/null || true
}

write_update_state() {
  local final_rc="$1"
  local final_status="success"
  local finished_epoch finished_at duration after_head branch origin_main upstream_main changed
  local local_matches_origin_main origin_matches_upstream_main
  local local_vs_upstream_left_right_count origin_vs_upstream_left_right_count

  if [[ "$final_rc" -ne 0 ]]; then
    final_status="failed"
  fi

  finished_epoch="$(date -u +%s)"
  finished_at="$(now_utc)"
  duration="$((finished_epoch - started_epoch))"
  after_head="$(safe_git_stdout rev-parse HEAD)"
  branch="$(safe_git_stdout branch --show-current)"
  origin_main="$(safe_git_stdout rev-parse origin/main)"
  upstream_main="$(safe_git_stdout rev-parse upstream/main)"
  local_vs_upstream_left_right_count="$(safe_rev_count "$after_head" "upstream/main")"
  origin_vs_upstream_left_right_count="$(safe_rev_count "origin/main" "upstream/main")"

  changed="false"
  if [[ -n "$before_head" && -n "$after_head" && "$before_head" != "$after_head" ]]; then
    changed="true"
  fi

  local_matches_origin_main="false"
  if [[ -n "$after_head" && -n "$origin_main" && "$after_head" == "$origin_main" ]]; then
    local_matches_origin_main="true"
  fi

  origin_matches_upstream_main="false"
  if [[ -n "$origin_main" && -n "$upstream_main" && "$origin_main" == "$upstream_main" ]]; then
    origin_matches_upstream_main="true"
  fi

  if [[ "$final_status" == "failed" && -z "$failure_code" ]]; then
    failure_code="${failed_step}_failed"
  fi
  if [[ "$final_status" == "failed" && -z "$failure_summary" ]]; then
    failure_summary="custom update failed at step: $failed_step"
  fi

  if [[ ! -f "$state_helper" ]]; then
    echo "WARNING: custom update state helper not found; skipping state write" >&2
    return 0
  fi

  HCU_STATUS="$final_status" \
  HCU_REPO="$repo_root" \
  HCU_BRANCH="$branch" \
  HCU_MODE="$mode" \
  HCU_AUTOSTASH="$(bool_word "$autostash")" \
  HCU_PUSH_AFTER="$(bool_word "$push_after")" \
  HCU_VERIFY_AFTER="$(bool_word "$verify_after")" \
  HCU_STARTED_AT="$started_at" \
  HCU_FINISHED_AT="$finished_at" \
  HCU_DURATION_SECONDS="$duration" \
  HCU_BEFORE_HEAD="$before_head" \
  HCU_AFTER_HEAD="$after_head" \
  HCU_ORIGIN_MAIN="$origin_main" \
  HCU_UPSTREAM_MAIN="$upstream_main" \
  HCU_CHANGED="$changed" \
  HCU_PUSHED="$(bool_word "$pushed")" \
  HCU_VERIFIED="$(bool_word "$verified")" \
  HCU_STASH_CREATED="$(bool_word "$stash_created")" \
  HCU_STASH_RESTORED="$(bool_word "$stash_restored")" \
  HCU_STASH_RESTORE_CONFLICTED="$(bool_word "$stash_restore_conflicted")" \
  HCU_FAILED_STEP="$failed_step" \
  HCU_FAILURE_CODE="$failure_code" \
  HCU_FAILURE_SUMMARY="$failure_summary" \
  HCU_EXIT_CODE="$final_rc" \
  HCU_LOCAL_MATCHES_ORIGIN_MAIN="$local_matches_origin_main" \
  HCU_ORIGIN_MATCHES_UPSTREAM_MAIN="$origin_matches_upstream_main" \
  HCU_LOCAL_VS_UPSTREAM_LEFT_RIGHT_COUNT="$local_vs_upstream_left_right_count" \
  HCU_ORIGIN_VS_UPSTREAM_LEFT_RIGHT_COUNT="$origin_vs_upstream_left_right_count" \
  python3 "$state_helper" write-from-env --path "$state_path" || {
    echo "WARNING: failed to write custom update state" >&2
  }
}

finalize() {
  local rc="$?"
  local final_rc="$rc"
  set +e

  if [[ -n "$stash_ref" ]]; then
    echo "Restoring autostash $stash_ref"
    git stash pop "$stash_ref"
    local stash_rc="$?"
    if [[ "$stash_rc" -eq 0 ]]; then
      stash_restored="true"
    else
      stash_restore_conflicted="true"
      echo "WARNING: autostash restore had conflicts. Stash was kept by git; resolve manually." >&2
      if [[ "$final_rc" -eq 0 ]]; then
        final_rc="$stash_rc"
        failed_step="restore_stash"
        failure_code="stash_restore_conflict"
        failure_summary="autostash restore failed; resolve conflicts manually"
      fi
    fi
  fi

  write_update_state "$final_rc"
  exit "$final_rc"
}
trap finalize EXIT

failed_step="checkout_main"
current_branch="$(git branch --show-current)"
if [[ "$current_branch" != "main" ]]; then
  echo "Switching to main from $current_branch"
  git checkout main
fi

before_head="$(git rev-parse HEAD)"

failed_step="validate_origin_remote"
origin_url="$(git remote get-url origin 2>/dev/null || true)"
if ! remote_is_expected "$origin_url" "mir-and-liebe" "hermes-agent"; then
  echo "ERROR: origin is not Mir's fork." >&2
  echo "Expected origin to be mir-and-liebe/hermes-agent." >&2
  exit 1
fi

failed_step="validate_upstream_remote"
upstream_url="$(git remote get-url upstream 2>/dev/null || true)"
if ! remote_is_expected "$upstream_url" "NousResearch" "hermes-agent"; then
  echo "ERROR: upstream is not official Hermes." >&2
  echo "Expected upstream to be NousResearch/hermes-agent." >&2
  exit 1
fi

failed_step="protect_dirty_tree"
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
    stash_created="true"
  fi
fi

failed_step="fetch_upstream"
git fetch upstream main
failed_step="fetch_origin"
git fetch origin main

case "$mode" in
  merge)
    failed_step="merge_upstream"
    git merge --no-edit upstream/main
    ;;
  rebase)
    failed_step="rebase_upstream"
    git rebase upstream/main
    ;;
esac

if [[ "$verify_after" == "true" ]]; then
  failed_step="verify"
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
  verified="true"
fi

if [[ "$push_after" == "true" ]]; then
  failed_step="push_origin"
  git push origin main
  pushed="true"
fi

failed_step="complete"
echo "Hermes custom update complete."
