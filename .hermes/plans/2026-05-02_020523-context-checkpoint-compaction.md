# Context Checkpoint Compaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade Hermes context compression from a single threshold into a pressure-aware, checkpoint-first compaction system that preserves continuation state before any context is discarded.

**Architecture:** Hermes already has a strong structured compressor in `agent/context_compressor.py`; do not replace it. Add a small pressure-band resolver and a checkpoint artifact writer around the existing `_compress_context()` boundary in `run_agent.py`. The checkpoint writer should be deterministic, redacted, persisted before compression, then enriched after compression with the actual summary/new session metadata.

**Tech Stack:** Python 3, pytest, Hermes `AIAgent`, `ContextCompressor`, SQLite session DB, YAML config.

---

## Verdict

This is worthy, with one correction: Hermes is not currently doing purely random compaction. The built-in compressor already has:

- structured summary sections in `agent/context_compressor.py`
- iterative summary updates across repeated compactions
- head/tail protection
- tool-result pruning
- session DB split that preserves the old full transcript before continuing

The valuable implementation is not “add a good summary template from scratch.” It is:

1. Calibrate thresholds for large-context GPT-5.5-style models.
2. Add explicit soft/normal/emergency/hard pressure bands instead of one opaque `compression.threshold`.
3. Persist a human-readable, redacted checkpoint artifact before compaction begins.
4. Add tests that prove active task, files, decisions, commands, errors, and next actions survive compaction.

## Current context verified

Relevant files inspected:

- `/Users/liebe/.hermes/hermes-agent/agent/context_compressor.py`
  - Existing `ContextCompressor` has structured sections: Active Task, Goal, Constraints & Preferences, Completed Actions, Active State, In Progress, Blocked, Key Decisions, Resolved Questions, Pending User Asks, Relevant Files, Remaining Work, Critical Context.
  - Existing default `threshold_percent` constructor value is `0.50`.
  - Existing compression is iterative via `_previous_summary`.

- `/Users/liebe/.hermes/hermes-agent/run_agent.py`
  - Reads `compression.threshold` from config at lines around 1804-1810, defaulting to `0.50`.
  - Initializes `ContextCompressor` around lines 1981-1994.
  - `_compress_context()` starts around line 8980 and is the correct integration point for checkpoint persistence.
  - `_compress_context()` already calls memory pre-compress hooks and rotates the session row after compression.

- `/Users/liebe/.hermes/hermes-agent/agent/context_engine.py`
  - Context engine interface defaults to `threshold_percent = 0.75`, but built-in construction currently overrides to config default `0.50`.

- Existing tests:
  - `/Users/liebe/.hermes/hermes-agent/tests/agent/test_context_compressor.py`
  - `/Users/liebe/.hermes/hermes-agent/tests/run_agent/test_compression_persistence.py`
  - `/Users/liebe/.hermes/hermes-agent/tests/run_agent/test_compression_boundary.py`
  - `/Users/liebe/.hermes/hermes-agent/tests/run_agent/test_compression_feasibility.py`
  - `/Users/liebe/.hermes/hermes-agent/tests/cli/test_manual_compress.py`
  - `/Users/liebe/.hermes/hermes-agent/tests/gateway/test_compress_command.py`

## Target behavior

For a 272,000-token context window, default pressure bands should resolve approximately to:

- Soft warning: 160K-180K
- Normal compaction: 200K
- Emergency compaction: 220K
- Never exceed: 235K+

Implement as ratios so other models are supported:

- soft warning: `0.62 * context_length` => 168,640 for 272K
- normal compaction: `0.735 * context_length` => 199,920 for 272K
- emergency compaction: `0.81 * context_length` => 220,320 for 272K
- hard limit / never exceed: `0.865 * context_length` => 235,280 for 272K

Keep config overrides because some providers reserve output tokens differently.

## Proposed config shape

Backward compatible with existing `compression.threshold`:

