"""Human-readable context compaction checkpoints.

Checkpoints are local operational recovery artifacts. They are intentionally
rendered before compression so a useful breadcrumb exists even if summary
creation or session rotation fails.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from agent.redact import redact_sensitive_text


@dataclass(frozen=True)
class ContextCheckpointMetadata:
    """Metadata rendered into a context compaction checkpoint."""

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


@dataclass(frozen=True)
class _Artifacts:
    current_goal: str
    decisions: tuple[str, ...]
    constraints: tuple[str, ...]
    changed_files: tuple[str, ...]
    read_files: tuple[str, ...]
    commands: tuple[str, ...]
    errors: tuple[str, ...]
    unresolved: tuple[str, ...]
    next_actions: tuple[str, ...]
    preserve_memory: tuple[str, ...]
    safe_forget: tuple[str, ...]
    raw_index: tuple[str, ...]


def _safe_text(value: object) -> str:
    return redact_sensitive_text(str(value or ""), force=True).strip()


def _format_int(value: int | None) -> str:
    return "pending" if value is None else f"{value:,}"


def _message_content(message: Mapping[str, object]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return _safe_text(content)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return _safe_text("\n".join(parts))
    return _safe_text(content)


def _parse_json_object(raw: object) -> dict[str, object]:
    if isinstance(raw, dict):
        return dict(raw)
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _tool_call_name_and_args(tool_call: object) -> tuple[str, dict[str, object]]:
    if not isinstance(tool_call, Mapping):
        return "unknown", {}
    function = tool_call.get("function")
    if not isinstance(function, Mapping):
        return "unknown", {}
    name = str(function.get("name") or "unknown")
    args = _parse_json_object(function.get("arguments"))
    return name, args


def _tool_call_id(tool_call: object) -> str:
    if not isinstance(tool_call, Mapping):
        return ""
    return str(tool_call.get("id") or tool_call.get("call_id") or "")


def _unique(items: Iterable[str], *, limit: int | None = None) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        clean = _safe_text(item)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
        if limit is not None and len(result) >= limit:
            break
    return tuple(result)


def _looks_like_error(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in ("error", "traceback", "failed", "exception", "exit_code\":1", "exit_code: 1"))


def _first_error_lines(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines and text:
        lines = [text]
    selected: list[str] = []
    for line in lines:
        if _looks_like_error(line):
            selected.append(line[:500])
        if len(selected) >= 5:
            break
    if selected:
        return selected
    return [line[:500] for line in lines[:2]]


def _extract_artifacts(messages: Sequence[Mapping[str, object]]) -> _Artifacts:
    latest_user = ""
    decisions: list[str] = []
    constraints: list[str] = []
    changed_files: list[str] = []
    read_files: list[str] = []
    commands: list[str] = []
    errors: list[str] = []
    unresolved: list[str] = []
    next_actions: list[str] = []
    preserve_memory: list[str] = []
    safe_forget: list[str] = []
    raw_index: list[str] = []
    call_id_to_tool: dict[str, str] = {}

    for message in messages:
        role = str(message.get("role") or "")
        content = _message_content(message)
        lowered = content.lower()

        if role == "user" and content:
            latest_user = content
            raw_index.append(f"user_message: {content[:500]}")
            if lowered.startswith("next") or "next:" in lowered:
                next_actions.append(content)

        if role == "assistant" and content:
            if "decided" in lowered or "decision" in lowered:
                decisions.append(content[:500])
            if "constraint" in lowered or "must" in lowered or "preserve" in lowered:
                constraints.append(content[:500])
            if "blocked" in lowered or "unresolved" in lowered:
                unresolved.append(content[:500])

        if role == "tool" and content and _looks_like_error(content):
            errors.extend(_first_error_lines(content))

        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            for tool_call in tool_calls:
                call_id = _tool_call_id(tool_call)
                name, args = _tool_call_name_and_args(tool_call)
                if call_id:
                    call_id_to_tool[call_id] = name
                raw_index.append(f"tool_call {name}: {_safe_text(json.dumps(args, ensure_ascii=False, sort_keys=True))}")

                path = args.get("path")
                if isinstance(path, str) and path:
                    if name in {"write_file", "patch"}:
                        changed_files.append(path)
                    elif name in {"read_file", "search_files"}:
                        read_files.append(path)
                    else:
                        read_files.append(path)

                command = args.get("command")
                if name == "terminal" and isinstance(command, str) and command:
                    commands.append(command)

                if name == "memory":
                    memory_content = args.get("content")
                    if isinstance(memory_content, str):
                        preserve_memory.append(memory_content)

        if role == "tool":
            tool_call_id = str(message.get("tool_call_id") or "")
            tool_name = call_id_to_tool.get(tool_call_id, "tool")
            if tool_call_id:
                raw_index.append(f"tool_result {tool_name}/{tool_call_id}: {content[:500]}")

    if latest_user and not next_actions:
        next_actions.append(latest_user)

    if not safe_forget:
        safe_forget.append("Raw historical prose not tied to active files, decisions, commands, errors, or user asks.")

    return _Artifacts(
        current_goal=latest_user or "Unknown from compacted messages.",
        decisions=_unique(decisions),
        constraints=_unique(constraints),
        changed_files=_unique(changed_files),
        read_files=_unique(read_files),
        commands=_unique(commands),
        errors=_unique(errors),
        unresolved=_unique(unresolved),
        next_actions=_unique(next_actions, limit=3),
        preserve_memory=_unique(preserve_memory),
        safe_forget=_unique(safe_forget),
        raw_index=_unique(raw_index),
    )


def _render_bullets(items: Sequence[str], empty: str = "None captured.") -> str:
    if not items:
        return empty
    return "\n".join(f"- {item}" for item in items)


def render_context_checkpoint(
    messages: Sequence[Mapping[str, object]],
    metadata: ContextCheckpointMetadata,
    *,
    summary_text: str | None = None,
    new_session_id: str | None = None,
    estimated_tokens_after: int | None = None,
) -> str:
    """Render a redacted markdown context compaction checkpoint."""
    artifacts = _extract_artifacts(messages)
    summary = _safe_text(summary_text) if summary_text else "pending until compression completes"
    parent = metadata.parent_session_id or "none"
    session_after = new_session_id or "pending"

    sections = [
        "# Context Compaction Checkpoint",
        "",
        "## Metadata",
        f"- Session ID: {_safe_text(metadata.session_id)}",
        f"- Parent session ID: {_safe_text(parent)}",
        f"- Platform: {_safe_text(metadata.platform)}",
        f"- Model: {_safe_text(metadata.model)}",
        f"- Provider: {_safe_text(metadata.provider)}",
        f"- Context window: {_format_int(metadata.context_length)}",
        f"- Estimated request tokens before: {_format_int(metadata.estimated_tokens_before)}",
        f"- Pressure band: {_safe_text(metadata.pressure_band)}",
        f"- Compression reason: {_safe_text(metadata.compression_reason)}",
        f"- Checkpoint created at: {_safe_text(metadata.created_at_iso)}",
        f"- New session ID: {_safe_text(session_after)}",
        f"- Estimated request tokens after: {_format_int(estimated_tokens_after)}",
        "",
        "## Current Goal",
        artifacts.current_goal,
        "",
        "## Decisions Made",
        _render_bullets(artifacts.decisions),
        "",
        "## Important Constraints",
        _render_bullets(artifacts.constraints),
        "",
        "## Files Changed",
        _render_bullets(artifacts.changed_files),
        "",
        "## Files Read / Inspected",
        _render_bullets(artifacts.read_files),
        "",
        "## Commands Run",
        _render_bullets(artifacts.commands),
        "",
        "## Errors Encountered",
        _render_bullets(artifacts.errors),
        "",
        "## Current Unresolved Problems",
        _render_bullets(artifacts.unresolved),
        "",
        "## Next 3 Actions",
        _render_bullets(artifacts.next_actions),
        "",
        "## Things To Preserve In Memory",
        _render_bullets(artifacts.preserve_memory),
        "",
        "## Things Safe To Forget",
        _render_bullets(artifacts.safe_forget),
        "",
        "## Raw Tool Artifact Index",
        _render_bullets(artifacts.raw_index),
        "",
        "## Compression Summary",
        summary,
        "",
    ]
    return redact_sensitive_text("\n".join(sections), force=True)


def _safe_path_part(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned.strip("_") or "unknown"


def _safe_checkpoint_dir(value: str) -> Path:
    """Return a safe relative checkpoint directory under Hermes home."""
    raw = Path(value or "context-checkpoints")
    if raw.is_absolute() or ".." in raw.parts:
        raise ValueError("compression.checkpoint_dir must be a relative path under HERMES_HOME")
    safe_parts = [_safe_path_part(part) for part in raw.parts if part not in ("", ".")]
    return Path(*safe_parts) if safe_parts else Path("context-checkpoints")


def write_context_checkpoint(
    *,
    hermes_home: Path,
    checkpoint_dir: str,
    session_id: str,
    reason: str,
    content: str,
) -> Path:
    """Write a checkpoint into a session-scoped local directory."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    session_part = _safe_path_part(session_id)
    reason_part = _safe_path_part(reason)
    directory = hermes_home / _safe_checkpoint_dir(checkpoint_dir) / session_part
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{timestamp}_{uuid.uuid4().hex[:8]}_{reason_part}.md"
    path.write_text(redact_sensitive_text(content, force=True), encoding="utf-8")
    return path
