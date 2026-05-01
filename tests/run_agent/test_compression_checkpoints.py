from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest


class _FakeCompressor:
    context_length = 272_000
    threshold_tokens = 199_920
    compression_count = 1
    last_summary_text = "## Active Task\nContinue from checkpoint."
    last_summary_hash = "abc123def456"
    last_summary_fallback_used = False
    last_prompt_tokens = 0
    last_completion_tokens = 0

    def __init__(self) -> None:
        self.called_after_checkpoint = False

    def compress(self, messages, current_tokens=None, focus_topic=None):
        checkpoint_path = Path(os.environ["TEST_CHECKPOINT_PATH"])
        self.called_after_checkpoint = checkpoint_path.exists()
        return [
            messages[0],
            {"role": "assistant", "content": "[CONTEXT COMPACTION] summary"},
            messages[-1],
        ]

    def on_session_start(self, *args, **kwargs):
        return None


class _RaisingCompressor(_FakeCompressor):
    def compress(self, messages, current_tokens=None, focus_topic=None):
        checkpoint_path = Path(os.environ["TEST_CHECKPOINT_PATH"])
        assert checkpoint_path.exists()
        raise RuntimeError("compression exploded")


def _make_agent(tmp_path: Path):
    from run_agent import AIAgent

    with patch("run_agent.get_hermes_home", return_value=tmp_path):
        agent = AIAgent(
            api_key="test-key",
            base_url="https://openrouter.ai/api/v1",
            model="test/model",
            quiet_mode=True,
            session_id="checkpoint-session",
            skip_context_files=True,
            skip_memory=True,
            enabled_toolsets=[],
        )
    agent._cached_system_prompt = "system"
    return agent


def _messages() -> list[dict[str, object]]:
    return [
        {"role": "user", "content": "Fix auth retry bug."},
        {
            "role": "assistant",
            "content": "Decided to keep retry API stable.",
            "tool_calls": [
                {
                    "id": "call-test",
                    "function": {
                        "name": "terminal",
                        "arguments": '{"command":"pnpm test auth"}',
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-test", "content": '{"exit_code":1,"output":"Error: timeout"}'},
        {"role": "user", "content": "Next: write regression test first."},
    ]


def test_compress_context_writes_checkpoint_before_compressor_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _make_agent(tmp_path)
    fake = _FakeCompressor()
    agent.context_compressor = fake

    monkeypatch.setenv(
        "TEST_CHECKPOINT_PATH",
        str(tmp_path / "context-checkpoints" / "checkpoint-session" / "placeholder.md"),
    )

    def remember_checkpoint(path: str) -> None:
        monkeypatch.setenv("TEST_CHECKPOINT_PATH", path)

    agent._on_compression_checkpoint_written_for_test = remember_checkpoint

    compressed, _ = agent._compress_context(_messages(), "system", approx_tokens=201_000)

    assert fake.called_after_checkpoint is True
    assert len(compressed) == 3
    checkpoint_path = Path(agent._last_compression_checkpoint_path)
    assert checkpoint_path.exists()
    content = checkpoint_path.read_text(encoding="utf-8")
    assert "Fix auth retry bug." in content
    assert "pnpm test auth" in content
    assert "Error: timeout" in content
    assert "## Active Task" in content
    assert "Continue from checkpoint." in content
    assert "- Estimated request tokens after:" in content


def test_compress_context_clears_stale_checkpoint_path_when_checkpoint_write_fails(tmp_path: Path) -> None:
    agent = _make_agent(tmp_path)

    class _CompressorWithoutCheckpointAssertion(_FakeCompressor):
        def compress(self, messages, current_tokens=None, focus_topic=None):
            return [messages[0], {"role": "assistant", "content": "summary"}, messages[-1]]

    agent.context_compressor = _CompressorWithoutCheckpointAssertion()
    agent._last_compression_checkpoint_path = "/tmp/old-checkpoint.md"

    with patch("run_agent.write_context_checkpoint", side_effect=OSError("disk full")):
        agent._compress_context(_messages(), "system", approx_tokens=201_000)

    assert agent._last_compression_checkpoint_path is None


def test_compress_context_keeps_pre_checkpoint_when_compression_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _make_agent(tmp_path)
    agent.context_compressor = _RaisingCompressor()

    def remember_checkpoint(path: str) -> None:
        monkeypatch.setenv("TEST_CHECKPOINT_PATH", path)

    agent._on_compression_checkpoint_written_for_test = remember_checkpoint

    with pytest.raises(RuntimeError, match="compression exploded"):
        agent._compress_context(_messages(), "system", approx_tokens=201_000)

    checkpoint_path = Path(agent._last_compression_checkpoint_path)
    assert checkpoint_path.exists()
    content = checkpoint_path.read_text(encoding="utf-8")
    assert "pending until compression completes" in content
    assert "Fix auth retry bug." in content


def test_compress_context_continues_when_checkpoint_enrichment_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _make_agent(tmp_path)
    fake = _FakeCompressor()
    agent.context_compressor = fake

    original_write_text = Path.write_text

    def fail_only_enrichment(self: Path, data: str, *args, **kwargs):
        if "Continue from checkpoint." in data:
            raise OSError("checkpoint disk became read-only")
        return original_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_only_enrichment)

    def remember_checkpoint(path: str) -> None:
        monkeypatch.setenv("TEST_CHECKPOINT_PATH", path)

    agent._on_compression_checkpoint_written_for_test = remember_checkpoint

    compressed, _ = agent._compress_context(_messages(), "system", approx_tokens=201_000)

    assert len(compressed) == 3
    checkpoint_path = Path(agent._last_compression_checkpoint_path)
    assert checkpoint_path.exists()
    content = checkpoint_path.read_text(encoding="utf-8")
    assert "pending until compression completes" in content