```yaml
compression:
  enabled: true
  threshold: 0.735              # existing field; default should move from 0.50 to 0.735 after tests
  target_ratio: 0.20
  protect_last_n: 20
  soft_warning_threshold: 0.62
  emergency_threshold: 0.81
  hard_limit_threshold: 0.865
  checkpoint_enabled: true
  checkpoint_dir: context-checkpoints
```

Rules:

- If only `compression.threshold` exists, use it as normal compaction threshold.
- If pressure-band fields are absent, derive defaults from the context window.
- Clamp thresholds to safe order: `soft < normal < emergency < hard`.
- Never include secrets in checkpoint files.
- Never compress tool schemas or system/developer instructions.

## Files to change

- Create: `/Users/liebe/.hermes/hermes-agent/agent/context_pressure.py`
  - Pure pressure-band dataclass and resolver.

- Create: `/Users/liebe/.hermes/hermes-agent/agent/context_checkpoint.py`
  - Pure checkpoint rendering and safe file persistence.

- Modify: `/Users/liebe/.hermes/hermes-agent/run_agent.py`
  - Parse new compression pressure config.
  - Initialize compressor with resolved normal threshold.
  - Emit soft/emergency warnings.
  - Persist checkpoint before calling `context_compressor.compress()`.
  - Enrich checkpoint after compression with new session id and summary metadata.

- Modify: `/Users/liebe/.hermes/hermes-agent/agent/context_compressor.py`
  - Expose the last generated summary/checkpoint body via a read-only field or getter, without changing the summary format.
  - Optional: add `compression_count`, `last_summary_hash`, and `last_summary_token_estimate` metadata.

- Modify: `/Users/liebe/.hermes/hermes-agent/agent/manual_compression_feedback.py`
  - Include checkpoint path in manual `/compress` feedback when available.

- Modify: `/Users/liebe/.hermes/hermes-agent/tui_gateway/server.py`
  - Return checkpoint path in manual compression RPC response if `_compress_context()` exposes it.

- Modify: `/Users/liebe/.hermes/hermes-agent/gateway/run.py`
  - Include checkpoint path in `/compress` command replies and hygiene compression logs.

- Create: `/Users/liebe/.hermes/hermes-agent/tests/agent/test_context_pressure.py`
- Create: `/Users/liebe/.hermes/hermes-agent/tests/agent/test_context_checkpoint.py`
- Create: `/Users/liebe/.hermes/hermes-agent/tests/run_agent/test_compression_checkpoints.py`
- Modify existing compression tests only where required by changed default threshold.

## Checkpoint artifact format

Write markdown to:

`<HERMES_HOME>/context-checkpoints/<session_id>/<YYYYMMDD_HHMMSS>_<reason>.md`

Template:

```markdown
# Context Compaction Checkpoint

## Metadata
- Session ID:
- Parent session ID:
- Platform:
- Model:
- Provider:
- Context window:
- Estimated request tokens before:
- Pressure band:
- Compression reason:
- Checkpoint created at:
- New session ID: pending
- Estimated request tokens after: pending

## Current Goal

## Decisions Made

## Important Constraints

## Files Changed

## Files Read / Inspected

## Commands Run

## Errors Encountered

## Current Unresolved Problems

## Next 3 Actions

## Things To Preserve In Memory

## Things Safe To Forget

## Raw Tool Artifact Index

## Compression Summary
pending until compression completes
```

Important: `Next 3 Actions` is okay in the checkpoint file because it is for human/agent recovery, but the injected compaction summary should continue using `Remaining Work` to avoid turning old next steps into active instructions.

## Task 1: Add pressure-band resolver

**Files:**
- Create: `agent/context_pressure.py`
- Test: `tests/agent/test_context_pressure.py`

- [ ] Step 1: Write failing tests for 272K defaults.

Test cases:

