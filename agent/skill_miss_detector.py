"""Offline missed-skill detector for Hermes session history."""
from __future__ import annotations

import json
import re
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, TypedDict
from urllib.parse import quote

from hermes_constants import get_hermes_home

CLASSIFIER_VERSION = "missed-skill-detector-v1"
_CONTENT_JSON_PREFIX = "\x00json:"
DEFAULT_INCLUDE_SOURCES = frozenset({"cli", "tui", "telegram", "discord", "api", "web"})
SYNTHETIC_USER_PREFIXES = (
    "[CONTEXT COMPACTION",
    "[Your active task list was preserved across context compression]",
    "[IMPORTANT: You are running as a scheduled cron job]",
    "[SYSTEM: You are running as a scheduled cron job]",
    "[SYSTEM: The user has invoked the ",
    "[IMPORTANT: The user has invoked the ",
)
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{12,}"),
    re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*\S+"),
    re.compile(r"(?i)([\"']?(?:api[_-]?key|token|secret|password)[\"']?\s*[:=]\s*)[\"'][^\"']+[\"']"),
)


class SkillExpectation(TypedDict):
    name: str
    score: float
    reason: str
    matched_patterns: list[str]


class MissedSkillEvent(TypedDict):
    ts: float
    session_id: str
    message_id: int
    session_source: str
    user_excerpt: str
    expected_skills: list[SkillExpectation]
    observed_skill_calls: list[str]
    missed: bool
    classifier_version: str
    source: str


class MissedSkillSummary(TypedDict):
    generated_at: float
    window_days: int
    classifier_version: str
    total_turns_scored: int
    total_missed: int
    miss_rate: float
    top_missed_skills: list[dict[str, int | str]]
    events_path: str | None


class MissedSkillReport(TypedDict):
    summary: MissedSkillSummary
    events: list[MissedSkillEvent]


class ToolCall(TypedDict, total=False):
    name: str
    arguments: dict[str, object] | str
    function: dict[str, object]


class MessageRow(TypedDict):
    id: int
    session_id: str
    source: str
    role: str
    content: str | None
    tool_calls: str | None
    timestamp: float


@dataclass(frozen=True)
class SkillRule:
    name: str
    patterns: tuple[str, ...]
    reason: str
    score: float = 0.85


DEFAULT_RULES = (
    SkillRule(
        "linear",
        (r"\blinear\b", r"\bissue\b", r"\bticket\b", r"\bbacklog\b", r"\btriage\b"),
        "task/project tracking terms should route through Linear",
    ),
    SkillRule(
        "obsidian",
        (r"\bobsidian\b", r"\bvault\b", r"\b2brain\b", r"\bnote\b", r"\bknowledge\b", r"\bwiki\b"),
        "knowledge/vault terms should load the Obsidian workflow",
    ),
    SkillRule(
        "hermes-agent",
        (r"\bhermes\b", r"\bgateway\b", r"\bcron\b", r"\bprofile\b", r"\btoolset\b", r"\bprovider\b"),
        "Hermes configuration/troubleshooting should load hermes-agent",
    ),
    SkillRule(
        "systematic-debugging",
        (r"\bbug\b", r"\bfailure\b", r"\bfailing\b", r"\berror\b", r"\bdebug\b", r"\broot cause\b"),
        "bug/error work should start with systematic debugging",
        0.78,
    ),
    SkillRule(
        "verification-before-completion",
        (r"\bverify\b", r"\bverification\b", r"\bdone\b", r"\bcomplete\b", r"\bfixed\b", r"\bpassing\b"),
        "completion or verification claims need explicit evidence",
        0.76,
    ),
    SkillRule(
        "writing-plans",
        (r"\bplan\b", r"\bimplementation plan\b", r"\bspec\b", r"\broadmap\b"),
        "multi-step implementation planning should load writing-plans",
        0.74,
    ),
)


def _coerce_text(content: object) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        if content.startswith(_CONTENT_JSON_PREFIX):
            try:
                decoded = json.loads(content[len(_CONTENT_JSON_PREFIX):])
            except (json.JSONDecodeError, TypeError):
                return content
            return _extract_text(decoded)
        return content
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    if isinstance(content, (int, float, bool)):
        return str(content)
    return _extract_text(content)


