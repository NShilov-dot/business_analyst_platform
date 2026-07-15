# Cloning this template into a new product

This repo is a starter template. After cloning it for a new product, run the
rebrand script once, then do a few manual follow-ups. Total time: ~15 minutes.

## 1. Rebrand (automated)

```bash
scripts/init-template.sh --name <slug> --display "<Display Name>"
# e.g.
scripts/init-template.sh --name beelinecrm --display "Beeline CRM"
```

`--name` is a machine slug (`^[a-z][a-z0-9]{1,30}$`). It becomes, everywhere at
once (src, config, docker-compose, Keycloak realm export, **and the tests**):

| Token (template)                 | Becomes                         |
|----------------------------------|---------------------------------|
| Keycloak realm `saas`            | `<slug>`                        |
| OIDC client `saas-backend`       | `<slug>-backend`                |
| Admin client `saas-backend-admin`| `<slug>-backend-admin`          |
| Session cookie `saas_session`    | `<slug>_session`                |
| Compose project `saas`           | `<slug>` (via `COMPOSE_PROJECT_NAME`) |
| Python pkg `saas-backend`        | `<slug>-backend`                |
| npm pkg `saas-frontend`          | `<slug>-frontend`               |
| Display `SaaS …`                 | `<Display Name> …`              |

Preview without writing: add `--dry-run`. The script edits only git-tracked
files; it skips itself and this guide.

> The script keeps everything self-consistent, so `make test` stays green
> immediately after rebranding (verified). Review with `git diff --stat` and commit.

## 2. Manual follow-ups

1. **Fresh secrets.** `make env` scaffolds `.env` with a freshly generated
   `SESSION_ENCRYPTION_KEYS`. Never reuse the template's committed dev values.
2. **Bring it up.** `make up && make migrate && make seed-demo`, then log in at
   http://localhost:3000 to smoke-test — `demo` (admin) or any of the domain
   seed users (`requester`, `ba`, `business_owner`, `executor`, `approver`;
   password for all: `demo-password-123`).
3. **Replace the example domain.** `modules/tasks` (backend) + the Tasks pages
   (frontend) are a reference implementation — replace them with your own bounded
   context. See [MODULES.md](MODULES.md) and `make new-module NAME=<name>`.
4. **Production secrets.** `make env-prod` then fill every `CHANGE_ME` in
   `.env.prod` (OIDC client secret, admin client secret, Redis password,
   `SESSION_ENCRYPTION_KEYS`, HTTPS issuers, `TRUSTED_HOSTS`). `config.py`'s
   `validate_for_production()` refuses to start prod with the template's dev
   secrets, so this is enforced — not optional.

## 3. Before any non-dev deployment: remove the demo seed

The Keycloak realm export ships seed users with known dev passwords (handy for
first-run smoke tests, unsafe to ship): `demo` (admin) plus the domain cast
`requester`, `ba`, `business_owner`, `executor`, `approver`. Remove them all:

```bash
python backend/scripts/remove_demo_user.py    # strips ALL seed users from realm-export.json
```

Then drop the matching dev data and the convenience target:
- delete the `seed-demo` target from the root `Makefile` (or leave it for dev only),
- in production the demo tenant is simply never seeded (no `make seed-demo`).

## 4. Things the script intentionally does NOT change

- **Generated encryption keys** — you must generate your own (`make env`).
- **The two-issuer Keycloak setup** — still `KEYCLOAK_ISSUER` (internal/backchannel)
  vs `KEYCLOAK_PUBLIC_ISSUER` (browser-facing). Set both correctly for prod
  (see `.env.prod.example` and [CLAUDE.md](../CLAUDE.md)).
- **Your product's actual domain logic** — that's yours to build.
