#!/usr/bin/env python3
"""Strip the seeded demo users from the Keycloak realm export.

The template ships seed users with known dev passwords so a fresh clone can
smoke-test login and the role-gated flows immediately: `demo` (admin) plus the
AI Business Analyst domain cast `requester`, `ba`, `business_owner`,
`executor`, `approver`. None of them must reach any shared/production
environment. Run this once, before deploying, to remove them all from
`keycloak/realm-export.json`.

    python backend/scripts/remove_demo_user.py [--username NAME ...] [--path <realm-export.json>]

Idempotent: a no-op for users that are already gone.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "keycloak" / "realm-export.json"

# Every human seed user shipped in realm-export.json (keep in sync with it).
_SEED_USERNAMES = (
    "demo",
    "requester",
    "ba",
    "business_owner",
    "executor",
    "approver",
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Remove the seeded demo users from the realm export.")
    ap.add_argument(
        "--username",
        action="append",
        default=None,
        help="username to remove; repeatable (default: all seed users: "
        + ", ".join(_SEED_USERNAMES)
        + ")",
    )
    ap.add_argument("--path", type=Path, default=_DEFAULT_PATH, help="path to realm-export.json")
    args = ap.parse_args()
    usernames = set(args.username) if args.username else set(_SEED_USERNAMES)

    realm = json.loads(args.path.read_text(encoding="utf-8"))
    users = realm.get("users", [])
    kept = [u for u in users if u.get("username") not in usernames]
    removed = sorted(u["username"] for u in users if u.get("username") in usernames)

    if not removed:
        print(f"No users {sorted(usernames)} found in {args.path} — nothing to do.")
        return 0

    realm["users"] = kept
    # Keycloak exports use 2-space indentation; keep a trailing newline.
    args.path.write_text(json.dumps(realm, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Removed {len(removed)} user(s) from {args.path}: {', '.join(removed)}.")
    print("Re-import the realm (wipe the keycloak-db volume, see docs/CLONING.md) to take effect.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
