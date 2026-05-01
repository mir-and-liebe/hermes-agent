import json
from pathlib import Path

from scripts.audit_silent_failures import audit_paths, main


def write_source(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "sample.py"
    path.write_text(source)
    return path


def test_raw_except_pass_is_flagged(tmp_path):
    path = write_source(
        tmp_path,
        """
def f():
    try:
        risky()
    except Exception:
        pass
""",
    )

    findings = audit_paths([path])

    assert len(findings) == 1
    assert findings[0]["line"] == 5
    assert findings[0]["pattern"] == "pass"


def test_raw_except_return_none_is_flagged(tmp_path):
    path = write_source(
        tmp_path,
        """
def f():
    try:
        risky()
    except Exception:
        return None
""",
    )

    findings = audit_paths([path])

    assert len(findings) == 1
    assert findings[0]["pattern"] == "return None"


def test_report_failure_makes_handler_visible(tmp_path):
    path = write_source(
        tmp_path,
        """
def f():
    try:
        risky()
    except Exception as exc:
        report_failure(component='x', operation='y', severity='degraded', message='z', exc=exc)
        return None
""",
    )

    assert audit_paths([path]) == []


def test_failure_policy_helpers_make_handler_visible(tmp_path):
    path = write_source(
        tmp_path,
        """
def f():
    try:
        risky()
    except Exception as exc:
        degraded(component='x', operation='y', exc=exc, user_visible_effect='fallback')
        return None
""",
    )

    assert audit_paths([path]) == []


def test_failure_reporting_wrappers_make_handler_visible(tmp_path):
    path = write_source(
        tmp_path,
        """
def f():
    try:
        risky()
    except Exception as exc:
        _report_render_failure(operation='x', exc=exc, cols=80, effect='fallback')
        return None
""",
    )

    assert audit_paths([path]) == []


def test_allowlist_comment_with_reason_is_not_flagged(tmp_path):
    path = write_source(
        tmp_path,
        """
def f():
    try:
        risky()
    except Exception:  # hermes-ok-silent: closed stdout during shutdown
        pass
""",
    )

    assert audit_paths([path]) == []


def test_allowlist_comment_without_reason_is_flagged(tmp_path):
    path = write_source(
        tmp_path,
        """
def f():
    try:
        risky()
    except Exception:  # hermes-ok-silent:
        pass
""",
    )

    findings = audit_paths([path])

    assert len(findings) == 1
    assert findings[0]["pattern"] == "pass"


def test_main_writes_baseline_and_allows_existing_findings(tmp_path, capsys):
    path = write_source(
        tmp_path,
        """
def f():
    try:
        risky()
    except Exception:
        pass
""",
    )
    baseline = tmp_path / "baseline.json"

    assert main([str(path), "--write-baseline", str(baseline)]) == 0
    baseline_data = json.loads(baseline.read_text())
    assert len(baseline_data["findings"]) == 1

    assert main([str(path), "--baseline", str(baseline)]) == 0
