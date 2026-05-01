from pathlib import Path

from scripts.audit_silent_failures import audit_paths

ROOT = Path(__file__).resolve().parents[2]
RUN_AGENT = ROOT / "run_agent.py"


def test_targeted_agent_loop_failures_are_reported_with_failure_policy():
    source = RUN_AGENT.read_text()

    for operation in [
        "cleanup_dead_connections",
        "load_cached_system_prompt",
        "persist_token_counts",
        "clear_nous_rate_limit",
        "post_api_request",
        "tool_progress_callback_thinking",
        "tool_progress_callback_reasoning",
        "sync_external_memory_provider",
        "spawn_background_review",
    ]:
        assert f'operation="{operation}"' in source

    assert 'component="agent.loop"' in source
    assert 'component="agent.session"' in source
    assert 'component="agent.accounting"' in source
    assert 'component="agent.hooks"' in source
    assert 'component="agent.memory"' in source


def test_targeted_agent_loop_sites_do_not_introduce_new_silent_audit_findings():
    baseline = ROOT / ".silent-failures-baseline.json"
    baseline_keys = {
        (item["file"], item["pattern"], item.get("snippet", ""))
        for item in __import__("json").loads(baseline.read_text())["findings"]
    }
    current = audit_paths([RUN_AGENT])
    new_findings = [
        item for item in current
        if (item["file"], item["pattern"], item.get("snippet", "")) not in baseline_keys
    ]

    assert new_findings == []
