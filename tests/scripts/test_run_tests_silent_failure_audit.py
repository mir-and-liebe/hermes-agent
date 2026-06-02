from pathlib import Path


def test_run_tests_invokes_silent_failure_audit_before_pytest():
    script = Path("scripts/run_tests.sh").read_text(encoding="utf-8")

    audit_pos = script.index("scripts/audit_silent_failures.py")
    runner_cmd = '"$PYTHON" "$SCRIPT_DIR/run_tests_parallel.py" "$@"'
    runner_pos = script.index(runner_cmd)

    assert audit_pos < runner_pos
    assert "--baseline" in script
    assert ".silent-failures-baseline.json" in script
    assert runner_cmd in script
