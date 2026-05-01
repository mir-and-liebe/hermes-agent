import logging
from pathlib import Path
import sys

from agent.failure_policy import get_failure_counts, reset_failure_counts_for_tests
from cron import scheduler


def test_unknown_delivery_platform_is_degraded(caplog):
    reset_failure_counts_for_tests()
    caplog.set_level(logging.WARNING, logger="hermes.failure")

    target = scheduler._resolve_single_delivery_target({"id": "job1"}, "unknown-platform")

    assert target is None
    assert "cron delivery target references an unknown platform" in caplog.text
    assert "platform=unknown-platform" in caplog.text
    assert get_failure_counts()["cron.delivery.resolve_delivery_target.degraded"] == 1


def test_missing_home_target_is_degraded(monkeypatch, caplog):
    reset_failure_counts_for_tests()
    caplog.set_level(logging.WARNING, logger="hermes.failure")
    monkeypatch.delenv("TELEGRAM_HOME_CHANNEL", raising=False)

    target = scheduler._resolve_single_delivery_target({"id": "job2"}, "telegram")

    assert target is None
    assert "cron delivery platform has no configured home target" in caplog.text
    assert "platform=telegram" in caplog.text


def test_channel_directory_resolution_failure_is_degraded(monkeypatch, caplog):
    reset_failure_counts_for_tests()
    caplog.set_level(logging.WARNING, logger="hermes.failure")

    def explode(_platform, _chat_id):
        raise RuntimeError("directory down")

    monkeypatch.setattr("gateway.channel_directory.resolve_channel_name", explode)

    target = scheduler._resolve_single_delivery_target({"id": "job3"}, "telegram:Mir")

    assert target == {"platform": "telegram", "chat_id": "Mir", "thread_id": None}
    assert "operation=resolve_channel_name" in caplog.text


def test_script_redaction_failure_withholds_output(monkeypatch, tmp_path, caplog):
    reset_failure_counts_for_tests()
    caplog.set_level(logging.WARNING, logger="hermes.failure")
    from hermes_constants import get_hermes_home

    scripts_dir = get_hermes_home() / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    script = scripts_dir / "script.py"
    script.write_text("print('private-data')\n")

    def explode(_text):
        raise RuntimeError("redactor down")

    monkeypatch.setattr("agent.redact.redact_sensitive_text", explode)

    ok, output = scheduler._run_job_script(script)

    assert ok is True
    assert output == "[redaction failed; output withheld]"
    assert "private-data" not in output
    assert "operation=redact_script_output" in caplog.text


def test_wrap_response_config_failure_is_best_effort(monkeypatch, caplog):
    reset_failure_counts_for_tests()
    caplog.set_level(logging.DEBUG, logger="hermes.failure")

    monkeypatch.setattr(scheduler, "load_config", lambda: (_ for _ in ()).throw(RuntimeError("config down")))
    monkeypatch.setattr("gateway.config.load_gateway_config", lambda: {})

    error = scheduler._deliver_result(
        {"id": "job4", "name": "Job 4", "deliver": "origin", "origin": {"platform": "missing", "chat_id": "1"}},
        "hello",
    )

    assert "unknown platform" in error
    assert "operation=load_wrap_response_config" in caplog.text