```python
from agent.context_pressure import resolve_context_pressure_bands


def test_resolves_gpt55_sized_pressure_bands():
    bands = resolve_context_pressure_bands(context_length=272_000, config={})

    assert bands.soft_warning_tokens == 168_640
    assert bands.normal_compaction_tokens == 199_920
    assert bands.emergency_compaction_tokens == 220_320
    assert bands.hard_limit_tokens == 235_280


def test_existing_threshold_config_remains_normal_threshold():
    bands = resolve_context_pressure_bands(
        context_length=272_000,
        config={"threshold": 0.50},
    )

    assert bands.normal_compaction_tokens == 136_000
    assert bands.soft_warning_tokens < bands.normal_compaction_tokens
    assert bands.emergency_compaction_tokens > bands.normal_compaction_tokens
    assert bands.hard_limit_tokens > bands.emergency_compaction_tokens


def test_pressure_bands_are_clamped_in_order():
    bands = resolve_context_pressure_bands(
        context_length=100_000,
        config={
            "soft_warning_threshold": 0.90,
            "threshold": 0.70,
            "emergency_threshold": 0.60,
            "hard_limit_threshold": 0.50,
        },
    )

    assert bands.soft_warning_tokens < bands.normal_compaction_tokens
    assert bands.normal_compaction_tokens < bands.emergency_compaction_tokens
    assert bands.emergency_compaction_tokens < bands.hard_limit_tokens
```

- [ ] Step 2: Run targeted test and verify it fails.

Run:

```bash
cd /Users/liebe/.hermes/hermes-agent
source venv/bin/activate
pytest tests/agent/test_context_pressure.py -q
```

Expected: import failure for `agent.context_pressure`.

- [ ] Step 3: Implement `agent/context_pressure.py` as a pure module.

Implementation requirements:

- Use `@dataclass(frozen=True)`.
- Accept `context_length: int` and `config: Mapping[str, Any]`.
- Support ratios and absolute token values later if config value is > 1.
- Return integer token counts.
- Clamp to maintain order without surprising crashes.
- No `Any` in public dataclass fields.

- [ ] Step 4: Run test and verify pass.

```bash
pytest tests/agent/test_context_pressure.py -q
```

## Task 2: Add checkpoint rendering and persistence

**Files:**
- Create: `agent/context_checkpoint.py`
- Test: `tests/agent/test_context_checkpoint.py`

- [ ] Step 1: Write failing tests for deterministic redacted checkpoint rendering.

Test cases should assert:

- checkpoint contains the required sections exactly once
- checkpoint includes session/model/token metadata
- checkpoint extracts file paths from tool calls/results where possible
- checkpoint includes terminal commands and exit codes where possible
- checkpoint redacts secrets using existing `redact_sensitive_text`
- checkpoint persistence creates parent directories and returns a path

- [ ] Step 2: Implement pure extraction helpers.

Suggested functions:

```python
@dataclass(frozen=True)
class ContextCheckpointMetadata:
    session_id: str
    parent_session_id: str | None
    platform: str
    model: str
    provider: str
    context_length: int
    estimated_tokens_before: int | None
    pressure_band: str
    compression_reason: str
    created_at_iso: str


def render_context_checkpoint(
    messages: Sequence[Mapping[str, object]],
    metadata: ContextCheckpointMetadata,
    *,
    summary_text: str | None = None,
    new_session_id: str | None = None,
    estimated_tokens_after: int | None = None,
) -> str:
    ...


def write_context_checkpoint(
    hermes_home: Path,
    session_id: str,
    reason: str,
    content: str,
) -> Path:
    ...
```

- [ ] Step 3: Keep extraction intentionally conservative.

Do not try to perfectly understand every message. Extract high-signal facts:

- user latest ask => Current Goal
- assistant tool calls for `read_file`, `write_file`, `patch`, `terminal`, `search_files`
- tool outputs with explicit `exit_code`, errors, exceptions, traceback lines
- todo snapshot if present
- file paths in tool arguments

