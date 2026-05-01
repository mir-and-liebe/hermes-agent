import json
from pathlib import Path

from agent.context_checkpoint import (
    ContextCheckpointMetadata,
    render_context_checkpoint,
    write_context_checkpoint,
)


def _metadata() -> ContextCheckpointMetadata:
    return ContextCheckpointMetadata(
        session_id="session-1",
        parent_session_id="parent-1",
        platform="cli",
        model="gpt-5.5",
        provider="openai-codex",
        context_length=272_000,
        estimated_tokens_before=201_234,
        pressure_band="compaction",
        compression_reason="automatic",
        created_at_iso="2026-05-02T02:30:00Z",
    )


def _messages() -> list[dict[str, object]]:
    return [
        {"role": "user", "content": "Fix the auth retry bug and preserve this next action."},
        {
            "role": "assistant",
            "content": "Decided to preserve existing retry API and add explicit timeout errors.",
            "tool_calls": [
                {
                    "id": "call-read",
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"path": "/repo/src/auth.ts", "offset": 1}),
                    },
                },
                {
                    "id": "call-test",
                    "function": {
                        "name": "terminal",
                        "arguments": json.dumps({"command": "pnpm test auth -- --runInBand"}),
                    },
                },
                {
                    "id": "call-patch",
                    "function": {
                        "name": "patch",
                        "arguments": json.dumps({"path": "/repo/src/auth.ts", "mode": "replace"}),
                    },
                },
            ],
        },
        {"role": "tool", "tool_call_id": "call-read", "content": "read /repo/src/auth.ts"},
        {
            "role": "tool",
            "tool_call_id": "call-test",
            "content": '{"exit_code":1,"output":"Error: timeout after 30s\\nOPENAI_API_KEY=sk-testsecret1234567890"}',
        },
        {"role": "user", "content": "Next: add a regression test before changing code."},
    ]


def test_render_checkpoint_contains_required_sections_and_metadata() -> None:
    content = render_context_checkpoint(_messages(), _metadata())

    assert "# Context Compaction Checkpoint" in content
    assert "- Session ID: session-1" in content
    assert "- Parent session ID: parent-1" in content
    assert "- Model: gpt-5.5" in content
    assert "- Context window: 272,000" in content
    assert "- Estimated request tokens before: 201,234" in content
    assert "- Pressure band: compaction" in content
    assert "- New session ID: pending" in content
    assert content.count("## Current Goal") == 1
    assert content.count("## Decisions Made") == 1
    assert content.count("## Files Changed") == 1
    assert content.count("## Commands Run") == 1
    assert content.count("## Compression Summary") == 1


def test_render_checkpoint_extracts_artifacts_and_redacts_secrets() -> None:
    content = render_context_checkpoint(_messages(), _metadata())

    assert "Next: add a regression test before changing code." in content
    assert "/repo/src/auth.ts" in content
    assert "pnpm test auth -- --runInBand" in content
    assert "Error: timeout after 30s" in content
    assert "OPENAI_API_KEY" in content
    assert "sk-test" not in content
    assert "sk-tes" not in content
    assert "7890" not in content
    assert "***" in content


def test_render_checkpoint_can_be_enriched_after_compression() -> None:
    content = render_context_checkpoint(
        _messages(),
        _metadata(),
        summary_text="## Active Task\nFinish the auth retry fix.",
        new_session_id="session-2",
        estimated_tokens_after=51_000,
    )

    assert "- New session ID: session-2" in content
    assert "- Estimated request tokens after: 51,000" in content
    assert "## Active Task" in content
    assert "Finish the auth retry fix." in content


def test_write_context_checkpoint_creates_session_scoped_file(tmp_path: Path) -> None:
    path = write_context_checkpoint(
        hermes_home=tmp_path,
        checkpoint_dir="context-checkpoints",
        session_id="session/with unsafe chars",
        reason="automatic compaction",
        content="# checkpoint",
    )

    assert path.exists()
    assert path.read_text(encoding="utf-8") == "# checkpoint"
    assert path.parent == tmp_path / "context-checkpoints" / "session_with_unsafe_chars"
    assert path.name.endswith("_automatic_compaction.md")


def test_write_context_checkpoint_rejects_absolute_or_parent_dirs(tmp_path: Path) -> None:
    for unsafe_dir in ("/tmp/checkpoints", "../outside", "safe/../../outside", ".. ", " ..", "safe/.. /.. /outside"):
        try:
            write_context_checkpoint(
                hermes_home=tmp_path,
                checkpoint_dir=unsafe_dir,
                session_id="session",
                reason="manual",
                content="# checkpoint",
            )
        except ValueError as exc:
            assert "relative path under HERMES_HOME" in str(exc)
        else:  # pragma: no cover - assertion branch
            raise AssertionError(f"unsafe checkpoint_dir was accepted: {unsafe_dir}")


def test_write_context_checkpoint_does_not_overwrite_same_second(tmp_path: Path) -> None:
    first = write_context_checkpoint(
        hermes_home=tmp_path,
        checkpoint_dir="context-checkpoints",
        session_id="session",
        reason="manual",
        content="first checkpoint",
    )
    second = write_context_checkpoint(
        hermes_home=tmp_path,
        checkpoint_dir="context-checkpoints",
        session_id="session",
        reason="manual",
        content="second checkpoint",
    )

    assert first != second
    assert first.read_text(encoding="utf-8") == "first checkpoint"
    assert second.read_text(encoding="utf-8") == "second checkpoint"
