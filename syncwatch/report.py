"""Turns findings into text a person can read, or JSON a machine can."""

from __future__ import annotations

import json
import os
import sys
from typing import TextIO

from syncwatch.checks import Finding, Severity

MARK = {Severity.BROKEN: "BROKEN", Severity.WARNING: "WARNING", Severity.INFO: "INFO"}
COLOUR = {Severity.BROKEN: "\033[31m", Severity.WARNING: "\033[33m", Severity.INFO: "\033[2m"}
GREEN, BOLD, RESET = "\033[32m", "\033[1m", "\033[0m"

CHECKS = [
    ("duplicate_keys", "The same record exists more than once in the destination."),
    ("missing_source_field", "The sync reads a field that no longer exists in the source."),
    ("fill_rate_drop", "A destination column that used to be full has emptied since the last check."),
    ("unsynced_records", "The source has records, or newer versions, the destination never got."),
    ("silent_success", "The sync said success and moved nothing while records were waiting."),
]


def use_colour(stream: TextIO) -> bool:
    if "NO_COLOR" in os.environ:
        return False
    return stream.isatty() or "FORCE_COLOR" in os.environ


def counts(findings: list[Finding]) -> tuple[int, int]:
    broken = sum(1 for f in findings if f.severity is Severity.BROKEN)
    warn = sum(1 for f in findings if f.severity is Severity.WARNING)
    return broken, warn


def render_text(
    findings: list[Finding], n_source: int, n_dest: int, stream: TextIO | None = None
) -> None:
    stream = stream or sys.stdout
    colour = use_colour(stream)
    width = max((len(f.check) for f in findings), default=0)
    for f in findings:
        line = f"{MARK[f.severity].ljust(7)}  {f.check.ljust(width)}  {f.message}"
        stream.write((COLOUR[f.severity] + line + RESET if colour else line) + "\n")

    broken, warn = counts(findings)
    if broken or warn:
        parts = []
        if broken:
            parts.append(f"{broken} broken")
        if warn:
            parts.append(f"{warn} warning" + ("s" if warn != 1 else ""))
        summary = ", ".join(parts)
        if findings:
            stream.write("\n")
        stream.write((BOLD + summary + RESET if colour else summary) + "\n")
    else:
        if findings:
            stream.write("\n")
        summary = f"clean: {n_source} source records, {n_dest} destination records, nothing wrong"
        stream.write((GREEN + summary + RESET if colour else summary) + "\n")


def render_json(findings: list[Finding], n_source: int, n_dest: int, stream: TextIO | None = None) -> None:
    stream = stream or sys.stdout
    broken, warn = counts(findings)
    payload = {
        "clean": broken == 0 and warn == 0,
        "broken": broken,
        "warnings": warn,
        "source_records": n_source,
        "destination_records": n_dest,
        "findings": [
            {"check": f.check, "severity": f.severity.value, "message": f.message, "detail": f.detail}
            for f in findings
        ],
    }
    stream.write(json.dumps(payload, indent=2, default=str) + "\n")


def render_checks(stream: TextIO | None = None) -> None:
    stream = stream or sys.stdout
    width = max(len(name) for name, _ in CHECKS)
    for name, meaning in CHECKS:
        stream.write(f"{name.ljust(width)}  {meaning}\n")
