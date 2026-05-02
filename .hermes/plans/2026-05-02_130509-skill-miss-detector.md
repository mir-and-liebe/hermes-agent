# Skill Miss Detector Implementation Plan

## Goal

Ship a safe, test-backed Hermes improvement that detects sessions where the agent likely should have loaded a relevant skill but did not, so Mir can turn skill-adherence drift into measurable feedback and durable improvement.

## Current context

- Stable main is green at b965cbeb3.
- Work is isolated on branch `feat/skill-miss-detector`.
- Latest relevant stash is `stash@{0}: unrelated-skill-miss-detector-wip-before-ci-green-fix-2`.
- Stash contains:
  - `agent/skill_miss_detector.py`
  - `scripts/analyze_missed_skills.py`
  - `tests/agent/test_skill_miss_detector.py`
- This is custom Hermes behavior, so it needs targeted tests and should be added to Custom Hermes CI if it becomes a protected feature.

## Architecture

Add a small offline analyzer first, not inline runtime enforcement.

Boundaries:
- `agent/skill_miss_detector.py`: pure analysis logic, SQLite read path, redaction, report writing, summary formatting.
- `scripts/analyze_missed_skills.py`: thin CLI wrapper only.
- `tests/agent/test_skill_miss_detector.py`: deterministic unit tests using temporary SQLite DB and temp skills root.
- `.github/workflows/custom-hermes-ci.yml`: targeted test inclusion after feature is stable.

Design constraints:
- Read-only against session DB.
- No secret leakage in excerpts or reports.
- Deterministic tests; no local `/Users/liebe` paths.
- No broad runtime hooks until offline analyzer behavior is verified.
- Avoid `Any` sprawl where practical; define typed records/helpers if the current WIP is too loose.

## Step-by-step execution

1. Restore WIP safely
   - Apply `stash@{0}` on `feat/skill-miss-detector`.
   - Confirm only intended files appear.
   - Do not drop older stashes.

2. Subagent architecture inspection
   - Ask one subagent to inspect session DB schema/tool-call patterns and confirm the analyzer's assumptions.
   - Ask one subagent to inspect CI/custom safety-net integration points.
   - Use their findings to refine implementation tasks.

3. TDD hardening
   - Run existing new tests first; if they fail, classify failures.
   - Add/adjust tests for:
     - expected skill matching
     - loaded skill suppression
     - turn boundary behavior
     - old session / recent message window behavior
     - secret redaction including JSON-style values
     - report writing side effects
     - script CLI behavior if practical
   - Verify failures before implementation changes when adding new behavior.

4. Implementation/refactor
   - Tighten types around event/summary/rule structures where useful.
   - Fix redaction and analyzer edge cases uncovered by tests/subagents.
   - Keep CLI wrapper thin and explicit.
   - Add the test file to Custom Hermes CI if the feature is kept.

5. Subagent reviews
   - Spec reviewer checks implementation against this plan.
   - Code-quality/security reviewer checks redaction, DB handling, paths, tests, and scope control.
   - Address any critical or important findings.

6. Verification
   - Run targeted tests:
     - `venv/bin/python -m pytest tests/agent/test_skill_miss_detector.py -q`
   - Run compile checks:
     - `venv/bin/python -m py_compile agent/skill_miss_detector.py scripts/analyze_missed_skills.py`
   - Run Custom Hermes CI-equivalent targeted subset if workflow changed.
   - Run `git diff --check`.

7. Commit/push
   - Commit with conventional message, likely `feat: add missed skill detector`.
   - Push branch to origin.
   - Create PR or report branch ready, depending on whether Mir wants direct main merge.

## Risks

- False positives from keyword rules. Mitigation: offline telemetry only, conservative threshold, transparent matched patterns.
- Secret leakage in excerpts. Mitigation: central redaction tests and never write full raw content.
- Session DB schema drift. Mitigation: keep SQL minimal and test against expected schema; fail clearly if schema missing.
- Skill name aliases/category paths. Mitigation: compare both full loaded name and basename.

## Verification evidence required before completion

- Targeted tests pass.
- Compile checks pass.
- Custom CI-equivalent subset passes if CI workflow updated.
- Git diff reviewed.
- Branch pushed, or explicit blocker stated.
