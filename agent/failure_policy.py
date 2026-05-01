"""Central failure visibility helpers for Hermes.

This module gives non-fatal failure paths one canonical surface: structured,
redacted logging plus lightweight process-local counters. It intentionally has
minimal dependencies so it can be used from agent, tools, gateway, cron, and CLI
code without import cycles.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import logging
from pathlib import Path
import re
from typing import Literal, Mapping

FailureSeverity = Literal[
    "critical",
    "recoverable",
    "degraded",
    "best_effort",
    "expected_absent",
]

_SAFE_METADATA_VALUE = str | int | float | bool | None

logger = logging.getLogger("hermes.failure")
_counts: Counter[str] = Counter()

_SECRET_FALLBACK_RE = re.compile(
    r"(?i)(sk-[A-Za-z0-9_.-]{3,}|gh[pousr]_[A-Za-z0-9_]{6,}|github_pat_[A-Za-z0-9_]{6,}|"
    r"xox[baprs]-[A-Za-z0-9-]{6,}|AIza[A-Za-z0-9_-]{10,}|hf_[A-Za-z0-9]{6,}|"
    r"[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API[_-]?KEY)[A-Z0-9_]*\s*=\s*\S+)"
)


@dataclass(frozen=True)
class FailureEvent:
    component: str
    operation: str
    severity: FailureSeverity
    message: str
    exception_type: str | None = None
    session_id: str | None = None
    task_id: str | None = None
    metadata: Mapping[str, _SAFE_METADATA_VALUE] = field(default_factory=dict)


def _fallback_redact(text: str) -> str:
    return _SECRET_FALLBACK_RE.sub("[REDACTED]", text)


def _redact(text: object) -> str:
    raw = str(text)
    try:
        from agent.redact import redact_sensitive_text

        return _fallback_redact(redact_sensitive_text(raw))
    except Exception:
        setattr(_redact, "failed", True)
        return _fallback_redact(raw)


def _sanitize_metadata(metadata: Mapping[str, object] | None) -> dict[str, _SAFE_METADATA_VALUE]:
    sanitized: dict[str, _SAFE_METADATA_VALUE] = {}
    for key, value in (metadata or {}).items():
        safe_key = _redact(key)
        if isinstance(value, (int, float, bool)) or value is None:
            sanitized[safe_key] = value
        else:
            sanitized[safe_key] = _redact(value)
    return sanitized


def _level_for(severity: FailureSeverity) -> int:
    if severity == "critical":
        return logging.ERROR
    if severity in {"recoverable", "degraded"}:
        return logging.WARNING
    return logging.DEBUG


def _counter_key(component: str, operation: str, severity: FailureSeverity) -> str:
    return f"{component}.{operation}.{severity}"


def get_failure_counts() -> dict[str, int]:
    """Return process-local failure counts keyed by component.operation.severity."""

    return dict(_counts)


def reset_failure_counts_for_tests() -> None:
    """Clear process-local counters. Intended for tests."""

    _counts.clear()


def get_recent_failure_lines(*, limit: int = 5, log_dir: Path | str | None = None) -> list[str]:
    """Return newest structured failure_event log lines for operator diagnostics."""

    if limit <= 0:
        return []
    try:
        if log_dir is None:
            from hermes_constants import get_hermes_home

            logs_path = get_hermes_home() / "logs"
        else:
            logs_path = Path(log_dir)
        candidates = [logs_path / "errors.log", logs_path / "agent.log", logs_path / "gateway.log"]
        lines: list[str] = []
        for path in candidates:
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue
            for line in reversed(content):
                if "failure_event" in line:
                    lines.append(_fallback_redact(line.strip()))
                    if len(lines) >= limit:
                        return lines
        return lines
    except Exception:
        return []


def report_failure(
    *,
    component: str,
    operation: str,
    severity: FailureSeverity,
    message: str,
    exc: BaseException | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    metadata: Mapping[str, object] | None = None,
    log_stack: bool = False,
) -> FailureEvent:
    """Record a non-fatal or fatal failure without raising from the reporter."""

    try:
        setattr(_redact, "failed", False)
        safe_component = _redact(component)
        safe_operation = _redact(operation)
        safe_message = _redact(message)
        safe_session_id = _redact(session_id) if session_id else None
        safe_task_id = _redact(task_id) if task_id else None
        safe_metadata = _sanitize_metadata(metadata)
        exception_type = type(exc).__name__ if exc is not None else None
        exception_message = _redact(exc) if exc is not None else ""

        event = FailureEvent(
            component=safe_component,
            operation=safe_operation,
            severity=severity,
            message=safe_message,
            exception_type=exception_type,
            session_id=safe_session_id,
            task_id=safe_task_id,
            metadata=safe_metadata,
        )
        _counts[_counter_key(safe_component, safe_operation, severity)] += 1

        parts = [
            "failure_event",
            f"severity={severity}",
            f"component={safe_component}",
            f"operation={safe_operation}",
            f"message={safe_message}",
        ]
        if exception_type:
            parts.append(f"exception_type={exception_type}")
        if exception_message:
            parts.append(f"exception={exception_message}")
        if safe_session_id:
            parts.append(f"session_id={safe_session_id}")
        if safe_task_id:
            parts.append(f"task_id={safe_task_id}")
        if getattr(_redact, "failed", False):
            parts.append("redaction_failed=true")
        for key in sorted(safe_metadata):
            parts.append(f"{key}={safe_metadata[key]}")

        logger.log(_level_for(severity), " ".join(parts), exc_info=exc if log_stack else None)
        return event
    except Exception as reporter_exc:  # pragma: no cover - last ditch safety
        try:
            logger.error(
                "failure_event_reporter_failed component=%s operation=%s exception_type=%s",
                _fallback_redact(component),
                _fallback_redact(operation),
                type(reporter_exc).__name__,
            )
        except Exception:
            pass
        return FailureEvent(
            component=_fallback_redact(component),
            operation=_fallback_redact(operation),
            severity=severity,
            message="failure reporter failed",
            exception_type=type(exc).__name__ if exc is not None else None,
        )


def best_effort(
    *,
    component: str,
    operation: str,
    exc: BaseException | None,
    reason: str,
    log_stack: bool = False,
    **metadata: object,
) -> FailureEvent:
    if not reason:
        raise ValueError("best_effort failure reporting requires a non-empty reason")
    return report_failure(
        component=component,
        operation=operation,
        severity="best_effort",
        message=reason,
        exc=exc,
        metadata=metadata,
        log_stack=log_stack,
    )


def degraded(
    *,
    component: str,
    operation: str,
    exc: BaseException | None,
    user_visible_effect: str,
    log_stack: bool = False,
    **metadata: object,
) -> FailureEvent:
    if not user_visible_effect:
        raise ValueError("degraded failure reporting requires a non-empty user_visible_effect")
    return report_failure(
        component=component,
        operation=operation,
        severity="degraded",
        message=user_visible_effect,
        exc=exc,
        metadata=metadata,
        log_stack=log_stack,
    )


def critical(
    *,
    component: str,
    operation: str,
    exc: BaseException | None,
    message: str | None = None,
    log_stack: bool = False,
    **metadata: object,
) -> FailureEvent:
    return report_failure(
        component=component,
        operation=operation,
        severity="critical",
        message=message or "critical operation failed",
        exc=exc,
        metadata=metadata,
        log_stack=log_stack,
    )
