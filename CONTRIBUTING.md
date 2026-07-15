# Contributing (internal)

This is an internal Beeline template maintained by the Platform team.

## Local setup

```bash
make env && make up && make migrate && make seed-demo   # running stack
# backend/.venv must exist for the gates (deps managed with uv):
cd backend && uv sync       # creates .venv from pyproject + uv.lock
pre-commit install          # optional but recommended
```

## Before opening a PR

```bash
make ci          # lint + mypy(strict, src) + tests + frontend typecheck — must pass
```

- Keep `CLAUDE.md`, `docs/QUICKSTART.md`, and `docs/MODULES.md` in sync when you
  change architecture, commands, or the module pattern.
- New feature? Scaffold it with `make new-module NAME=…` and add tests under
  `backend/tests/modules/<name>/`.
- **Dependencies:** when you change `backend/pyproject.toml`, run `uv lock` and
  commit the updated `backend/uv.lock` so installs stay reproducible. (Frontend:
  commit the updated `frontend/package-lock.json`.)

## Conventions

- Commits: short imperative subject; reference the ticket where applicable.
- Branches: `feature/…`, `fix/…`, `chore/…`.
- Never commit real secrets. `.env` / `.env.prod` are git-ignored; only the
  `*.env.example` files are tracked. `detect-private-key` runs in pre-commit.
- Respect the invariants in `CLAUDE.md` (tenant_id from token only; repos
  `flush()` not `commit()`; transaction-scoped `search_path`; two Keycloak issuers).
