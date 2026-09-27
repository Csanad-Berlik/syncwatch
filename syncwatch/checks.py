"""The five checks.

Each one answers a question a client would actually ask:

  duplicate_keys        Does the same record exist twice?
  missing_source_field  Is the sync reading a field that no longer exists?
  fill_rate_drop        Did a column that used to be full suddenly empty?
  unsynced_records      Does the source have records the destination never got?
  silent_success        Did the sync say "success" while moving nothing it should have?

None of them look at what the sync says it did, except silent_success, and
that one exists precisely to catch the sync lying.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from syncwatch.model import (
    SUCCESS_WORDS,
    Mapping,
    Run,
    Snapshot,
    State,
    is_empty,
    latest_run,
    norm_key,
    parse_time,
)

SAMPLE = 5  # how many example keys to print in a finding


class Severity(str, Enum):
    BROKEN = "broken"    # data is wrong right now
    WARNING = "warning"  # data is probably wrong, but there are innocent explanations
    INFO = "info"        # nothing wrong, just telling you something


ORDER = {Severity.BROKEN: 0, Severity.WARNING: 1, Severity.INFO: 2}


@dataclass
class Finding:
    check: str
    severity: Severity
    message: str
    detail: dict[str, Any] = field(default_factory=dict)


def _sample(keys: list[str]) -> str:
    shown = ", ".join(keys[:SAMPLE])
    more = len(keys) - SAMPLE
    return shown + (f" (+{more} more)" if more > 0 else "")


def _n(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


# ---------------------------------------------------------------- 1


def duplicate_keys(dest: Snapshot, mapping: Mapping) -> list[Finding]:
    """The same key more than once in the destination.

    The classic cause is a webhook delivered twice. Providers retry when they
    do not get a fast enough 200, and a sync that inserts without checking
    what is already there writes the record again.
    """
    counts: Counter[str] = Counter()
    keyless = 0
    for record in dest.records:
        k = norm_key(record.get(mapping.dest_key))
        if k is None:
            keyless += 1
        else:
            counts[k] += 1

    findings: list[Finding] = []
    dups = sorted(k for k, n in counts.items() if n > 1)
    if dups:
        extra_rows = sum(counts[k] - 1 for k in dups)
        findings.append(
            Finding(
                "duplicate_keys",
                Severity.BROKEN,
                f"{_n(len(dups), 'key')} appear{'s' if len(dups) == 1 else ''} more than once in {dest.system} "
                f"({_n(extra_rows, 'extra row')}): {_sample([f'{k} x{counts[k]}' for k in dups])}",
                {"keys": dups, "extra_rows": extra_rows},
            )
        )
    if keyless:
        findings.append(
            Finding(
                "duplicate_keys",
                Severity.WARNING,
                f"{_n(keyless, 'record')} in {dest.system} {'has' if keyless == 1 else 'have'} no {mapping.dest_key!r} "
                f"and cannot be matched to anything",
                {"keyless": keyless},
            )
        )
    return findings


# ---------------------------------------------------------------- 2


def missing_source_field(source: Snapshot, mapping: Mapping, state: State) -> list[Finding]:
    """A field the mapping reads from is gone from the source.

    Code like payload.get("company") does not fail when "company" is renamed.
    It returns None, the sync writes None, and the run reports success.
    """
    if not source.records:
        return []  # an empty source says nothing about its schema
    present = source.fields()
    missing = [f for f in mapping.fields if f not in present]
    if not missing:
        return []

    hint = ""
    appeared: list[str] = []
    if state.source_fields is not None:
        appeared = sorted(present - state.source_fields)
        vanished = [f for f in missing if f in state.source_fields]
        if len(vanished) == 1 and len(appeared) == 1:
            hint = f". Looks like a rename: {vanished[0]} -> {appeared[0]}"
        elif appeared:
            hint = f". New fields since the last check: {', '.join(appeared)}"

    return [
        Finding(
            "missing_source_field",
            Severity.BROKEN,
            f"the mapping reads {', '.join(missing)} from {source.system}, "
            f"which no record has any more{hint}",
            {"missing": missing, "appeared": appeared},
        )
    ]


# ---------------------------------------------------------------- 3


def fill_rate(records: list[dict[str, Any]], name: str) -> float | None:
    if not records:
        return None
    filled = sum(1 for r in records if not is_empty(r.get(name)))
    return filled / len(records)


def fill_rate_drop(
    dest: Snapshot, mapping: Mapping, state: State
) -> tuple[list[Finding], dict[str, float]]:
    """A destination column emptied out since the last check.

    Returns the findings and the fill rates to remember for next time.
    A column that dropped keeps its old baseline, so it keeps alerting until
    it recovers. Otherwise one alert would quietly become the new normal.
    """
    now: dict[str, float] = {}
    for dest_field in mapping.fields.values():
        rate = fill_rate(dest.records, dest_field)
        if rate is not None:
            now[dest_field] = rate

    if state.dest_fill is None:
        return [], now

    findings: list[Finding] = []
    remember = dict(now)
    for name, rate in sorted(now.items()):
        before = state.dest_fill.get(name)
        if before is None:
            continue
        if before - rate >= mapping.fill_drop_threshold:
            findings.append(
                Finding(
                    "fill_rate_drop",
                    Severity.WARNING,
                    f"{name} in {dest.system} was {_pct(before)} filled at the last check, "
                    f"{_pct(rate)} now",
                    {"field": name, "before": before, "now": rate},
                )
            )
            remember[name] = before
    return findings, remember


# ---------------------------------------------------------------- 4


FUTURE_TOLERANCE = timedelta(minutes=5)


@dataclass
class Backlog:
    missing: list[str]
    stale: list[str]
    future: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.missing) + len(self.stale)


def find_backlog(source: Snapshot, dest: Snapshot, mapping: Mapping, now: datetime) -> Backlog:
    """Source records the destination does not have, or has an older version of.

    Records changed inside the grace window are skipped, because the sync may
    simply not have run yet. Records with no timestamp are always checked.

    A timestamp in the future is not trusted. It usually means local time was
    stored as UTC. Skipping those records as "too recent" would hide them
    forever, so they are checked like records with no timestamp and reported.
    """
    grace = timedelta(minutes=mapping.grace_minutes)
    dest_ts_field = mapping.dest_updated_at

    newest_in_dest: dict[str, datetime | None] = {}
    for record in dest.records:
        k = norm_key(record.get(mapping.dest_key))
        if k is None:
            continue
        ts = parse_time(record.get(dest_ts_field)) if dest_ts_field else None
        prev = newest_in_dest.get(k)
        if k not in newest_in_dest or (ts is not None and (prev is None or ts > prev)):
            newest_in_dest[k] = ts

    missing: list[str] = []
    stale: list[str] = []
    future: list[str] = []
    for record in source.records:
        k = norm_key(record.get(mapping.key))
        if k is None:
            continue
        src_ts = parse_time(record.get(mapping.updated_at)) if mapping.updated_at else None
        if src_ts is not None and src_ts - now > FUTURE_TOLERANCE:
            future.append(k)
            src_ts = None
        if src_ts is not None and now - src_ts < grace:
            continue
        if k not in newest_in_dest:
            missing.append(k)
            continue
        if src_ts is not None and dest_ts_field:
            dest_ts = newest_in_dest[k]
            if dest_ts is None or dest_ts < src_ts:
                stale.append(k)
    return Backlog(sorted(missing), sorted(stale), sorted(future))


def unsynced_records(backlog: Backlog, source: Snapshot, dest: Snapshot) -> list[Finding]:
    findings: list[Finding] = []
    if backlog.missing:
        findings.append(
            Finding(
                "unsynced_records",
                Severity.BROKEN,
                f"{_n(len(backlog.missing), 'record')} in {source.system} never reached "
                f"{dest.system}: {_sample(backlog.missing)}",
                {"missing": backlog.missing},
            )
        )
    if backlog.stale:
        findings.append(
            Finding(
                "unsynced_records",
                Severity.BROKEN,
                f"{_n(len(backlog.stale), 'record')} changed in {source.system} but {dest.system} "
                f"still has the old version: {_sample(backlog.stale)}",
                {"stale": backlog.stale},
            )
        )
    if backlog.future:
        findings.append(
            Finding(
                "unsynced_records",
                Severity.WARNING,
                f"{_n(len(backlog.future), 'record')} in {source.system} "
                f"{'has' if len(backlog.future) == 1 else 'have'} an update time in the future, "
                f"usually local time stored as UTC. Checked anyway: {_sample(backlog.future)}",
                {"future": backlog.future},
            )
        )
    return findings


# ---------------------------------------------------------------- 5


def silent_success(runs: list[Run], backlog: Backlog) -> list[Finding]:
    """The sync said success and moved nothing, while there was work waiting.

    The signature of an error caught and swallowed. An expired token is the
    usual cause: the fetch fails, the code treats it as "no new data", and
    the run is logged as a success.

    A run that reports failure is not flagged here. A loud failure is already
    doing its job.
    """
    last = latest_run(runs)
    if last is None:
        return []
    if last.status not in SUCCESS_WORDS or last.rows_moved is None:
        return []  # a failure is loud already, and an unknown count proves nothing
    if last.rows_moved != 0 or backlog.total == 0:
        return []
    when = last.run_at.strftime("%Y-%m-%d %H:%M UTC") if last.run_at else "the last run"
    return [
        Finding(
            "silent_success",
            Severity.BROKEN,
            f"the sync reported success at {when} and moved 0 rows, "
            f"but {_n(backlog.total, 'record')} {'was' if backlog.total == 1 else 'were'} waiting to move. "
            "It is failing quietly",
            {"run_at": last.run_at.isoformat() if last.run_at else None, "waiting": backlog.total},
        )
    ]


# ---------------------------------------------------------------- all


def run_checks(
    source: Snapshot,
    dest: Snapshot,
    mapping: Mapping,
    runs: list[Run] | None = None,
    state: State | None = None,
    now: datetime | None = None,
) -> tuple[list[Finding], State]:
    """Run every check. Returns the findings, sorted worst first, and the state to save."""
    state = state or State()
    runs = runs or []
    if now is None:
        now = source.taken_at or datetime.now(timezone.utc)

    findings: list[Finding] = []
    findings += duplicate_keys(dest, mapping)
    findings += missing_source_field(source, mapping, state)
    fill_findings, fill_now = fill_rate_drop(dest, mapping, state)
    findings += fill_findings
    backlog = find_backlog(source, dest, mapping, now)
    findings += unsynced_records(backlog, source, dest)
    findings += silent_success(runs, backlog)

    if state.dest_fill is None and state.source_fields is None:
        findings.append(
            Finding(
                "baseline",
                Severity.INFO,
                "first run: fill rates and field names recorded. "
                "fill_rate_drop and rename hints start working from the next run",
            )
        )

    findings.sort(key=lambda f: ORDER[f.severity])

    source_fields = source.fields() if source.records else state.source_fields
    if any(f.check == "missing_source_field" for f in findings) and state.source_fields is not None:
        source_fields = state.source_fields  # keep the good schema until it comes back

    new_state = State(
        source_fields=source_fields,
        dest_fill=fill_now,
        checked_at=now.isoformat(),
    )
    return findings, new_state


def rebaseline(source: Snapshot, dest: Snapshot, mapping: Mapping, now: datetime | None = None) -> State:
    """Accept the current state as normal. Use after a deliberate change."""
    now = now or source.taken_at or datetime.now(timezone.utc)
    fill = {}
    for dest_field in mapping.fields.values():
        rate = fill_rate(dest.records, dest_field)
        if rate is not None:
            fill[dest_field] = rate
    return State(
        source_fields=source.fields() if source.records else None,
        dest_fill=fill,
        checked_at=now.isoformat(),
    )