- [ ] Step 4: Run tests.

```bash
pytest tests/agent/test_context_checkpoint.py -q
```

## Task 3: Expose last summary metadata from ContextCompressor

**Files:**
- Modify: `agent/context_compressor.py`
- Test: `tests/agent/test_context_compressor.py`

- [ ] Step 1: Add tests proving summary metadata is available after compression.

Expected fields:

- `last_summary_text: str | None`
- `last_summary_hash: str | None`
- `last_summary_fallback_used: bool`

- [ ] Step 2: Implement without changing injected summary behavior.

Rules:

- `last_summary_text` should be redacted summary body with `SUMMARY_PREFIX` stripped, or the exact injected text if stripping is risky.
- Do not expose secrets.
- Reset metadata in `on_session_reset()`.
- Keep existing `_previous_summary` behavior intact.

- [ ] Step 3: Run compressor tests.

```bash
pytest tests/agent/test_context_compressor.py tests/agent/test_compress_focus.py -q
```

## Task 4: Integrate checkpoint persistence in `_compress_context()`

**Files:**
- Modify: `run_agent.py`
- Test: `tests/run_agent/test_compression_checkpoints.py`

- [ ] Step 1: Write failing integration test.

Test should monkeypatch compressor to avoid a real LLM call and verify:

- checkpoint file is written before `compress()` is called
- checkpoint path survives if `compress()` raises
- successful compression enriches checkpoint with new session id and post-token estimate
- checkpoint path is stored on the agent as `_last_compression_checkpoint_path`

- [ ] Step 2: Add checkpoint call before line ~9006 where `context_compressor.compress()` is invoked.

Pseudo-flow:

```python
checkpoint_path = None
if checkpoint_enabled:
    checkpoint_path = _write_pre_compression_checkpoint(...)
    self._last_compression_checkpoint_path = str(checkpoint_path)

try:
    compressed = self.context_compressor.compress(...)
finally:
    # if failed, pre-checkpoint remains useful
```

After compression and session rotation, rewrite/enrich the same checkpoint with:

- new session id
- estimated post-compression tokens
- summary text/hash
- fallback warning if summary generation failed

- [ ] Step 3: Make failure non-blocking.

Checkpoint failures should log warnings but never block compression. Compression itself remains the primary safety operation.

- [ ] Step 4: Run targeted tests.

```bash
pytest tests/run_agent/test_compression_checkpoints.py tests/run_agent/test_compression_persistence.py -q
```

## Task 5: Add soft/emergency pressure warnings

**Files:**
- Modify: `run_agent.py`
- Test: `tests/run_agent/test_context_pressure_warnings.py`

- [ ] Step 1: Write tests for warning cadence.

Cases:

- below soft: no warning
- above soft, below normal: one warning per band crossing
- above emergency: warning says emergency compression will fire before next risky call
- above hard: force compression preflight if possible; if compression disabled, visible warning suggests `/compress` or `/new`

- [ ] Step 2: Integrate `resolve_context_pressure_bands()` during AIAgent init.

Store on agent:

```python
self._context_pressure_bands = bands
self._last_context_pressure_band = "normal" | "soft" | "emergency" | "hard"
```

- [ ] Step 3: Change default normal threshold from `0.50` to `0.735` only after tests cover small-context models.

For small context windows, do not allow threshold below `MINIMUM_CONTEXT_LENGTH` behavior to regress.

- [ ] Step 4: Ensure existing explicit config still wins.

If Mir already sets `compression.threshold: 0.50`, do not silently change it.

## Task 6: Surface checkpoint path in manual compression UX

**Files:**
- Modify: `agent/manual_compression_feedback.py`
- Modify: `tui_gateway/server.py`
- Modify: `gateway/run.py`
- Tests:
  - `tests/cli/test_manual_compress.py`
  - `tests/gateway/test_compress_command.py`
  - `tests/gateway/test_compress_focus.py`

