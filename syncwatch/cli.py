"""Command line entry point.

  syncwatch check  --source S --dest D --mapping M [--runs R] [--state F]
  syncwatch checks

Exit codes: 0 clean, 1 something is wrong, 2 could not read the inputs.
Exit 2 is deliberate and loud. If the watchdog cannot see, it must say so.
"""

from __future__ import annotations

import argparse
import sys

from syncwatch.checks import Finding, Severity, rebaseline, run_checks
from syncwatch.model import (
    LoadError,
    State,
    load_mapping,
    load_runs,
    load_snapshot,
    load_state,
    save_state,
)
from syncwatch.report import counts, render_checks, render_json, render_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="syncwatch",
        description="Catches the sync that says success while the data is wrong.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="compare the two systems and report what is wrong")
    check.add_argument("--source", required=True, help="snapshot of the system data comes from")
    check.add_argument("--dest", required=True, help="snapshot of the system data goes to")
    check.add_argument("--mapping", required=True, help="which source field lands in which destination field")
    check.add_argument("--runs", help="the sync's own run log, needed for silent_success")
    check.add_argument("--state", help="file to remember the last check in, needed for fill_rate_drop")
    check.add_argument("--json", action="store_true", help="machine readable output")
    check.add_argument(
        "--rebaseline",
        action="store_true",
        help="accept the current data as normal and reset the state file. Use after a deliberate change",
    )

    sub.add_parser("checks", help="list the checks and what each one catches")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "checks":
        render_checks()
        return 0

    try:
        source = load_snapshot(args.source)
        dest = load_snapshot(args.dest)
        mapping = load_mapping(args.mapping)
        runs = load_runs(args.runs) if args.runs else []
        state = load_state(args.state) if args.state else State()
    except LoadError as exc:
        print(f"syncwatch: cannot check, {exc}", file=sys.stderr)
        return 2

    if args.rebaseline:
        if not args.state:
            print("syncwatch: --rebaseline needs --state", file=sys.stderr)
            return 2
        save_state(rebaseline(source, dest, mapping), args.state)
        print(f"baseline reset from {len(source.records)} source and {len(dest.records)} destination records")
        return 0

    findings, new_state = run_checks(source, dest, mapping, runs, state)

    if not args.state:
        findings = [f for f in findings if f.check != "baseline"]
        findings.append(
            Finding("baseline", Severity.INFO, "no --state file, so fill_rate_drop and rename hints are off")
        )
    if not args.runs:
        findings.append(Finding("runs", Severity.INFO, "no --runs file, so silent_success is off"))

    # Report first. If saving state fails afterwards, the findings must still be seen.
    if args.json:
        render_json(findings, len(source.records), len(dest.records))
    else:
        render_text(findings, len(source.records), len(dest.records))

    if args.state:
        try:
            save_state(new_state, args.state)
        except OSError as exc:
            print(f"syncwatch: checked, but could not save state to {args.state}: {exc}", file=sys.stderr)
            return 2

    broken, warn = counts(findings)
    return 1 if broken or warn else 0


if __name__ == "__main__":
    raise SystemExit(main())
