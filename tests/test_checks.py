from datetime import datetime, timedelta, timezone

from syncwatch import Mapping, Run, Severity, Snapshot, State, run_checks
from syncwatch.checks import rebaseline
from syncwatch.model import latest_run

T0 = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
MAPPING = Mapping(
    key="email",
    fields={"email": "email", "company": "company", "updated_at": "synced"},
    updated_at="updated_at",
    grace_minutes=10,
)


def src(*records, taken=T0):
    return Snapshot("crm", list(records), taken)


def dst(*records):
    return Snapshot("sheet", list(records), T0)


def c(email, company="Acme", mins_ago=60):
    return {"email": email, "company": company, "updated_at": (T0 - timedelta(minutes=mins_ago)).isoformat()}


def r(email, company="Acme", mins_ago=60):
    return {"email": email, "company": company, "synced": (T0 - timedelta(minutes=mins_ago)).isoformat()}


def checks(findings, severity=None):
    return [f.check for f in findings if severity is None or f.severity is severity]


def ok_run(moved):
    return [Run(T0 - timedelta(minutes=1), "success", moved)]


# ---------------------------------------------------------------- clean


def test_clean_pair_reports_nothing_but_baseline():
    findings, _ = run_checks(src(c("a@x.com"), c("b@x.com")), dst(r("a@x.com"), r("b@x.com")), MAPPING, ok_run(2))
    assert checks(findings) == ["baseline"]
    assert findings[0].severity is Severity.INFO


def test_second_clean_run_has_no_baseline_note():
    s, d = src(c("a@x.com")), dst(r("a@x.com"))
    _, state = run_checks(s, d, MAPPING)
    findings, _ = run_checks(s, d, MAPPING, state=state)
    assert findings == []


# ---------------------------------------------------------------- duplicate_keys


def test_duplicate_is_broken():
    findings, _ = run_checks(src(c("a@x.com")), dst(r("a@x.com"), r("a@x.com")), MAPPING)
    assert "duplicate_keys" in checks(findings, Severity.BROKEN)


def test_duplicate_ignores_case_and_whitespace():
    findings, _ = run_checks(src(c("a@x.com")), dst(r("a@x.com"), r(" A@X.com ")), MAPPING)
    assert "duplicate_keys" in checks(findings, Severity.BROKEN)


def test_keyless_destination_row_is_a_warning():
    findings, _ = run_checks(src(c("a@x.com")), dst(r("a@x.com"), r("")), MAPPING)
    assert "duplicate_keys" in checks(findings, Severity.WARNING)


# ---------------------------------------------------------------- missing_source_field


def test_renamed_field_is_broken_with_rename_hint():
    state = State(source_fields={"email", "company", "updated_at"}, dest_fill={})
    renamed = {"email": "a@x.com", "company_name": "Acme", "updated_at": c("a@x.com")["updated_at"]}
    findings, _ = run_checks(src(renamed), dst(r("a@x.com")), MAPPING, state=state)
    hit = [f for f in findings if f.check == "missing_source_field"]
    assert hit and hit[0].severity is Severity.BROKEN
    assert "company -> company_name" in hit[0].message


def test_empty_source_does_not_claim_missing_fields():
    findings, _ = run_checks(src(), dst(), MAPPING)
    assert "missing_source_field" not in checks(findings)


def test_good_schema_is_kept_while_a_field_is_missing():
    good = {"email", "company", "updated_at"}
    renamed = {"email": "a@x.com", "company_name": "Acme", "updated_at": c("a@x.com")["updated_at"]}
    _, state = run_checks(src(renamed), dst(r("a@x.com")), MAPPING, state=State(source_fields=good, dest_fill={}))
    assert state.source_fields == good  # so the rename hint survives the next run too


# ---------------------------------------------------------------- fill_rate_drop


def test_fill_drop_warns_against_previous_run():
    full = [r(f"{i}@x.com") for i in range(10)]
    half = [r(f"{i}@x.com", company=None if i < 5 else "Acme") for i in range(10)]
    s = src(*[c(f"{i}@x.com") for i in range(10)])
    _, state = run_checks(s, dst(*full), MAPPING)
    findings, _ = run_checks(s, dst(*half), MAPPING, state=state)
    hit = [f for f in findings if f.check == "fill_rate_drop"]
    assert hit and hit[0].severity is Severity.WARNING
    assert "100%" in hit[0].message and "50%" in hit[0].message


def test_fill_drop_keeps_alerting_until_it_recovers():
    s = src(*[c(f"{i}@x.com") for i in range(10)])
    full = dst(*[r(f"{i}@x.com") for i in range(10)])
    half = dst(*[r(f"{i}@x.com", company=None if i < 5 else "Acme") for i in range(10)])
    _, state = run_checks(s, full, MAPPING)
    _, state = run_checks(s, half, MAPPING, state=state)
    findings, _ = run_checks(s, half, MAPPING, state=state)
    assert "fill_rate_drop" in checks(findings)