def _extract_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = [_extract_text(item) for item in value]
        return " ".join(part for part in parts if part).strip()
    if isinstance(value, dict):
        type_value = value.get("type")
        text = value.get("text")
        if type_value == "text" and isinstance(text, str):
            return text
        if type_value in {"image_url", "input_image", "file", "input_file"}:
            return ""
        if isinstance(text, str):
            return text
        # Avoid turning image/file URLs into keyword-bearing user intent.
        parts = [
            _extract_text(item)
            for key, item in value.items()
            if key not in {"image_url", "input_image", "file", "url"}
        ]
        return " ".join(part for part in parts if part).strip()
    return str(value)


def _is_synthetic_user_text(text: str) -> bool:
    stripped = text.lstrip()
    return any(stripped.startswith(prefix) for prefix in SYNTHETIC_USER_PREFIXES)


def redact_excerpt(text: str, max_chars: int = 180) -> str:
    redacted = _coerce_text(text).replace("\n", " ").strip()
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    if len(redacted) > max_chars:
        return redacted[: max_chars - 1].rstrip() + "…"
    return redacted


def _parse_tool_calls(raw: str | None) -> list[ToolCall]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _tool_call_name(call: ToolCall) -> str:
    fn = call.get("function")
    if isinstance(fn, dict):
        return str(fn.get("name") or "")
    return str(call.get("name") or "")


def _tool_call_args(call: ToolCall) -> dict[str, object]:
    fn = call.get("function")
    args = fn.get("arguments") if isinstance(fn, dict) else call.get("arguments")
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _skill_name_variants(name: str) -> set[str]:
    stripped = name.strip()
    if not stripped:
        return set()
    variants = {stripped}
    for separator in ("/", ":"):
        if separator in stripped:
            variants.add(stripped.rsplit(separator, 1)[-1])
    return {variant for variant in variants if variant}


def observed_skill_loads(tool_calls: Iterable[ToolCall]) -> set[str]:
    loaded: set[str] = set()
    for call in tool_calls:
        if _tool_call_name(call).rsplit(".", 1)[-1] == "skill_view":
            name = _tool_call_args(call).get("name")
            if isinstance(name, str) and name.strip():
                loaded.add(name.strip())
    return loaded


def expected_skills_for_text(
    text: str,
    rules: Iterable[SkillRule] = DEFAULT_RULES,
) -> list[SkillExpectation]:
    searchable = _coerce_text(text)
    matches: list[SkillExpectation] = []
    for rule in rules:
        matched = [
            pattern
            for pattern in rule.patterns
            if re.search(pattern, searchable, flags=re.IGNORECASE)
        ]
        if matched:
            matches.append(
                {
                    "name": rule.name,
                    "score": round(min(0.95, rule.score + 0.03 * (len(matched) - 1)), 2),
                    "reason": rule.reason,
                    "matched_patterns": matched[:5],
                }
            )
    return sorted(matches, key=lambda item: item["score"], reverse=True)


def _load_skill_names(skills_root: Path) -> set[str]:
    names: set[str] = set()
    if not skills_root.exists():
        return names
    for skill_md in skills_root.rglob("SKILL.md"):
        names.update(_skill_name_variants(skill_md.parent.name))
        try:
            names.update(_skill_name_variants(str(skill_md.parent.relative_to(skills_root))))
        except ValueError:
            pass
    return names


def _validate_schema(conn: sqlite3.Connection) -> None:
    required = {
        "sessions": {"id", "source"},
        "messages": {"id", "session_id", "role", "content", "tool_calls", "timestamp"},
    }
    for table, columns in required.items():
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        if exists is None:
            raise RuntimeError(f"missing required table: {table}")
        present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        missing = sorted(columns - present)
        if missing:
            raise RuntimeError(f"missing required columns in {table}: {', '.join(missing)}")


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(db_path.resolve()), safe='/')}?mode=ro"
    try:
        return sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError as exc:
        raise RuntimeError(f"unable to open session database read-only: {db_path}") from exc


def _load_rows(conn: sqlite3.Connection, cutoff: float) -> list[MessageRow]:
    rows = conn.execute(
        """
        SELECT
            m.id AS id,
            m.session_id AS session_id,
            s.source AS source,
            m.role AS role,
            m.content AS content,
            m.tool_calls AS tool_calls,
            m.timestamp AS timestamp
        FROM messages m JOIN sessions s ON s.id = m.session_id
        WHERE m.timestamp >= ?
        ORDER BY m.session_id, m.id
        """,
        (cutoff,),
    )
    return [
        {
            "id": int(row["id"]),
            "session_id": str(row["session_id"]),
            "source": str(row["source"]),
            "role": str(row["role"]),
            "content": row["content"],
            "tool_calls": row["tool_calls"],
            "timestamp": float(row["timestamp"]),
        }
        for row in rows
    ]


