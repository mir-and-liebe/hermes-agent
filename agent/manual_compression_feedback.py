"""User-facing summaries for manual compression commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence


def summarize_manual_compression(
    before_messages: Sequence[dict[str, Any]],
    after_messages: Sequence[dict[str, Any]],
    before_tokens: int,
    after_tokens: int,
    checkpoint_path: str | None = None,
    expose_checkpoint_path: bool = False,
) -> dict[str, Any]:
    """Return consistent user-facing feedback for manual compression."""
    before_count = len(before_messages)
    after_count = len(after_messages)
    noop = list(after_messages) == list(before_messages)

    if noop:
        headline = f"No changes from compression: {before_count} messages"
        if after_tokens == before_tokens:
            token_line = (
                f"Approx request size: ~{before_tokens:,} tokens (unchanged)"
            )
        else:
            token_line = (
                f"Approx request size: ~{before_tokens:,} → "
                f"~{after_tokens:,} tokens"
            )
    else:
        headline = f"Compressed: {before_count} → {after_count} messages"
        token_line = (
            f"Approx request size: ~{before_tokens:,} → "
            f"~{after_tokens:,} tokens"
        )

    note = None
    if not noop and after_count < before_count and after_tokens > before_tokens:
        note = (
            "Note: fewer messages can still raise this estimate when "
            "compression rewrites the transcript into denser summaries."
        )

    checkpoint_name = Path(checkpoint_path).name if checkpoint_path else None

    return {
        "noop": noop,
        "headline": headline,
        "token_line": token_line,
        "note": note,
        "checkpoint_saved": bool(checkpoint_path),
        "checkpoint_name": checkpoint_name,
        "checkpoint_path": checkpoint_path if expose_checkpoint_path else None,
    }