def test_small_drop_under_threshold_is_quiet():
    s = src(*[c(f"{i}@x.com") for i in range(10)])
    full = dst(*[r(f"{i}@x.com") for i in range(10)])
    one_gone = dst(*[r(f"{i}@x.com", company=None if i == 0 else "Acme") for i in range(10)])
    _, state = run_checks(s, full, MAPPING)
    findings, _ = run_checks(s, one_gone, MAPPING, state=state)
    assert "fill_rate_drop" not in checks(findings)


def test_rebaseline_accepts_the_current_level():
    s = src(*[c(f"{i}@x.com") for i in range(10)])
    half = dst(*[r(f"{i}@x.com", company=None if i < 5 else "Acme") for i in range(10)])
    state = rebaseline(s, half, MAPPING)
    findings, _ = run_checks(s, half, MAPPING, state=state)
    assert "fill_rate_drop" not in checks(findings)


# ---------------------------------------------------------------- unsynced_records


def test_missing_record_is_broken():
    findings, _ = run_checks(src(c("a@x.com"), c("b@x.com")), dst(r("a@x.com")), MAPPING)
    hit = [f for f in findings if f.check == "unsynced_records"]
    assert hit and "b@x.com" in hit[0].message


def test_record_inside_grace_window_is_not_flagged():
    findings, _ = run_checks(src(c("a@x.com"), c("b@x.com", mins_ago=2)), dst(r("a@x.com")), MAPPING)
    assert "unsynced_records" not in checks(findings)


def test_stale_destination_copy_is_broken():
    findings, _ = run_checks(src(c("a@x.com", mins_ago=30)), dst(r("a@x.com", mins_ago=90)), MAPPING)
    hit = [f for f in findings if f.check == "unsynced_records"]
    assert hit and "old version" in hit[0].message


def test_record_without_timestamp_is_always_checked():
    no_ts = {"email": "b@x.com", "company": "Acme"}
    findings, _ = run_checks(src(c("a@x.com"), no_ts), dst(r("a@x.com")), MAPPING)
    assert "unsynced_records" in checks(findings)


# ---------------------------------------------------------------- silent_success


def test_success_with_zero_rows_and_a_backlog_is_broken():
    findings, _ = run_checks(src(c("a@x.com"), c("b@x.com")), dst(r("a@x.com")), MAPPING, ok_run(0))
    assert "silent_success" in checks(findings, Severity.BROKEN)


def test_success_with_zero_rows_and_nothing_waiting_is_fine():
    findings, _ = run_checks(src(c("a@x.com")), dst(r("a@x.com")), MAPPING, ok_run(0))
    assert "silent_success" not in checks(findings)


def test_a_loud_failure_is_not_called_silent():
    runs = [Run(T0, "failed", 0)]
    findings, _ = run_checks(src(c("a@x.com"), c("b@x.com")), dst(r("a@x.com")), MAPPING, runs)
    assert "silent_success" not in checks(findings)
    assert "unsynced_records" in checks(findings)


def test_timestamp_tie_goes_to_the_later_log_entry():
    """Found by running the demo: two runs in the same second, max() took the first."""
    runs = [Run(T0, "success", 20), Run(T0, "success", 0)]
    assert latest_run(runs).rows_moved == 0
    findings, _ = run_checks(src(c("a@x.com"), c("b@x.com")), dst(r("a@x.com")), MAPPING, runs)
    assert "silent_success" in checks(findings)


def test_broken_findings_sort_before_warnings_and_info():
    findings, _ = run_checks(src(c("a@x.com"), c("b@x.com")), dst(r("a@x.com"), r("")), MAPPING, ok_run(0))
    order = [f.severity for f in findings]
    assert order == sorted(order, key=lambda s: {"broken": 0, "warning": 1, "info": 2}[s.value])


# ---------------------------------------------------------------- found by probing the watchdog


def test_future_timestamp_does_not_hide_a_missing_record():
    """Local time stored as UTC looks 2h in the future. It must not count as 'too recent'."""
    future = (T0 + timedelta(hours=2)).replace(tzinfo=None).isoformat()
    record = {"email": "a@x.com", "company": "Acme", "updated_at": future}
    findings, _ = run_checks(src(record), dst(), MAPPING)
    assert "unsynced_records" in checks(findings, Severity.BROKEN)
    assert "unsynced_records" in checks(findings, Severity.WARNING)


def test_unknown_rows_moved_is_not_read_as_zero(tmp_path):
    import json
    from syncwatch import load_runs

    path = tmp_path / "runs.json"
    path.write_text(json.dumps([{"run_at": T0.isoformat(), "status": "success"}]))
    runs = load_runs(path)
    assert runs[0].rows_moved is None
    findings, _ = run_checks(src(c("a@x.com")), dst(), MAPPING, runs)
    assert "silent_success" not in checks(findings)
    assert "unsynced_records" in checks(findings)
