"""Two fake systems, small enough to read in one sitting.

crm    the source. Contacts, plus an outbox of events it would send as
       webhooks: contact.created and contact.updated. Access needs a token.
sheet  the destination. A plain table of rows, like Airtable or a sheet.

Both are SQLite files in one folder, so the demo needs no accounts.
"""

from __future__ import annotations

import json
import os
import random
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent


def base_dir() -> Path:
    path = Path(os.environ.get("SYNCWATCH_DEMO_DIR", HERE / "state"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect(name: str) -> sqlite3.Connection:
    conn = sqlite3.connect(base_dir() / name)
    conn.row_factory = sqlite3.Row
    return conn


class AuthError(Exception):
    """The token was rejected."""


# ---------------------------------------------------------------- crm


FIRST = ["anna", "bence", "csilla", "david", "eszter", "gabor", "hanna", "istvan",
         "julia", "kristof", "laura", "mate", "nora", "peter", "reka", "tamas",
         "viktoria", "zoltan", "emese", "levente", "dora", "adam", "flora", "balint"]
LAST = ["kovacs", "nagy", "toth", "szabo", "horvath", "varga", "kiss", "molnar"]
COMPANIES = {
    "Northwind": "northwind.io",
    "Lumen Labs": "lumenlabs.com",
    "Brightpath": "brightpath.co",
    "Quarry AI": "quarry.ai",
    "Fieldnote": "fieldnote.app",
    "Atlas Freight": "atlasfreight.com",
    "Cobalt HR": "cobalthr.com",
    "Mintline": "mintline.io",
}
TITLES = ["Head of Sales", "RevOps Lead", "Founder", "VP Marketing", "SDR Manager", "COO"]


def crm_reset() -> None:
    with _connect("crm.db") as db:
        db.executescript(
            """
            DROP TABLE IF EXISTS contacts;
            DROP TABLE IF EXISTS events;
            DROP TABLE IF EXISTS meta;
            CREATE TABLE contacts (
                email TEXT PRIMARY KEY, company TEXT, title TEXT, phone TEXT, updated_at TEXT
            );
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT, payload TEXT, created_at TEXT
            );
            CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
            INSERT INTO meta VALUES ('token_valid', '1');
            """
        )


def _contact_row(db: sqlite3.Connection, email: str) -> dict:
    row = db.execute("SELECT * FROM contacts WHERE email = ?", (email,)).fetchone()
    return dict(row)


def _emit(db: sqlite3.Connection, kind: str, email: str) -> None:
    payload = json.dumps(_contact_row(db, email))
    db.execute("INSERT INTO events (type, payload, created_at) VALUES (?, ?, ?)", (kind, payload, now_iso()))


def _columns(db: sqlite3.Connection) -> list[str]:
    return [r["name"] for r in db.execute("PRAGMA table_info(contacts)")]


def crm_add(n: int, seed: int = 7) -> list[str]:
    rng = random.Random(seed)
    added = []
    with _connect("crm.db") as db:
        existing = {r["email"] for r in db.execute("SELECT email FROM contacts")}
        company_col = "company" if "company" in _columns(db) else "company_name"
        while len(added) < n:
            company = rng.choice(list(COMPANIES))
            email = f"{rng.choice(FIRST)}.{rng.choice(LAST)}@{COMPANIES[company]}"
            if email in existing:
                continue
            existing.add(email)
            db.execute(
                f"INSERT INTO contacts (email, {company_col}, title, phone, updated_at) VALUES (?, ?, ?, ?, ?)",
                (email, company, rng.choice(TITLES),
                 f"+36 30 {rng.randint(100, 999)} {rng.randint(1000, 9999)}", now_iso()),
            )
            _emit(db, "contact.created", email)
            added.append(email)
    return added


def crm_touch(n: int) -> list[str]:
    """Change the title on n contacts, like a rep editing records."""
    with _connect("crm.db") as db:
        emails = [r["email"] for r in db.execute("SELECT email FROM contacts ORDER BY email LIMIT ?", (n,))]
        for email in emails:
            db.execute(
                "UPDATE contacts SET title = ?, updated_at = ? WHERE email = ?",
                ("Director of Revenue", now_iso(), email),
            )
            _emit(db, "contact.updated", email)
    return emails


def crm_rename_field(old: str, new: str) -> None:
    """An admin renames a property in the CRM settings. Nobody tells the sync."""
    with _connect("crm.db") as db:
        db.execute(f"ALTER TABLE contacts RENAME COLUMN {old} TO {new}")


def crm_set_token_valid(valid: bool) -> None:
    with _connect("crm.db") as db:
        db.execute("UPDATE meta SET v = ? WHERE k = 'token_valid'", ("1" if valid else "0",))


def crm_fetch_events(since_id: int) -> list[dict]:
    """What the sync calls. Rejects the call if the token has expired."""
    with _connect("crm.db") as db:
        valid = db.execute("SELECT v FROM meta WHERE k = 'token_valid'").fetchone()["v"] == "1"
        if not valid:
            raise AuthError("401 Unauthorized: token expired")
        rows = db.execute("SELECT * FROM events WHERE id > ? ORDER BY id", (since_id,)).fetchall()
        return [{"id": r["id"], "type": r["type"], "payload": json.loads(r["payload"])} for r in rows]


def crm_all_contacts() -> list[dict]:
    """The watchdog's own read. It uses its own access, not the sync's token."""
    with _connect("crm.db") as db:
        return [dict(r) for r in db.execute("SELECT * FROM contacts ORDER BY email")]


# ---------------------------------------------------------------- sheet


def sheet_reset() -> None:
    with _connect("sheet.db") as db:
        db.executescript(
            """
            DROP TABLE IF EXISTS rows;
            DROP TABLE IF EXISTS cursor;
            CREATE TABLE rows (
                row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT, company TEXT, title TEXT, phone TEXT, source_updated_at TEXT
            );
            CREATE TABLE cursor (last_event_id INTEGER);
            INSERT INTO cursor VALUES (0);
            """
        )
    runs_path().write_text("[]\n", encoding="utf-8")


def sheet_all_rows() -> list[dict]:
    with _connect("sheet.db") as db:
        rows = db.execute(
            "SELECT email, company, title, phone, source_updated_at FROM rows ORDER BY row_id"
        ).fetchall()
        return [dict(r) for r in rows]


def runs_path() -> Path:
    return base_dir() / "runs.json"
