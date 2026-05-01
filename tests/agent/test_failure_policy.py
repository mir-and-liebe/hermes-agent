import logging

import pytest

from agent.failure_policy import (
    best_effort,
    critical,
    degraded,
    get_failure_counts,
    get_recent_failure_lines,
    report_failure,
    reset_failure_counts_for_tests,
)


def setup_function():
    reset_failure_counts_for_tests()


def test_degraded_failure_logs_warning_and_counts(caplog):
    caplog.set_level(logging.WARNING, logger="hermes.failure")

    event = degraded(
        component="tools.registry",
        operation="check_fn",
        exc=RuntimeError("boom"),
        user_visible_effect="tool unavailable",
        tool="demo",
    )

    assert event.severity == "degraded"
    assert event.component == "tools.registry"
    assert event.operation == "check_fn"
    assert event.exception_type == "RuntimeError"
    assert event.metadata["tool"] == "demo"
    assert get_failure_counts()["tools.registry.check_fn.degraded"] == 1
    assert "component=tools.registry" in caplog.text
    assert "operation=check_fn" in caplog.text
    assert "tool unavailable" in caplog.text


def test_best_effort_requires_reason():
    with pytest.raises(ValueError, match="reason"):
        best_effort(
            component="agent.loop",
            operation="cleanup",
            exc=RuntimeError("boom"),
            reason="",
        )


def test_degraded_requires_user_visible_effect():
    with pytest.raises(ValueError, match="user_visible_effect"):
        degraded(
            component="agent.loop",
            operation="persist",
            exc=RuntimeError("boom"),
            user_visible_effect="",
        )


def test_report_failure_redacts_message_and_metadata(caplog):
    caplog.set_level(logging.ERROR, logger="hermes.failure")
    fake_credential = "sk-" + "unit-test-redaction-value"

    event = critical(
        component="gateway.delivery",
        operation="send",
        exc=RuntimeError(f"credential {fake_credential} leaked"),
        credential=fake_credential,
    )

    assert event.severity == "critical"
    assert fake_credential not in caplog.text
    assert "credential" in caplog.text
    assert get_failure_counts()["gateway.delivery.send.critical"] == 1


def test_report_failure_never_raises_when_redaction_fails(monkeypatch, caplog):
    import agent.redact

    def explode(_text):
        raise RuntimeError("redactor down")

    monkeypatch.setattr(agent.redact, "redact_sensitive_text", explode)
    caplog.set_level(logging.WARNING, logger="hermes.failure")

    event = report_failure(
        component="agent.memory",
        operation="sync",
        severity="degraded",
        message="sync failed",
        exc=RuntimeError("raw failure"),
        metadata={"credential": "sk-" + "unit-test-redaction-value"},
    )

    assert event.component == "agent.memory"
    assert "sk-sec...7890" not in caplog.text
    assert "redaction_failed" in caplog.text


def test_get_recent_failure_lines_reads_newest_failure_events(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "errors.log").write_text(
        "old unrelated\n"
        "2026 failure_event severity=degraded component=a operation=one message=first\n"
        "middle\n"
        "2026 failure_event severity=best_effort component=b operation=two message=second\n",
        encoding="utf-8",
    )

    lines = get_recent_failure_lines(limit=1, log_dir=logs)

    assert lines == ["2026 failure_event severity=best_effort component=b operation=two message=second"]
