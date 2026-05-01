"""Tests for tui_gateway.render — rendering bridge fallback behavior."""

import logging
from unittest.mock import MagicMock, patch

from agent.failure_policy import get_failure_counts, reset_failure_counts_for_tests
from tui_gateway.render import make_stream_renderer, render_diff, render_message


def _stub_rich(mock_mod):
    return patch.dict("sys.modules", {"agent.rich_output": mock_mod})


def _no_rich():
    return patch.dict("sys.modules", {"agent.rich_output": None})


# ── render_message ───────────────────────────────────────────────────


def test_render_message_none_without_module():
    with _no_rich():
        assert render_message("hello") is None


def test_render_message_formatted():
    mod = MagicMock()
    mod.format_response.return_value = "<b>hi</b>"

    with _stub_rich(mod):
        assert render_message("hi", 100) == "<b>hi</b>"


def test_render_message_type_error_fallback():
    mod = MagicMock()
    mod.format_response.side_effect = [TypeError, "fallback"]

    with _stub_rich(mod):
        assert render_message("hi") == "fallback"


def test_render_message_exception_returns_none(caplog):
    reset_failure_counts_for_tests()
    caplog.set_level(logging.WARNING, logger="hermes.failure")
    mod = MagicMock()
    mod.format_response.side_effect = RuntimeError

    with _stub_rich(mod):
        assert render_message("hi") is None

    assert "component=tui_gateway.render" in caplog.text
    assert "operation=render_message" in caplog.text
    assert get_failure_counts()["tui_gateway.render.render_message.degraded"] == 1


# ── render_diff / make_stream_renderer ───────────────────────────────


def test_render_diff_none_without_module():
    with _no_rich():
        assert render_diff("+line") is None


def test_stream_renderer_none_without_module():
    with _no_rich():
        assert make_stream_renderer() is None


def test_stream_renderer_returns_instance():
    renderer = MagicMock()
    mod = MagicMock()
    mod.StreamingRenderer.return_value = renderer

    with _stub_rich(mod):
        assert make_stream_renderer(120) is renderer


def test_render_diff_exception_returns_none_and_reports_failure(caplog):
    reset_failure_counts_for_tests()
    caplog.set_level(logging.WARNING, logger="hermes.failure")
    mod = MagicMock()
    mod.render_diff.side_effect = RuntimeError("bad diff")

    with _stub_rich(mod):
        assert render_diff("+line") is None

    assert "component=tui_gateway.render" in caplog.text
    assert "operation=render_diff" in caplog.text
    assert get_failure_counts()["tui_gateway.render.render_diff.degraded"] == 1


def test_stream_renderer_exception_returns_none_and_reports_failure(caplog):
    reset_failure_counts_for_tests()
    caplog.set_level(logging.WARNING, logger="hermes.failure")
    mod = MagicMock()
    mod.StreamingRenderer.side_effect = RuntimeError("bad renderer")

    with _stub_rich(mod):
        assert make_stream_renderer(120) is None

    assert "component=tui_gateway.render" in caplog.text
    assert "operation=make_stream_renderer" in caplog.text
    assert get_failure_counts()["tui_gateway.render.make_stream_renderer.degraded"] == 1
