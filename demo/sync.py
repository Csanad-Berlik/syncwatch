"""The sync being watched.

This is written the way a lot of real syncs are written: short, readable,
and wrong in three ways that never raise an error. Each one is marked.
Do not fix them. The point of the demo is that the watchdog catches them.
"""

from __future__ import annotations

import json
import sqlite3

from demo.systems import AuthError, _connect, crm_fetch_events, now_iso, runs_path


def run() -> dict:
    with _connect("sheet.db") as sheet:
        cursor = sheet.execute("SELECT last_event_id FROM cursor").fetchone()["last_event_id"]

        try:
            events = crm_fetch_events(since_id=cursor)
        except AuthError:
            events = []  # BUG 3: an auth failure is treated as "nothing new"

        moved = 0
        for event in events:
            p = event["payload"]
            if event["type"] == "contact.created":
                # BUG 1: inserts without checking whether the contact is already here,
                # so a redelivered webhook writes the same contact twice
                sheet.execute(
                    "INSERT INTO rows (email, company, title, phone, source_updated_at) VALUES (?, ?, ?, ?, ?)",
                    (p["email"], p.get("company"), p.get("title"), p.get("phone"), p.get("updated_at")),
                )
            elif event["type"] == "contact.updated":
                # BUG 2: .get("company") returns None if the field was renamed,
                # and None is written straight over the real value
                sheet.execute(
                    "UPDATE rows SET company = ?, title = ?, phone = ?, source_updated_at = ? WHERE email = ?",
                    (p.get("company"), p.get("title"), p.get("phone"), p.get("updated_at"), p["email"]),
                )
            moved += 1
            cursor = event["id"]

        sheet.execute("UPDATE cursor SET last_event_id = ?", (cursor,))

    entry = {"run_at": now_iso(), "status": "success", "rows_moved": moved}
    log = json.loads(runs_path().read_text(encoding="utf-8"))
    log.append(entry)
    runs_path().write_text(json.dumps(log, indent=2) + "\n", encoding="utf-8")
    return entry


def rewind(n: int) -> None:
    """Move the cursor back n events. This is what a webhook redelivery looks like:
    the provider did not get a 200 in time, so it sends the same events again."""
    with _connect("sheet.db") as sheet:
        cur = sheet.execute("SELECT last_event_id FROM cursor").fetchone()["last_event_id"]
        sheet.execute("UPDATE cursor SET last_event_id = ?", (max(0, cur - n),))
