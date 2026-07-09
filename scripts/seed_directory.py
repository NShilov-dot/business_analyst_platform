#!/usr/bin/env python3
"""Seed extra demo users into Keycloak so the triage business-owner / executor
pickers have a realistic directory to search.

Dev-only, idempotent. Standalone (httpx + master-admin REST) so it can run from
the host venv against the docker-exposed Keycloak without importing the app:

    cd backend && .venv/bin/python scripts/seed_directory.py

Env overrides (sensible dev defaults):
    KC_URL         http://localhost:8080
    KC_REALM       saas
    KC_ADMIN_USER  admin
    KC_ADMIN_PASS  admin
    TENANT_ID      11111111-1111-1111-1111-111111111111   (the demo tenant)
    SEED_PASSWORD  demo-password-123

Each user is created enabled+verified, gets the ``tenant_id`` attribute (this is
what the /v1/directory/users query filters on — the run VERIFIES it persisted),
a permanent password, and its realm roles. Existing users are left untouched.

Departments are seeded separately via SQL (see the accompanying psql step / the
`make seed-demo` pattern) — this script only touches Keycloak.
"""

from __future__ import annotations

import os
import sys

import httpx

KC_URL = os.environ.get("KC_URL", "http://localhost:8080").rstrip("/")
REALM = os.environ.get("KC_REALM", "saas")
ADMIN_USER = os.environ.get("KC_ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("KC_ADMIN_PASS", "admin")
TENANT_ID = os.environ.get("TENANT_ID", "11111111-1111-1111-1111-111111111111")
SEED_PASSWORD = os.environ.get("SEED_PASSWORD", "demo-password-123")

# (username, first_name, last_name, [realm roles])
USER_SPECS: list[tuple[str, str, str, list[str]]] = [
    ("dilnoza.yusupova", "Dilnoza", "Yusupova", ["tenant_user", "business_owner"]),
    ("rustam.abdullaev", "Rustam", "Abdullaev", ["tenant_user", "business_owner"]),
    ("kamola.nazarova", "Kamola", "Nazarova", ["tenant_user", "business_owner"]),
    ("jasur.ibragimov", "Jasur", "Ibragimov", ["tenant_user", "executor"]),
    ("malika.sobirova", "Malika", "Sobirova", ["tenant_user", "executor"]),
    ("otabek.rakhimov", "Otabek", "Rakhimov", ["tenant_user", "executor"]),
    ("farrukh.ismailov", "Farrukh", "Ismailov", ["tenant_user", "ba"]),
    ("sardor.mirzaev", "Sardor", "Mirzaev", ["tenant_user", "approver"]),
    ("nodira.kurbanova", "Nodira", "Kurbanova", ["tenant_user"]),
    ("bekzod.tureaev", "Bekzod", "Tureaev", ["tenant_user"]),
]


def _admin_token(c: httpx.Client) -> str:
    r = c.post(
        f"{KC_URL}/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": ADMIN_USER,
            "password": ADMIN_PASS,
        },
    )
    r.raise_for_status()
    return str(r.json()["access_token"])


def _ensure_unmanaged_attributes(c: httpx.Client) -> None:
    """Best-effort: allow unmanaged user attributes so ``tenant_id`` persists."""
    try:
        r = c.get(f"{KC_URL}/admin/realms/{REALM}/users/profile")
        if r.status_code != 200:
            return
        profile = r.json()
        if profile.get("unmanagedAttributePolicy") == "ENABLED":
            return
        profile["unmanagedAttributePolicy"] = "ENABLED"
        put = c.put(f"{KC_URL}/admin/realms/{REALM}/users/profile", json=profile)
        print(f"  user-profile unmanagedAttributePolicy=ENABLED -> {put.status_code}")
    except httpx.HTTPError as exc:  # pragma: no cover - best effort
        print(f"  (could not adjust user profile: {exc})")


def _role(c: httpx.Client, name: str) -> dict:
    r = c.get(f"{KC_URL}/admin/realms/{REALM}/roles/{name}")
    r.raise_for_status()
    return dict(r.json())


def main() -> int:
    with httpx.Client(timeout=20.0) as c:
        c.headers["Authorization"] = f"Bearer {_admin_token(c)}"

        _ensure_unmanaged_attributes(c)
        role_cache = {name: _role(c, name) for name in {"tenant_user", "ba", "business_owner", "executor", "approver"}}

        created, skipped, verified_fail = 0, 0, 0
        for username, first, last, roles in USER_SPECS:
            existing = c.get(
                f"{KC_URL}/admin/realms/{REALM}/users", params={"username": username, "exact": "true"}
            ).json()
            if existing:
                print(f"  skip   {username:<20} (exists, id={existing[0]['id'][:8]})")
                skipped += 1
                continue

            resp = c.post(
                f"{KC_URL}/admin/realms/{REALM}/users",
                json={
                    "username": username,
                    "email": f"{username}@demo.beeline.uz",
                    "firstName": first,
                    "lastName": last,
                    "enabled": True,
                    "emailVerified": True,
                    "attributes": {"tenant_id": [TENANT_ID]},
                },
            )
            if resp.status_code != 201:
                print(f"  FAIL   {username:<20} create -> {resp.status_code} {resp.text[:120]}")
                continue
            uid = resp.headers["Location"].rsplit("/", 1)[-1]

            c.put(
                f"{KC_URL}/admin/realms/{REALM}/users/{uid}/reset-password",
                json={"type": "password", "value": SEED_PASSWORD, "temporary": False},
            ).raise_for_status()

            c.post(
                f"{KC_URL}/admin/realms/{REALM}/users/{uid}/role-mappings/realm",
                json=[{"id": role_cache[r]["id"], "name": r} for r in roles],
            ).raise_for_status()

            # VERIFY the tenant_id attribute persisted — the directory query relies on it.
            check = c.get(f"{KC_URL}/admin/realms/{REALM}/users/{uid}").json()
            ok = (check.get("attributes") or {}).get("tenant_id") == [TENANT_ID]
            if not ok:
                verified_fail += 1
            print(
                f"  create {username:<20} id={uid[:8]} roles={roles} "
                f"tenant_id={'OK' if ok else 'MISSING!'}"
            )
            created += 1

        print(f"\nDone. created={created} skipped={skipped} tenant_id_missing={verified_fail}")
        if verified_fail:
            print(
                "WARNING: tenant_id attribute did not persist on some users — they will NOT appear "
                "in /v1/directory/users. Check the realm user-profile unmanagedAttributePolicy.",
                file=sys.stderr,
            )
            return 2
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