- [ ] Step 1: Extend `summarize_manual_compression()` with optional `checkpoint_path: str | None`.

- [ ] Step 2: Include one concise line:

```text
Checkpoint: /path/to/checkpoint.md
```

- [ ] Step 3: Gateway/TUI should include checkpoint path only when available.

- [ ] Step 4: Run tests.

```bash
pytest tests/cli/test_manual_compress.py tests/gateway/test_compress_command.py tests/gateway/test_compress_focus.py -q
```

## Task 7: Add probe-style preservation tests

**Files:**
- Create or modify: `tests/run_agent/test_compression_checkpoints.py`
- Modify: `tests/agent/test_context_compressor.py`

- [ ] Step 1: Build a synthetic long coding transcript.

Include:

- user goal
- decision
- full file path
- terminal command and failing output
- unresolved problem
- next action
- memory candidate
- thing safe to forget

- [ ] Step 2: Compress with a fake summary model response.

Assert that checkpoint and injected summary preserve:

- exact file path
- exact command
- exact error line
- latest unfulfilled user ask
- no fake secret values

- [ ] Step 3: Add regression for latest user message not being swallowed.

This complements existing boundary tests and proves checkpoint content still has the active task even if the injected summary changes later.

## Task 8: Documentation and config update

**Files:**
- Modify whichever existing Hermes docs currently document `compression.threshold`.
- If no obvious docs file exists, update inline config comments/help output only; do not create a new top-level doc without confirmation.

- [ ] Step 1: Search docs for `compression.threshold`.

```bash
cd /Users/liebe/.hermes/hermes-agent
rg "compression:|compression.threshold|threshold" website docs hermes_cli agent tests -g'*.md' -g'*.py' -g'*.yaml'
```

- [ ] Step 2: Document pressure bands and checkpoint directory.

- [ ] Step 3: Add a note that checkpoint artifacts are redacted but still local operational records, not public logs.

## Task 9: Verification

Run targeted first:

```bash
cd /Users/liebe/.hermes/hermes-agent
source venv/bin/activate
pytest tests/agent/test_context_pressure.py tests/agent/test_context_checkpoint.py tests/run_agent/test_compression_checkpoints.py -q
pytest tests/agent/test_context_compressor.py tests/run_agent/test_compression_persistence.py tests/run_agent/test_compression_boundary.py -q
pytest tests/cli/test_manual_compress.py tests/gateway/test_compress_command.py tests/gateway/test_compress_focus.py -q
```

Then run broader test script:

```bash
scripts/run_tests.sh
```

If full suite is too slow, run the relevant context/gateway/CLI subset and explicitly report that full suite was not run.

## Risks and tradeoffs

- Raising default threshold from 50% to 73.5% increases usable context but reduces recovery margin. Mitigate with soft/emergency bands and preflight compression.
- Checkpoint files may contain sensitive project details even after redaction. Keep under `HERMES_HOME`, do not sync to Obsidian by default, and apply redaction twice.
- Do not overfit to GPT-5.5. Use ratios and config overrides.
- Do not make checkpoint persistence required for compression; a filesystem error should not block the safety mechanism.
- Avoid adding a second memory system. Checkpoints are operational recovery artifacts, not durable knowledge. Stable preferences still go to memory; team knowledge still goes to 2brain/docs.

## Definition of done

- 272K context resolves to approximately 168K / 200K / 220K / 235K pressure bands.
- Auto compression uses the normal threshold unless explicit config overrides it.
- Pre-compaction checkpoint file exists before the old middle messages are replaced.
- Successful compression enriches the checkpoint with new session id, post-token estimate, and summary metadata.
- Manual `/compress`, gateway `/compress`, and TUI compression can surface checkpoint path.
- Tests prove file paths, commands, errors, latest ask, decisions, and unresolved problems survive.
- No secrets are written to checkpoint output in test fixtures.
- Relevant tests pass with evidence.
