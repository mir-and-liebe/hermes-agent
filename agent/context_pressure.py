"""Context pressure bands for compression trigger policy.

This module is intentionally pure: it converts compression config plus a model
context window into explicit token thresholds that callers can log, display, and
compare against without knowing config details.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping

ContextPressureBand = Literal["normal", "soft", "compaction", "emergency", "hard"]

_DEFAULT_SOFT_RATIO = 0.62
_DEFAULT_NORMAL_RATIO = 0.735
_DEFAULT_EMERGENCY_RATIO = 0.81
_DEFAULT_HARD_RATIO = 0.865


@dataclass(frozen=True)
class ContextPressureBands:
    """Resolved token thresholds for context-window pressure."""

    context_length: int
    soft_warning_tokens: int
    normal_compaction_tokens: int
    emergency_compaction_tokens: int
    hard_limit_tokens: int


def _coerce_threshold(value: object, default_ratio: float, context_length: int) -> int:
    """Return a token threshold from either a ratio or an absolute token value."""
    numeric: float
    try:
        numeric = float(value) if value is not None else default_ratio
    except (TypeError, ValueError):
        numeric = default_ratio

    if numeric <= 0:
        numeric = default_ratio

    if numeric <= 1:
        return int(context_length * numeric)
    return int(numeric)


def resolve_context_pressure_bands(
    *,
    context_length: int,
    config: Mapping[str, object],
) -> ContextPressureBands:
    """Resolve soft/normal/emergency/hard context pressure thresholds.

    Existing ``compression.threshold`` remains the normal compaction threshold.
    Other values default to ratios tuned for large-context coding models, but all
    values may be specified either as ratios (0 < value <= 1) or absolute token
    counts (value > 1).
    """
    if context_length <= 0:
        raise ValueError("context_length must be positive")

    normal = _coerce_threshold(
        config.get("threshold"), _DEFAULT_NORMAL_RATIO, context_length
    )
    soft = _coerce_threshold(
        config.get("soft_warning_threshold"), _DEFAULT_SOFT_RATIO, context_length
    )
    emergency = _coerce_threshold(
        config.get("emergency_threshold"), _DEFAULT_EMERGENCY_RATIO, context_length
    )
    hard = _coerce_threshold(
        config.get("hard_limit_threshold"), _DEFAULT_HARD_RATIO, context_length
    )

    if context_length >= 4:
        normal = max(2, min(normal, context_length - 2))
        soft = max(1, min(soft, normal - 1))
        emergency = max(normal + 1, min(emergency, context_length - 1))
        hard = max(emergency + 1, min(hard, context_length))
    else:
        # Tiny test windows cannot express four strictly ordered bands. Keep the
        # thresholds inside the available context window and let classification
        # collapse adjacent pressure states.
        normal = max(1, min(normal, context_length))
        soft = max(1, min(soft, normal))
        emergency = max(normal, min(emergency, context_length))
        hard = max(emergency, min(hard, context_length))

    return ContextPressureBands(
        context_length=context_length,
        soft_warning_tokens=soft,
        normal_compaction_tokens=normal,
        emergency_compaction_tokens=emergency,
        hard_limit_tokens=hard,
    )


def classify_context_pressure(
    prompt_tokens: int,
    bands: ContextPressureBands,
) -> ContextPressureBand:
    """Classify the current prompt size against resolved pressure bands."""
    if prompt_tokens >= bands.hard_limit_tokens:
        return "hard"
    if prompt_tokens >= bands.emergency_compaction_tokens:
        return "emergency"
    if prompt_tokens >= bands.normal_compaction_tokens:
        return "compaction"
    if prompt_tokens >= bands.soft_warning_tokens:
        return "soft"
    return "normal"
