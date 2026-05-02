from __future__ import annotations

import importlib.util
import json
import os
import stat
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
STATE_MODULE_PATH = ROOT / "scripts" / "hermes_custom_update_state.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "hermes_custom_update_state", STATE_MODULE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_write_state_creates_secure_success_file(tmp_path):
    module = _load_module()
    state_path = tmp_path / "ops" / "state" / "hermes-custom-update.json"

    module.write_state(
        state_path,
        {
            "status": "success",
            "repo": str(tmp_path / "repo"),
            "branch": "main",
            "mode": "merge",
            "autostash": "false",
            "push_after": "false",
            "verify_after": "true",
            "verified": "true",
            "pushed": "false",
            "stash_created": "false",
            "stash_restored": "false",
            "stash_restore_conflicted": "false",
            "started_at": "2026-05-02T00:00:00Z",
            "finished_at": "2026-05-02T00:00:01Z",
            "exit_code": "0",
            "before_head": "a" * 40,
            "after_head": "a" * 40,
            "origin_main": "a" * 40,
            "upstream_main": "a" * 40,
            "changed": "false",
        },
    )

    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["status"] == "success"
    assert data["changed"] is False
    assert data["verified"] is True
    assert data["pushed"] is False
    assert data["exit_code"] == 0
    assert stat.S_IMODE(state_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600


def test_write_state_rejects_invalid_status(tmp_path):
    module = _load_module()

    with pytest.raises(ValueError, match="status"):
        module.write_state(tmp_path / "state.json", {"status": "maybe"})


def test_write_state_drops_secret_bearing_fields_and_sanitizes_strings(tmp_path):
    module = _load_module()
    state_path = tmp_path / "state.json"

    module.write_state(
        state_path,
        {
            "status": "failed",
            "failed_step": "fetch_upstream",
            "failure_code": "fetch_failed",
            "exit_code": "1",
            "origin_url": "https://user:secret@example.com/mir-and-liebe/hermes-agent.git",
            "stderr": "remote https://token:secret@example.com/repo failed",
            "failure_summary": "fetch failed for https://user:secret@example.com/repo?token=secret",
        },
    )

    raw = state_path.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert "origin_url" not in data
    assert "stderr" not in data
    assert "secret" not in raw
    assert "token=secret" not in raw
    assert data["failure_summary"] == "fetch failed for https://[REDACTED]@example.com/repo?token=[REDACTED]"


def test_write_state_preserves_existing_file_when_replace_fails(tmp_path, monkeypatch):
    module = _load_module()
    state_path = tmp_path / "state.json"
    module.write_state(state_path, {"status": "success", "exit_code": "0"})
    before = state_path.read_text(encoding="utf-8")

    def fail_replace(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        module.write_state(state_path, {"status": "failed", "exit_code": "1"})

    assert state_path.read_text(encoding="utf-8") == before
    assert not list(tmp_path.glob(".*.tmp"))
