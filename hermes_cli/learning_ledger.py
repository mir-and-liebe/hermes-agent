"""Learning ledger: read-only index of how Hermes has grown for this profile."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home


@dataclass
class LedgerItem:
    type: str
    name: str
    summary: str
    source: str
    count: int = 0
    last_used_at: float | None = None
    learned_at: float | None = None


def build_learning_ledger(db: Any = None, *, limit: int = 80) -> dict[str, Any]:
    """Build a compact, read-only ledger from existing Hermes artifacts."""
    items = [
        *_memory_items(),
        *_skill_items(),
        *_tool_usage_items(db),
        *_integration_items(),
    ]
    items.sort(
        key=lambda i: (i.last_used_at or i.learned_at or 0, i.type, i.name),
        reverse=True,
    )

    counts: dict[str, int] = {}
    for item in items:
        counts[item.type] = counts.get(item.type, 0) + 1

    return {
        "generated_at": time.time(),
        "home": str(get_hermes_home()),
        "counts": counts,
        "items": [asdict(item) for item in items[: max(1, limit)]],
        "total": len(items),
    }


def _memory_items() -> list[LedgerItem]:
    try:
        from tools.memory_tool import MemoryStore, get_memory_dir

        mem_dir = get_memory_dir()
        pairs = [
            ("memory", "MEMORY.md", "agent note"),
            ("user", "USER.md", "user profile"),
        ]
        items: list[LedgerItem] = []
        for item_type, filename, label in pairs:
            path = mem_dir / filename
            for idx, entry in enumerate(MemoryStore._read_file(path), 1):
                items.append(
                    LedgerItem(
                        type=item_type,
                        name=f"{label} {idx}",
                        summary=_one_line(entry),
                        source=str(path),
                        learned_at=_mtime(path),
                    )
                )
        return items
    except Exception:
        return []


def _skill_items() -> list[LedgerItem]:
    try:
        from tools.skills_tool import _find_all_skills

        return [
            LedgerItem(
                type="skill",
                name=str(skill.get("name") or "unnamed"),
                summary=str(skill.get("description") or "No description."),
                source=str(skill.get("category") or "general"),
            )
            for skill in _find_all_skills()
        ]
    except Exception:
        return []


def _tool_usage_items(db: Any) -> list[LedgerItem]:
    if db is None or not getattr(db, "_conn", None):
        return []

    usage: dict[tuple[str, str], LedgerItem] = {}

    def bump(item_type: str, name: str, summary: str, ts: float | None):
        key = (item_type, name)
        item = usage.get(key)
        if not item:
            item = usage[key] = LedgerItem(
                type=item_type,
                name=name,
                summary=summary,
                source="state.db",
            )
        item.count += 1
        if ts and (not item.last_used_at or ts > item.last_used_at):
            item.last_used_at = ts

    try:
        with db._lock:
            rows = db._conn.execute(
                """
                SELECT role, content, tool_calls, tool_name, timestamp
                FROM messages
                WHERE tool_name IS NOT NULL OR tool_calls IS NOT NULL
                ORDER BY timestamp DESC
                LIMIT 5000
                """
            ).fetchall()
    except Exception:
        return []

    for row in rows:
        ts = _float(row["timestamp"])
        tool_name = row["tool_name"]
        content = row["content"] or ""
        if tool_name == "memory":
            target = _json(content).get("target") or "memory"
            bump(str(target), f"{target} writes", "Durable memory updates", ts)
        elif tool_name == "session_search":
            bump("recall", "session_search", "Past conversations recalled", ts)
        elif tool_name in {"skill_view", "skill_manage"}:
            data = _json(content)
            name = str(data.get("name") or data.get("skill") or tool_name)
            bump("skill-use", name, "Skill loaded or managed", ts)

        for call in _tool_calls(row["tool_calls"]):
            name, args = call
            if name == "session_search":
                bump("recall", "session_search", "Past conversations recalled", ts)
            elif name in {"skill_view", "skill_manage"}:
                skill_name = str(
                    args.get("name") or args.get("skill") or args.get("query") or name
                )
                bump("skill-use", skill_name, "Skill requested by the agent", ts)
            elif name == "memory":
                target = str(args.get("target") or "memory")
                bump(target, f"{target} writes", "Durable memory updates", ts)

    return list(usage.values())


def _integration_items() -> list[LedgerItem]:
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
    except Exception:
        return []

    items: list[LedgerItem] = []
    provider = ((cfg.get("memory") or {}) if isinstance(cfg, dict) else {}).get(
        "provider"
    )
    if provider:
        items.append(
            LedgerItem(
                type="integration",
                name=f"memory:{provider}",
                summary="External memory provider is configured",
                source="config.yaml",
            )
        )

    for server in (
        sorted(((cfg.get("mcp") or {}).get("servers") or {}).keys())
        if isinstance(cfg, dict)
        else []
    ):
        items.append(
            LedgerItem(
                type="integration",
                name=f"mcp:{server}",
                summary="MCP server is configured",
                source="config.yaml",
            )
        )

    return items


def _tool_calls(raw: str | None) -> list[tuple[str, dict[str, Any]]]:
    calls = _json(raw)
    if not isinstance(calls, list):
        return []

    parsed = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") or {}
        name = call.get("name") or fn.get("name")
        args = fn.get("arguments") or call.get("arguments") or call.get("args") or {}
        if isinstance(args, str):
            args = _json(args)
        if name:
            parsed.append((str(name), args if isinstance(args, dict) else {}))
    return parsed


def _json(raw: Any) -> Any:
    if not raw:
        return {}
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _one_line(text: str, *, max_len: int = 180) -> str:
    line = " ".join(str(text).split())
    return line[: max_len - 1] + "…" if len(line) > max_len else line
