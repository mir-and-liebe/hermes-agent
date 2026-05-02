#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: hermes-fork-upgrade [options]

Upgrade Mir's custom Hermes through the fork-backed workflow, not `hermes update`.

Flow:
  1. Verify local checkout topology:
       origin   -> mir-and-liebe/hermes-agent
       upstream -> NousResearch/hermes-agent, push disabled
  2. Run hermes-custom-update --autostash with the supplied options.
  3. If merge conflicts remain, launch a Hermes resolver session with the
     canonical custom-distribution skill and a self-contained conflict prompt.
  4. The resolver must preserve custom behavior, incorporate upstream main,
     verify, and push only to origin/main.

Options passed through to hermes-custom-update:
  --merge          Merge upstream/main into custom main (default)
  --rebase         Rebase custom main on upstream/main
  --no-push        Do not push after successful update
  --skip-verify    Skip deterministic verification in hermes-custom-update

Options for this command:
  --no-ai-resolve  Do not launch the Hermes conflict resolver; stop on conflict
  --help, -h       Show this help

Recommended normal use:
  hermes-fork-upgrade

If you want a dry/safe topology check without pushing or tests:
  hermes-fork-upgrade --skip-verify --no-push --no-ai-resolve
USAGE
}

repo="${HERMES_AGENT_REPO:-$HOME/.hermes/hermes-agent}"
ai_resolve="true"
pass_args=(--autostash)

for arg in "$@"; do
  case "$arg" in
    --merge|--rebase|--no-push|--skip-verify)
      pass_args+=("$arg")
      ;;
    --no-ai-resolve)
      ai_resolve="false"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! -d "$repo/.git" ]]; then
  echo "ERROR: Hermes repo not found at $repo" >&2
  exit 1
fi

cd "$repo"

branch="$(git branch --show-current)"
if [[ "$branch" != "main" ]]; then
  echo "Switching Hermes checkout from $branch to main"
  git switch main
fi

origin_url="$(git remote get-url origin 2>/dev/null || true)"
upstream_url="$(git remote get-url upstream 2>/dev/null || true)"
upstream_push_url="$(git remote get-url --push upstream 2>/dev/null || true)"

if [[ "$origin_url" != *"mir-and-liebe/hermes-agent"* ]]; then
  echo "ERROR: origin is not Mir's Hermes fork: $origin_url" >&2
  exit 1
fi

if [[ "$upstream_url" != *"NousResearch/hermes-agent"* ]]; then
  echo "ERROR: upstream is not official Hermes: $upstream_url" >&2
  exit 1
fi

if [[ "$upstream_push_url" != "DISABLED" ]]; then
  echo "ERROR: upstream push URL is not disabled: $upstream_push_url" >&2
  echo "Run: git remote set-url --push upstream DISABLED" >&2
  exit 1
fi

updater=""
if command -v hermes-custom-update >/dev/null 2>&1; then
  updater="$(command -v hermes-custom-update)"
elif [[ -x scripts/hermes-custom-update.sh ]]; then
  updater="scripts/hermes-custom-update.sh"
else
  echo "ERROR: hermes-custom-update is not installed and scripts/hermes-custom-update.sh is missing" >&2
  exit 1
fi

set +e
"$updater" "${pass_args[@]}"
status=$?
set -e

if [[ $status -eq 0 ]]; then
  echo "Hermes fork upgrade finished through origin/main."
  exit 0
fi

conflicts="$(git diff --name-only --diff-filter=U || true)"
if [[ -z "$conflicts" ]]; then
  echo "hermes-custom-update failed without active merge conflicts (exit $status)." >&2
  echo "Inspect the output above, then rerun hermes-fork-upgrade after fixing the blocker." >&2
  exit "$status"
fi

cat >&2 <<EOF
hermes-custom-update stopped with merge conflicts:
$conflicts
EOF

if [[ "$ai_resolve" != "true" ]]; then
  echo "Stopped because --no-ai-resolve was set." >&2
  exit "$status"
fi

if ! command -v hermes >/dev/null 2>&1; then
  echo "ERROR: hermes command is unavailable, cannot launch resolver." >&2
  exit "$status"
fi

prompt="$(cat <<'PROMPT'
You are resolving a Hermes custom fork upgrade conflict for Mir.

Canonical repo: /Users/liebe/.hermes/hermes-agent
Goal: finish merging official NousResearch/hermes-agent upstream/main into Mir's fork-backed origin/main without losing custom Hermes upgrades.

Rules:
- Load and follow the hermes-custom-distribution, systematic-debugging, and verification-before-completion skills.
- Keep origin as mir-and-liebe/hermes-agent and upstream as NousResearch/hermes-agent.
- Never push to upstream. Upstream push URL must remain DISABLED.
- Preserve Mir's custom behavior unless it is clearly obsolete and replaced by equivalent upstream functionality.
- Resolve current Git conflicts one file at a time.
- Run targeted verification after resolving conflicts. Minimum:
  bash -n scripts/hermes-custom-update.sh
  bash -n scripts/hermes-fork-upgrade.sh
  bash -n ~/.local/bin/hermes-fork-upgrade
  venv/bin/python -m py_compile hermes_cli/main.py cli.py
  If custom CI/test files exist, also run the targeted Custom Hermes CI test set or the closest local equivalent.
- Commit the merge if Git still needs a merge commit.
- Push only to origin main after verification passes.
- If conflict resolution is unsafe, stop and report exact conflicted files and why.
- Final response must include verification evidence, final HEAD, and origin/main sync state.
PROMPT
)"

exec hermes \
  --skills hermes-custom-distribution \
  --skills systematic-debugging \
  --skills verification-before-completion \
  chat \
  -q "$prompt" \
  --source hermes-fork-upgrade