def _append_miss_event(
    events: list[MissedSkillEvent],
    *,
    row: MessageRow,
    content: str,
    expected: list[SkillExpectation],
    loaded: set[str],
    known: set[str],
    canonical: set[str],
) -> None:
    loaded_names: set[str] = set()
    for name in loaded:
        loaded_names.update(_skill_name_variants(name))
    missed = [
        item
        for item in expected
        if item["name"] not in loaded_names
        and (item["name"] in known or item["name"] in canonical)
    ]
    if not missed:
        return
    events.append(
        {
            "ts": row["timestamp"],
            "session_id": row["session_id"],
            "message_id": row["id"],
            "session_source": row["source"],
            "user_excerpt": redact_excerpt(content),
            "expected_skills": missed,
            "observed_skill_calls": sorted(loaded),
            "missed": True,
            "classifier_version": CLASSIFIER_VERSION,
            "source": "offline-analyzer-v1",
        }
    )


def analyze_database(
    db_path: Path,
    *,
    skills_root: Path | None = None,
    days: int = 7,
    threshold: float = 0.75,
    include_sources: Iterable[str] | None = None,
) -> MissedSkillReport:
    cutoff = time.time() - days * 86400
    allowed_sources = set(include_sources) if include_sources is not None else set(DEFAULT_INCLUDE_SOURCES)
    with _connect_readonly(db_path) as conn:
        conn.row_factory = sqlite3.Row
        _validate_schema(conn)
        rows = _load_rows(conn, cutoff)

    known = _load_skill_names(skills_root or Path(get_hermes_home()) / "skills")
    canonical = {rule.name for rule in DEFAULT_RULES}
    events: list[MissedSkillEvent] = []
    turns = 0
    pending_row: MessageRow | None = None
    pending_content = ""
    pending_expected: list[SkillExpectation] = []
    pending_loaded: set[str] = set()
    current_session_id: str | None = None

    def flush_pending() -> None:
        if pending_row is None:
            return
        _append_miss_event(
            events,
            row=pending_row,
            content=pending_content,
            expected=pending_expected,
            loaded=pending_loaded,
            known=known,
            canonical=canonical,
        )

    def reset_pending() -> None:
        nonlocal pending_row, pending_content, pending_expected, pending_loaded
        pending_row = None
        pending_content = ""
        pending_expected = []
        pending_loaded = set()

    for row in rows:
        if current_session_id != row["session_id"]:
            flush_pending()
            reset_pending()
            current_session_id = row["session_id"]
        if row["source"] not in allowed_sources:
            continue
        if row["role"] == "user":
            flush_pending()
            reset_pending()

            content = _coerce_text(row["content"])
            if not content or _is_synthetic_user_text(content):
                continue
            turns += 1
            pending_row = row
            pending_content = content
            pending_expected = [
                item
                for item in expected_skills_for_text(content)
                if item["score"] >= threshold
            ]
            continue

        if pending_row is not None:
            pending_loaded.update(observed_skill_loads(_parse_tool_calls(row["tool_calls"])))

    flush_pending()

    top = Counter(item["name"] for event in events for item in event["expected_skills"])
    summary: MissedSkillSummary = {
        "generated_at": time.time(),
        "window_days": days,
        "classifier_version": CLASSIFIER_VERSION,
        "total_turns_scored": turns,
        "total_missed": len(events),
        "miss_rate": round(len(events) / max(1, turns), 4),
        "top_missed_skills": [
            {"name": name, "count": count} for name, count in top.most_common(20)
        ],
        "events_path": None,
    }
    return {"summary": summary, "events": events}


def write_reports(report: MissedSkillReport, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    events_path = output_dir / "missed-skill-events.jsonl"
    summary_path = output_dir / "missed-skill-summary.json"
    with events_path.open("w", encoding="utf-8") as fh:
        for event in report.get("events", []):
            fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    summary = dict(report.get("summary", {}))
    summary["events_path"] = str(events_path)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"events": events_path, "summary": summary_path}


def format_summary(summary: MissedSkillSummary) -> str:
    lines = [
        f"Missed skill detector ({summary.get('window_days')}d)",
        f"turns_scored={summary.get('total_turns_scored', 0)} "
        f"missed={summary.get('total_missed', 0)} "
        f"miss_rate={summary.get('miss_rate', 0)}",
    ]
    top = summary.get("top_missed_skills") or []
    lines.append(
        "top_missed="
        + (
            ", ".join(f"{item['name']}:{item['count']}" for item in top[:10])
            if top
            else "none"
        )
    )
    return "\n".join(lines)
