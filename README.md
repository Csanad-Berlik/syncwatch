# syncwatch

Catches the sync that says success while the data is wrong.

It reads both systems directly and compares them. It never trusts what the sync says about itself.

## The problem

Most syncs between two tools fail without an error. The run log says success. The dashboard is green. The data is wrong.

Three ways this happens, all common:

1. **A webhook arrives twice.** The provider did not get a fast enough reply, so it sent the event again. The sync inserts without checking, and the same contact now exists twice.
2. **A field gets renamed.** Someone renames `company` to `company_name` in the CRM settings. The sync still reads `company`, gets nothing back, and writes that nothing over the real value.
3. **A token expires.** The fetch fails, the code catches the error and treats it as "no new data", and the run is logged as a success with 0 rows. New records pile up and never arrive.

None of these throw. You find out from a bad campaign or an angry client.

## Try it

No accounts needed. The demo runs two fake systems on your machine.

```
pip install -e ".[dev]"
./demo/run_demo.sh
```

`./demo/run_demo.sh --pause` waits for Enter between scenes, for recording.

## What the demo shows

Each scene starts clean, breaks one thing, runs the sync, then runs the watchdog.

| Scene | What breaks | Sync's own log | Watchdog |
| --- | --- | --- | --- |
| 0 | nothing | success, 20 rows | clean |
| 1 | 5 webhooks redelivered | success, 5 rows | `duplicate_keys` |
| 2 | `company` renamed in the CRM | success, 10 rows | `missing_source_field`, with a rename hint, and `fill_rate_drop` |
| 3 | sync's token expires | success, 0 rows | `unsynced_records`, `silent_success` |

The sync in `demo/sync.py` is written the way many real syncs are. Its three bugs are marked `BUG 1`, `BUG 2`, `BUG 3`. They are left in on purpose.

## The five checks

```
syncwatch checks
```

| Check | Question it answers |
| --- | --- |
| `duplicate_keys` | Does the same record exist more than once in the destination? |
| `missing_source_field` | Is the sync reading a field that no longer exists in the source? |
| `fill_rate_drop` | Did a destination column that used to be full suddenly empty? |
| `unsynced_records` | Does the source have records, or newer versions, the destination never got? |
| `silent_success` | Did the sync report success and move nothing while records were waiting? |

## Using it on real systems

```
syncwatch check \
  --source crm.json --dest sheet.json --mapping mapping.json \
  --runs runs.json --state watch-state.json
```

It needs four files and a fifth it writes itself.

**Snapshots.** Every record from one system, flat:

```json
{
  "system": "crm",
  "taken_at": "2026-09-26T14:00:00Z",
  "records": [
    {"email": "anna@northwind.io", "company": "Northwind", "updated_at": "2026-09-26T13:52:11Z"}
  ]
}
```

**Mapping.** Which source field lands in which destination field:

```json
{
  "key": "email",
  "updated_at": "updated_at",
  "fields": {
    "email": "email",
    "company": "company",
    "updated_at": "source_updated_at"
  },
  "grace_minutes": 30,
  "fill_drop_threshold": 0.2
}
```

`grace_minutes` is how long a record can be missing before it counts. Set it to a bit more than the sync interval.

**Runs.** The sync's own log. Only `silent_success` uses it.

```json
[{"run_at": "2026-09-26T14:00:00Z", "status": "success", "rows_moved": 0}]
```

**State.** Written by syncwatch after each check. It remembers fill rates and field names so the next run can spot changes.

Getting snapshots out of real tools is the job of an adapter. **No real adapters exist yet.** That is the next piece of work.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | clean |
| 1 | something is wrong |
| 2 | could not read the inputs, or could not save state |

Schedule it and alert on anything that is not 0.

## Design decisions

**It reads both sides itself.** The sync's report is the thing that lies. The watchdog needs its own read access to each system. If it shared the sync's token, the same expiry would blind both.

**When it cannot see, it fails loudly.** A missing, unreadable or malformed input exits 2 with a plain message. A watchdog that goes quiet when its own read breaks has the same bug it exists to catch.

**A drop keeps alerting until it recovers.** If `company` drops from 100% to 50% filled, the old baseline is kept. Otherwise one alert would quietly become the new normal. Use `--rebaseline` after a deliberate change.

**Unknown is not zero.** If the run log has no `rows_moved`, `silent_success` stays off. Firing on a guess would be a false alarm dressed as evidence.

**Future timestamps are not trusted.** A record updated "two hours from now" usually means local time was stored as UTC. It is checked anyway and reported, rather than skipped as too recent.

**Findings print before state is saved.** If saving fails, you still see what was wrong.

## Known limits

**Wrong but non-empty values are not caught.** If the sync writes the wrong company into the right field, every check passes. Comparing values directly is the obvious next check. It is not in yet, because formatting differences like phone numbers and capitalisation would make it noisy.

**Deletions are not checked.** A record deleted in the source but still in the destination is not reported.

**Adapters must write every mapped field, even when empty.** If an adapter drops null properties and a field happens to be empty on every record, `missing_source_field` will report it as gone.

**Small tables are noisy.** With three rows, clearing one value is a 33% drop and triggers `fill_rate_drop`. Raise `fill_drop_threshold` for small tables.

**Keys are compared trimmed and lowercased.** Right for emails. If your keys are case-sensitive ids, two different records could be treated as one.

**Snapshots load fully into memory.** Fine for thousands of records. Not built for millions.

## Bugs found by probing it

The watchdog was tested against its own blind spots after the first version worked. Four real bugs came out, each now has a test:

1. Two sync runs logged in the same second. `max()` returned the first of the tie, so an old healthy run was read as the latest and `silent_success` never fired.
2. Timestamps in the future fell inside the grace window and were skipped forever. A missing record went unreported.
3. A run log with no `rows_moved` was read as 0, so `silent_success` fired on a guess.
4. When state could not be saved, the tool exited before printing its findings.

## Tests

```
pytest
```

34 tests. Unit tests for each check, the four bugs above, the CLI exit codes, and the full demo story as an end-to-end test. Checked on Python 3.10 to 3.13.

## Related

[clayspec](https://github.com/Csanad-Berlik/clayspec) does the same kind of check for one specific failure inside Clay: a formula pointing at a column id that no longer exists.
