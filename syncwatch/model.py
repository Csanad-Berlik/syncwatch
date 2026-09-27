"""The shapes everything else works against, and the loaders that build them.

Four inputs:

  snapshot   every record in one system at one moment
  mapping    which source field lands in which destination field
  runs       what the sync said about itself, one entry per run
  state      what the watchdog saw last time, so it can spot changes

Every loader raises LoadError with a plain message on bad input. The
watchdog must fail loudly when it cannot see. A watchdog that goes quiet
when its own read breaks is the same bug it exists to catch.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class LoadError(Exception):
    """An input file is missing, unreadable, or the wrong shape."""


# ---------------------------------------------------------------- helpers


def parse_time(value: Any) -> datetime | None:
    """ISO 8601 string or unix seconds to an aware UTC datetime. None if unparseable."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def norm_key(value: Any) -> str | None:
    """Keys are compared trimmed and lowercased. See README, known limits."""
    if is_empty(value):
        return None
    return str(value).strip().lower()


def _read_json(path: Path, what: str) -> Any:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise LoadError(f"{what} file not found: {path}") from None
    except OSError as exc:
        raise LoadError(f"cannot read {what} file {path}: {exc}") from None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LoadError(f"{what} file {path} is not valid JSON: {exc}") from None


# ---------------------------------------------------------------- snapshot


@dataclass
class Snapshot:
    system: str
    records: list[dict[str, Any]]
    taken_at: datetime | None = None

    def fields(self) -> set[str]:
        found: set[str] = set()
        for record in self.records:
            found.update(record.keys())
        return found


def load_snapshot(path: str | Path) -> Snapshot:
    data = _read_json(Path(path), "snapshot")
    if isinstance(data, list):
        records, system, taken = data, Path(path).stem, None
    elif isinstance(data, dict):
        records = data.get("records")
        system = str(data.get("system") or Path(path).stem)
        taken = data.get("taken_at")
    else:
        raise LoadError(f"snapshot {path} must be a list of records or an object with 'records'")
    if not isinstance(records, list):
        raise LoadError(f"snapshot {path}: 'records' must be a list")
    for i, record in enumerate(records):
        if not isinstance(record, dict):
            raise LoadError(f"snapshot {path}: record {i} is not an object")
    taken_at = parse_time(taken)
    if taken is not None and taken_at is None:
        raise LoadError(f"snapshot {path}: taken_at {taken!r} is not a timestamp")
    return Snapshot(system=system, records=records, taken_at=taken_at)


# ---------------------------------------------------------------- mapping


@dataclass
class Mapping:
    key: str
    fields: dict[str, str]
    updated_at: str | None = None
    grace_minutes: float = 30.0
    fill_drop_threshold: float = 0.2

    @property
    def dest_key(self) -> str:
        return self.fields[self.key]

    @property
    def dest_updated_at(self) -> str | None:
        if self.updated_at is None:
            return None
        return self.fields.get(self.updated_at)


def load_mapping(path: str | Path) -> Mapping:
    data = _read_json(Path(path), "mapping")
    if not isinstance(data, dict):
        raise LoadError(f"mapping {path} must be a JSON object")
    key = data.get("key")
    fields = data.get("fields")
    if not isinstance(key, str) or not key:
        raise LoadError(f"mapping {path}: 'key' must name the source key field")
    if not isinstance(fields, dict) or not fields:
        raise LoadError(f"mapping {path}: 'fields' must map source field -> destination field")
    for src, dst in fields.items():
        if not isinstance(src, str) or not isinstance(dst, str) or not src or not dst:
            raise LoadError(f"mapping {path}: every field name must be a non-empty string")
    if key not in fields:
        raise LoadError(f"mapping {path}: key {key!r} must also appear in 'fields'")
    updated_at = data.get("updated_at")
    if updated_at is not None and not isinstance(updated_at, str):
        raise LoadError(f"mapping {path}: 'updated_at' must be a field name")
    try:
        grace = float(data.get("grace_minutes", 30))
        threshold = float(data.get("fill_drop_threshold", 0.2))
    except (TypeError, ValueError):
        raise LoadError(f"mapping {path}: grace_minutes and fill_drop_threshold must be numbers") from None
    if grace < 0:
        raise LoadError(f"mapping {path}: grace_minutes cannot be negative")
    if not 0 < threshold <= 1:
        raise LoadError(f"mapping {path}: fill_drop_threshold must be between 0 and 1")
    return Mapping(
        key=key,
        fields=dict(fields),
        updated_at=updated_at,
        grace_minutes=grace,
        fill_drop_threshold=threshold,
    )


# ---------------------------------------------------------------- runs


@dataclass
class Run:
    run_at: datetime | None
    status: str
    rows_moved: int | None  # None when the log does not say. Unknown is not zero.


SUCCESS_WORDS = {"success", "succeeded", "ok", "completed", "complete", "done"}


def load_runs(path: str | Path) -> list[Run]:
    data = _read_json(Path(path), "runs")
    if isinstance(data, dict):
        data = data.get("runs")
    if not isinstance(data, list):
        raise LoadError(f"runs {path} must be a list of runs or an object with 'runs'")
    runs: list[Run] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise LoadError(f"runs {path}: entry {i} is not an object")
        moved = item.get("rows_moved")
        if moved is not None and (isinstance(moved, bool) or not isinstance(moved, (int, float))):
            raise LoadError(f"runs {path}: entry {i} rows_moved must be a number")
        runs.append(
            Run(
                run_at=parse_time(item.get("run_at")),
                status=str(item.get("status", "")).strip().lower(),
                rows_moved=int(moved) if moved is not None else None,
            )
        )
    return runs


def latest_run(runs: list[Run]) -> Run | None:
    """The most recent run. On a timestamp tie, the one later in the log wins.

    Timestamps are often only to the second, so two runs can share one. A plain
    max() returns the first of a tie, which would read an old healthy run as
    the latest and hide a silent failure behind it.
    """
    if not runs:
        return None
    if all(r.run_at is not None for r in runs):
        index = max(range(len(runs)), key=lambda i: (runs[i].run_at, i))
        return runs[index]
    return runs[-1]


# ---------------------------------------------------------------- state


@dataclass
class State:
    """What the watchdog saw on its last run. Empty on the first run."""

    source_fields: set[str] | None = None
    dest_fill: dict[str, float] | None = None
    checked_at: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def load_state(path: str | Path) -> State:
    p = Path(path)
    if not p.exists():
        return State()
    data = _read_json(p, "state")
    if not isinstance(data, dict):
        raise LoadError(f"state {path} must be a JSON object")
    fields = data.get("source_fields")
    fill = data.get("dest_fill")
    return State(
        source_fields=set(fields) if isinstance(fields, list) else None,
        dest_fill={str(k): float(v) for k, v in fill.items()} if isinstance(fill, dict) else None,
        checked_at=data.get("checked_at"),
    )


def save_state(state: State, path: str | Path) -> None:
    payload = {
        "checked_at": state.checked_at,
        "source_fields": sorted(state.source_fields) if state.source_fields is not None else None,
        "dest_fill": state.dest_fill,
    }
    Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
