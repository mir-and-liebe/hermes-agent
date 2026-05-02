# Custom Update State Reporting Plan

## Goal

Harden the custom Hermes upstream-sync loop so every `hermes-custom-update` run leaves a small machine-readable state artifact with enough information for cron/operator reporting: repo, branch, mode, push/verify choices, SHAs before/after, upstream range, status, and failure reason when the script exits nonzero.

This is Vector B: operational reliability around the fork-backed custom Hermes distribution.

## Current context

- Canonical repo: `/Users/liebe/.hermes/hermes-agent`
- Stable branch `main` is green at `d95dfbe5e63bf378023f047fefc5e80900ad3025`.
- `scripts/hermes-custom-update.sh` currently performs topology checks, optional autostash, fetch, merge/rebase, verify, push, and prints a final success line.
- Weekly cron has a separate preflight script at `/Users/liebe/.hermes/scripts/hermes_custom_update_context.py`; that script can decide silent/no-op but does not receive structured result state from the update script.
- Existing tests do not cover `scripts/hermes-custom-update.sh` beyond shell syntax in CI.

## Proposed behavior

Add state writing to `scripts/hermes-custom-update.sh` without changing its primary CLI contract:

- Default state path: `${HERMES_CUSTOM_UPDATE_STATE:-$HOME/.hermes/ops/state/hermes-custom-update.json}`
- State should be written atomically enough for local cron use: write temp file then move into place.
- State should not include secrets, token values, remote URLs with credentials, or raw stderr logs.
- On success, include:
  - `status: "success"`
  - `repo`, `branch`, `mode`, `autostash`, `push_after`, `verify_after`
  - `started_at`, `finished_at`
  - `before_head`, `after_head`, `origin_main`, `upstream_main`
  - `upstream_range` or equivalent ahead/behind count
  - `changed: true/false`
  - `pushed: true/false`
  - `verified: true/false`
  - `stash_created: true/false`
- On failure, include:
  - `status: "failed"`
  - `failed_step` if known
  - `exit_code`
  - the same safe state fields known at the point of failure
- No-op runs should still write state with `changed: false` and `status: "success"`.

## Implementation approach

1. Add a small helper script for state writing instead of making Bash hand-roll JSON escaping.
   - Proposed path: `scripts/hermes_custom_update_state.py`
   - CLI shape:
     - `scripts/hermes_custom_update_state.py write --path PATH --status success --repo REPO ...`
   - Prefer stdlib only.
   - Keep types explicit.

2. Wire `scripts/hermes-custom-update.sh` to collect state fields and call the helper on EXIT.
   - Add `started_at` early.
   - Track `failed_step` by setting a variable before major operations.
   - Use a trap to write state before restoring autostash or just before exit. Avoid hiding original exit code.
   - Sanitize remote URLs or omit them entirely.

3. Add tests around the helper and a lightweight integration path.
   - Unit test the Python helper in `tests/scripts/test_hermes_custom_update_state.py`.
   - Add a shell-level smoke test in Python that creates a temporary git repo with `origin` and `upstream` bare repos, copies/runs `scripts/hermes-custom-update.sh` with `HERMES_CUSTOM_UPDATE_STATE` and `HERMES_CUSTOM_VERIFY=true`, then asserts state fields.
   - Keep test small and not network-dependent.

4. Add new state helper/test targets to `.github/workflows/custom-hermes-ci.yml`.

## Files likely to change

- `scripts/hermes-custom-update.sh`
- `scripts/hermes_custom_update_state.py`
- `tests/scripts/test_hermes_custom_update_state.py`
- possibly `tests/scripts/test_hermes_custom_update_script.py`
- `.github/workflows/custom-hermes-ci.yml`
- `autonomous-ai-agents/hermes-custom-distribution` skill only if a new operator rule is discovered while implementing

## Verification

Minimum local gate:

```bash
bash -n scripts/hermes-custom-update.sh
venv/bin/python -m py_compile scripts/hermes_custom_update_state.py tests/scripts/test_hermes_custom_update_state.py
venv/bin/python -m pytest tests/scripts/test_hermes_custom_update_state.py -q --tb=short
```

If shell integration test is added:

```bash
venv/bin/python -m pytest tests/scripts/test_hermes_custom_update_state.py tests/scripts/test_hermes_custom_update_script.py -q --tb=short
```

Custom safety net:

```bash
bash -n scripts/hermes-custom-update.sh
bash -n scripts/hermes-fork-upgrade.sh
bash -n scripts/run_tests.sh
python scripts/audit_silent_failures.py run_agent.py model_tools.py tools/registry.py cron gateway hermes_cli tui_gateway --baseline .silent-failures-baseline.json
venv/bin/python -m pytest <custom-targets> -q --tb=short
```

## Risks

- Bash EXIT traps can accidentally hide original exit code. Preserve and re-exit with the original code.
- Autostash restore failures should remain visible and should update state as failed.
- State writing must not break updates if the state directory cannot be written unless explicitly desirable. Default: state write failures should warn but not hide the update result.
- Avoid writing state into repo-tracked files; default goes under `~/.hermes/ops/state/`.
