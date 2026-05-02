#!/usr/bin/env python3
"""Generate Hermes missed-skill telemetry from local session history."""
from __future__ import annotations

import argparse
from pathlib import Path

from agent.skill_miss_detector import analyze_database, format_summary, write_reports
from hermes_constants import get_hermes_home


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--threshold", type=float, default=0.75)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--skills-root", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument(
        "--include-source",
        action="append",
        default=None,
        help="Session source to include (repeatable). Defaults to interactive sources.",
    )
    args = parser.parse_args(argv)
    if args.days < 1:
        parser.error("--days must be >= 1")
    if not 0 <= args.threshold <= 1:
        parser.error("--threshold must be between 0 and 1")

    hermes_home = Path(get_hermes_home())
    report = analyze_database(
        args.db or hermes_home / "state.db",
        skills_root=args.skills_root or hermes_home / "skills",
        days=args.days,
        threshold=args.threshold,
        include_sources=args.include_source,
    )
    paths = write_reports(report, args.output_dir or hermes_home / "logs" / "skills")
    print(format_summary(report["summary"]))
    print(f"events={paths['events']}")
    print(f"summary={paths['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
