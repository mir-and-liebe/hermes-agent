import json
import sqlite3
import time
from pathlib import Path

import pytest

from agent.skill_miss_detector import (
    analyze_database,
    expected_skills_for_text,
    redact_excerpt,
    write_reports,
)


def _init_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL, started_at REAL NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE messages ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "session_id TEXT NOT NULL, "
        "role TEXT NOT NULL, "
        "content TEXT, "
        "tool_calls TEXT, "
        "timestamp REAL NOT NULL)"
    )
    return conn


def _skill_root(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    for name in [
        "linear",
        "obsidian",
        "hermes-agent",
        "systematic-debugging",
        "verification-before-completion",
        "writing-plans",
    ]:
        path = root / "test" / name
        path.mkdir(parents=True, exist_ok=True)
        (path / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {name}\n---\n",
            encoding="utf-8",
        )
    return root


def _insert_session(conn: sqlite3.Connection, session_id: str, source: str, started_at: float) -> None:
    conn.execute(
        "INSERT INTO sessions(id, source, started_at) VALUES (?, ?, ?)",
        (session_id, source, started_at),
    )


def _insert_message(
    conn: sqlite3.Connection,
    session_id: str,
    role: str,
    content: str | None,
    timestamp: float,
    tool_calls: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO messages(session_id, role, content, tool_calls, timestamp) "
        "VALUES (?, ?, ?, ?, ?)",
        (session_id, role, content, tool_calls, timestamp),
    )


def _skill_view_call(name: str) -> str:
    return json.dumps(
        [{"function": {"name": "skill_view", "arguments": json.dumps({"name": name})}}]
    )


def test_expected_skills_for_linear_and_obsidian_terms():
    expected = expected_skills_for_text(
        "Create a Linear issue and update the Obsidian vault note"
    )
    names = {item["name"] for item in expected}
    assert "linear" in names
    assert "obsidian" in names


def test_redact_excerpt_removes_secret_like_values():
    secret = "sk-" + "secret1234567890"
    redacted = redact_excerpt(f"token={secret} and password=hunter2 should not leak")
    assert "sk-secret" not in redacted
    assert "hunter2" not in redacted
    assert "[REDACTED]" in redacted


def test_redact_excerpt_removes_json_style_secret_values():
    api_key = "sk-" + "secret1234567890"
    slack_token = "xoxb-" + "secret-token-value"
    redacted = redact_excerpt(
        json.dumps({"api_key": api_key, "token": slack_token})
    )
    assert "sk-secret" not in redacted
    assert "xoxb-secret" not in redacted
    assert "[REDACTED]" in redacted


def test_analyze_database_reports_missed_skill(tmp_path):
    db_path = tmp_path / "state.db"
    conn = _init_db(db_path)
    now = time.time()
    _insert_session(conn, "s1", "cli", now)
    _insert_message(
        conn,
        "s1",
        "user",
        "Please create a Linear ticket for this bug",
        now,
    )
    conn.commit()
    conn.close()

    report = analyze_database(db_path, skills_root=_skill_root(tmp_path), days=1)

    assert report["summary"]["total_turns_scored"] == 1
    assert report["summary"]["total_missed"] == 1
    missed_names = {
        item["name"] for event in report["events"] for item in event["expected_skills"]
    }
    assert "linear" in missed_names


def test_analyze_database_suppresses_skill_when_loaded_in_turn(tmp_path):
    db_path = tmp_path / "state.db"
    conn = _init_db(db_path)
    now = time.time()
    _insert_session(conn, "s1", "cli", now)
    _insert_message(conn, "s1", "user", "Please create a Linear issue", now)
    _insert_message(conn, "s1", "assistant", "Loading skill", now + 1, _skill_view_call("linear"))
    conn.commit()
    conn.close()

    report = analyze_database(db_path, skills_root=_skill_root(tmp_path), days=1)

    assert report["summary"]["total_missed"] == 0
    assert report["events"] == []


def test_namespaced_skill_load_suppresses_canonical_expected_skill(tmp_path):
    db_path = tmp_path / "state.db"
    conn = _init_db(db_path)
    now = time.time()
    _insert_session(conn, "s1", "cli", now)
    _insert_message(conn, "s1", "user", "Please debug this Hermes gateway error", now)
    _insert_message(
        conn,
        "s1",
        "assistant",
        "Loading skill",
        now + 1,
        _skill_view_call("autonomous-ai-agents:hermes-agent"),
    )
    conn.commit()
    conn.close()

    report = analyze_database(db_path, skills_root=_skill_root(tmp_path), days=1)

    missed_names = {
        item["name"] for event in report["events"] for item in event["expected_skills"]
    }
    assert "hermes-agent" not in missed_names


def test_late_skill_load_does_not_suppress_earlier_turn(tmp_path):
    db_path = tmp_path / "state.db"
    conn = _init_db(db_path)
    now = time.time()
    _insert_session(conn, "s1", "cli", now - 10)
    _insert_message(conn, "s1", "user", "Create a Linear issue", now)
    _insert_message(conn, "s1", "assistant", "No skill loaded here", now + 1)
    _insert_message(conn, "s1", "user", "Another Linear ticket", now + 2)
    _insert_message(conn, "s1", "assistant", "Loading skill now", now + 3, _skill_view_call("linear"))
    conn.commit()
    conn.close()

    report = analyze_database(db_path, skills_root=_skill_root(tmp_path), days=1)

    assert [event["message_id"] for event in report["events"]] == [1]


def test_next_session_skill_load_does_not_suppress_previous_session_miss(tmp_path):
    db_path = tmp_path / "state.db"
    conn = _init_db(db_path)
    now = time.time()
    _insert_session(conn, "s1", "cli", now)
    _insert_session(conn, "s2", "cli", now)
    _insert_message(conn, "s1", "user", "Create a Linear issue", now)
    _insert_message(conn, "s2", "assistant", "Loading skill", now + 1, _skill_view_call("linear"))
    conn.commit()
    conn.close()

    report = analyze_database(db_path, skills_root=_skill_root(tmp_path), days=1)

    assert report["summary"]["total_missed"] == 1
    assert [event["session_id"] for event in report["events"]] == ["s1"]


def test_window_uses_message_timestamp_not_session_start(tmp_path):
    db_path = tmp_path / "state.db"
    conn = _init_db(db_path)
    now = time.time()
    _insert_session(conn, "s1", "cli", now - 10 * 86400)
    _insert_message(conn, "s1", "user", "Create a Linear issue today", now)
    conn.commit()
    conn.close()

    report = analyze_database(db_path, skills_root=_skill_root(tmp_path), days=1)

    assert report["summary"]["total_turns_scored"] == 1
    assert report["summary"]["total_missed"] == 1


@pytest.mark.parametrize(
    "content",
    [
        "[CONTEXT COMPACTION — REFERENCE ONLY] Previous summary mentions Linear issues",
        "[Your active task list was preserved across context compression]\n- Linear ticket",
        "[IMPORTANT: You are running as a scheduled cron job]\nCheck Hermes cron profile",
        "[SYSTEM: The user has invoked the \"plan\" command]\nWrite a plan",
    ],
)
def test_synthetic_user_messages_are_skipped(tmp_path, content):
    db_path = tmp_path / "state.db"
    conn = _init_db(db_path)
    now = time.time()
    _insert_session(conn, "s1", "cli", now)
    _insert_message(conn, "s1", "user", content, now)
    conn.commit()
    conn.close()

    report = analyze_database(db_path, skills_root=_skill_root(tmp_path), days=1)

    assert report["summary"]["total_turns_scored"] == 0
    assert report["summary"]["total_missed"] == 0


def test_cron_source_is_excluded_by_default_but_can_be_included(tmp_path):
    db_path = tmp_path / "state.db"
    conn = _init_db(db_path)
    now = time.time()
    _insert_session(conn, "cron-session", "cron", now)
    _insert_message(conn, "cron-session", "user", "Debug Hermes cron profile failure", now)
    conn.commit()
    conn.close()

    default_report = analyze_database(db_path, skills_root=_skill_root(tmp_path), days=1)
    included_report = analyze_database(
        db_path,
        skills_root=_skill_root(tmp_path),
        days=1,
        include_sources={"cron"},
    )

    assert default_report["summary"]["total_turns_scored"] == 0
    assert included_report["summary"]["total_turns_scored"] == 1
    assert included_report["summary"]["total_missed"] == 1


def test_json_encoded_structured_content_extracts_text_parts(tmp_path):
    db_path = tmp_path / "state.db"
    conn = _init_db(db_path)
    now = time.time()
    content = "\x00json:" + json.dumps(
        [
            {"type": "text", "text": "Please create a Linear issue"},
            {"type": "image_url", "image_url": {"url": "file:///tmp/screenshot.png"}},
        ]
    )
    _insert_session(conn, "s1", "cli", now)
    _insert_message(conn, "s1", "user", content, now)
    conn.commit()
    conn.close()

    report = analyze_database(db_path, skills_root=_skill_root(tmp_path), days=1)

    assert report["summary"]["total_turns_scored"] == 1
    assert report["summary"]["total_missed"] == 1
    assert report["events"][0]["user_excerpt"] == "Please create a Linear issue"


def test_missing_schema_raises_clear_error(tmp_path):
    db_path = tmp_path / "state.db"
    sqlite3.connect(db_path).close()

    with pytest.raises(RuntimeError, match="missing required table"):
        analyze_database(db_path, skills_root=_skill_root(tmp_path), days=1)


def test_missing_database_path_does_not_create_file(tmp_path):
    db_path = tmp_path / "missing" / "state.db"

    with pytest.raises(RuntimeError, match="unable to open session database read-only"):
        analyze_database(db_path, skills_root=_skill_root(tmp_path), days=1)

    assert not db_path.exists()


def test_write_reports_does_not_touch_usage_sidecar(tmp_path):
    output = tmp_path / "logs" / "skills"
    paths = write_reports({"summary": {"window_days": 1, "total_missed": 0}, "events": []}, output)
    assert paths["events"].exists()
    assert paths["summary"].exists()
    assert not (tmp_path / "skills" / ".usage.json").exists()
