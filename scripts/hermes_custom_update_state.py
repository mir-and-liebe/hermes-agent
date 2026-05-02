#!/usr/bin/env python3
"""Write safe machine-readable state for hermes-custom-update."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = 1
_VALID_STATUSES = {"success", "failed"}
_BOOL_FIELDS = {
    "autostash",
    "push_after",
    "verify_after",
    "verified",
    "pushed",
    "stash_created",
    "stash_restored",
    "stash_restore_conflicted",
    "changed",
    "local_matches_origin_main",
    "origin_matches_upstream_main",
}
_INT_FIELDS = {"exit_code", "duration_seconds"}
_ALLOWED_FIELDS = {
    "schema_version",
    "status",
    "repo",
    "branch",
    "mode",
    "autostash",
    "push_after",
    "verify_after",
    "started_at",
    "finished_at",
    "duration_seconds",
    "before_head",
    "after_head",
    "origin_main",
    "upstream_main",
    "changed",
    "pushed",
    "verified",
    "stash_created",
    "stash_restored",
    "stash_restore_conflicted",
    "failed_step",
    "failure_code",
    "failure_summary",
    "exit_code",
    "local_matches_origin_main",
    "origin_matches_upstream_main",
    "local_vs_upstream_left_right_count",
    "origin_vs_upstream_left_right_count",
}
_DROP_KEY_PARTS = (
    "url",
    "remote",
    "stderr",
    "stdout",
    "token",
    "secret",
    "authorization",
    "password",
    "credential",
)
_ENV_TO_FIELD = {
    "HCU_STATUS": "status",
    "HCU_REPO": "repo",
    "HCU_BRANCH": "branch",
    "HCU_MODE": "mode",
    "HCU_AUTOSTASH": "autostash",
    "HCU_PUSH_AFTER": "push_after",
    "HCU_VERIFY_AFTER": "verify_after",
    "HCU_STARTED_AT": "started_at",
    "HCU_FINISHED_AT": "finished_at",
    "HCU_DURATION_SECONDS": "duration_seconds",
    "HCU_BEFORE_HEAD": "before_head",
    "HCU_AFTER_HEAD": "after_head",
    "HCU_ORIGIN_MAIN": "origin_main",
    "HCU_UPSTREAM_MAIN": "upstream_main",
    "HCU_CHANGED": "changed",
    "HCU_PUSHED": "pushed",
    "HCU_VERIFIED": "verified",
    "HCU_STASH_CREATED": "stash_created",
    "HCU_STASH_RESTORED": "stash_restored",
    "HCU_STASH_RESTORE_CONFLICTED": "stash_restore_conflicted",
    "HCU_FAILED_STEP": "failed_step",
    "HCU_FAILURE_CODE": "failure_code",
    "HCU_FAILURE_SUMMARY": "failure_summary",
    "HCU_EXIT_CODE": "exit_code",
    "HCU_LOCAL_MATCHES_ORIGIN_MAIN": "local_matches_origin_main",
    "HCU_ORIGIN_MATCHES_UPSTREAM_MAIN": "origin_matches_upstream_main",
    "HCU_LOCAL_VS_UPSTREAM_LEFT_RIGHT_COUNT": "local_vs_upstream_left_right_count",
    "HCU_ORIGIN_VS_UPSTREAM_LEFT_RIGHT_COUNT": "origin_vs_upstream_left_right_count",
}

_USERINFO_RE = re.compile(r"(https?://)[^/@\s]+@")
_QUERY_SECRET_RE = re.compile(
    r"([?&](?:token|access_token|password|secret|key|api_key)=)[^&\s]+",
    re.IGNORECASE,
)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _DROP_KEY_PARTS)


def _sanitize_text(value: str) -> str:
    value = _USERINFO_RE.sub(r"\1[REDACTED]@", value)
    return _QUERY_SECRET_RE.sub(r"\1[REDACTED]", value)


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y"}:
            return True
        if lowered in {"0", "false", "no", "n", ""}:
            return False
    return bool(value)


def _coerce_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        return int(value.strip())
    return 0


def normalize_state(raw_state: Mapping[str, object]) -> dict[str, object]:
    normalized: dict[str, object] = {"schema_version": SCHEMA_VERSION}

    for key, raw_value in raw_state.items():
        if key not in _ALLOWED_FIELDS or _is_sensitive_key(key):
            continue
        if raw_value is None:
            continue
        if isinstance(raw_value, str) and raw_value == "":
            continue
        if key in _BOOL_FIELDS:
            normalized[key] = _coerce_bool(raw_value)
        elif key in _INT_FIELDS:
            normalized[key] = _coerce_int(raw_value)
        elif isinstance(raw_value, str):
            normalized[key] = _sanitize_text(raw_value)
        else:
            normalized[key] = raw_value

    status = normalized.get("status")
    if status not in _VALID_STATUSES:
        raise ValueError("status must be one of: failed, success")
    if status == "failed" and not normalized.get("failed_step"):
        normalized["failed_step"] = "unknown"
    normalized.setdefault("exit_code", 0 if status == "success" else 1)
    return normalized


def write_state(path: str | Path, raw_state: Mapping[str, object]) -> None:
    state = normalize_state(raw_state)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(target.parent, 0o700)

    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent),
        prefix=f".{target.stem}_",
        suffix=".tmp",
        text=True,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, target)
        os.chmod(target, 0o600)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def _state_from_env(env: Mapping[str, str]) -> dict[str, object]:
    state: dict[str, object] = {}
    for env_key, field in _ENV_TO_FIELD.items():
        if env_key in env:
            state[field] = env[env_key]
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    env_parser = subparsers.add_parser("write-from-env")
    env_parser.add_argument("--path", required=True)

    stdin_parser = subparsers.add_parser("write")
    stdin_parser.add_argument("--path", required=True)

    args = parser.parse_args(argv)
    if args.command == "write-from-env":
        state = _state_from_env(os.environ)
    else:
        state = json.load(sys.stdin)
    write_state(args.path, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
