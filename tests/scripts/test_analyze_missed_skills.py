import sqlite3
import time
from pathlib import Path

import pytest

from scripts.analyze_missed_skills import main


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
    root = tmp_path / "skills" / "custom" / "hermes-agent"
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(
        "---\nname: hermes-agent\ndescription: Hermes\n---\n",
        encoding="utf-8",
    )
    return tmp_path / "skills"


def test_cli_rejects_invalid_threshold():
    with pytest.raises(SystemExit):
        main(["--threshold", "1.5"])


def test_cli_rejects_non_positive_days():
    with pytest.raises(SystemExit):
        main(["--days", "0"])


def test_cli_include_source_controls_source_filter(tmp_path, capsys):
    db_path = tmp_path / "state.db"
    out_dir = tmp_path / "reports"
    conn = _init_db(db_path)
    now = time.time()
    conn.execute(
        "INSERT INTO sessions(id, source, started_at) VALUES (?, ?, ?)",
        ("cron-session", "cron", now),
    )
    conn.execute(
        "INSERT INTO messages(session_id, role, content, tool_calls, timestamp) "
        "VALUES (?, ?, ?, ?, ?)",
        ("cron-session", "user", "Debug Hermes cron profile", None, now),
    )
    conn.commit()
    conn.close()

    exit_code = main(
        [
            "--db",
            str(db_path),
            "--skills-root",
            str(_skill_root(tmp_path)),
            "--output-dir",
            str(out_dir),
            "--days",
            "1",
            "--include-source",
            "cron",
        ]
    )

    assert exit_code == 0
    assert "turns_scored=1 missed=1" in capsys.readouterr().out
    assert (out_dir / "missed-skill-events.jsonl").exists()
    assert (out_dir / "missed-skill-summary.json").exists()
