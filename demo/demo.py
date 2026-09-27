"""Drive the demo one step at a time.

  python -m demo.demo reset            fresh CRM and sheet
  python -m demo.demo seed 20          add 20 contacts to the CRM
  python -m demo.demo sync             run the sync once
  python -m demo.demo snapshot         dump both systems to JSON for the watchdog
  python -m demo.demo break duplicate  replay the last 5 webhooks
  python -m demo.demo break rename     rename company -> company_name, then edit 10 contacts
  python -m demo.demo break auth       expire the sync's token, then add 5 contacts
"""

from __future__ import annotations

import json
import sys

from demo import sync
from demo.systems import (
    base_dir,
    crm_add,
    crm_all_contacts,
    crm_rename_field,
    crm_reset,
    crm_set_token_valid,
    crm_touch,
    now_iso,
    sheet_all_rows,
    sheet_reset,
)


def snapshot() -> None:
    base = base_dir()
    taken = now_iso()
    (base / "crm.json").write_text(
        json.dumps({"system": "crm", "taken_at": taken, "records": crm_all_contacts()}, indent=2),
        encoding="utf-8",
    )
    (base / "sheet.json").write_text(
        json.dumps({"system": "sheet", "taken_at": taken, "records": sheet_all_rows()}, indent=2),
        encoding="utf-8",
    )


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]

    if cmd == "reset":
        crm_reset()
        sheet_reset()
        (base_dir() / "watch-state.json").unlink(missing_ok=True)
        print("reset: empty CRM, empty sheet")
    elif cmd == "seed":
        n = int(rest[0]) if rest else 20
        crm_add(n, seed=7)
        print(f"crm: added {n} contacts")
    elif cmd == "sync":
        r = sync.run()
        print(f"sync log: status={r['status']}  rows_moved={r['rows_moved']}")
    elif cmd == "snapshot":
        snapshot()
        print(f"snapshot: {len(crm_all_contacts())} contacts in crm, {len(sheet_all_rows())} rows in sheet")
    elif cmd == "break":
        kind = rest[0] if rest else ""
        if kind == "duplicate":
            sync.rewind(5)
            print("broke it: the webhook provider redelivered the last 5 events")
        elif kind == "rename":
            crm_rename_field("company", "company_name")
            crm_touch(10)
            print("broke it: 'company' renamed to 'company_name' in the CRM, then 10 contacts edited")
        elif kind == "auth":
            crm_set_token_valid(False)
            crm_add(5, seed=99)
            print("broke it: the sync's token expired, then 5 new contacts were added")
        else:
            print("break what? duplicate | rename | auth")
            return 2
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
