"""The demo story, as a test. Each scene must fire exactly the checks it claims to."""

import pytest

from demo import demo, sync
from demo.systems import base_dir
from syncwatch import load_mapping, load_runs, load_snapshot, load_state, run_checks, save_state
from syncwatch.checks import Severity

MAPPING = "demo/mapping.json"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNCWATCH_DEMO_DIR", str(tmp_path))


def watch():
    base = base_dir()
    findings, state = run_checks(
        load_snapshot(base / "crm.json"),
        load_snapshot(base / "sheet.json"),
        load_mapping(MAPPING),
        load_runs(base / "runs.json"),
        load_state(base / "watch-state.json"),
    )
    save_state(state, base / "watch-state.json")
    return {f.check for f in findings if f.severity is not Severity.INFO}


def baseline():
    demo.main(["reset"])
    demo.main(["seed", "20"])
    sync.run()
    demo.snapshot()
    assert watch() == set()


def after(kind):
    baseline()
    demo.main(["break", kind])
    entry = sync.run()
    assert entry["status"] == "success"  # the sync always thinks it is fine
    demo.snapshot()
    return watch()


def test_healthy_sync_is_clean():
    baseline()
    sync.run()
    demo.snapshot()
    assert watch() == set()


def test_redelivered_webhooks():
    assert after("duplicate") == {"duplicate_keys"}


def test_renamed_field():
    assert after("rename") == {"missing_source_field", "fill_rate_drop"}


def test_expired_token():
    assert after("auth") == {"unsynced_records", "silent_success"}
