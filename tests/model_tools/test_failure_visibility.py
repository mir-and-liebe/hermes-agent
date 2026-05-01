import logging

from agent.failure_policy import get_failure_counts, reset_failure_counts_for_tests
from model_tools import _report_discovery_failure


def test_report_discovery_failure_is_degraded_and_counted(caplog):
    reset_failure_counts_for_tests()
    caplog.set_level(logging.WARNING, logger="hermes.failure")

    _report_discovery_failure("mcp", RuntimeError("mcp down"))

    assert "component=model_tools" in caplog.text
    assert "operation=mcp_discovery" in caplog.text
    assert "tool subsystem discovery failed; related tools may be unavailable" in caplog.text
    assert get_failure_counts()["model_tools.mcp_discovery.degraded"] == 1
