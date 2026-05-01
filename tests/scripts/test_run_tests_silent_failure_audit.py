from pathlib import Path


def test_run_tests_invokes_silent_failure_audit_before_pytest():
    script = Path("scripts/run_tests.sh").read_text(encoding="utf-8")

    audit_pos = script.index("scripts/audit_silent_failures.py")
    pytest_pos = script.index("-m pytest")

    assert audit_pos < pytest_pos
    assert "--baseline" in script
    assert ".silent-failures-baseline.json" in script
    assert 'if [ "$#" -gt 0 ]' in script
    assert 'PYTEST_ARGS+=("$@")' in script
