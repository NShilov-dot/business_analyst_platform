SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE := docker compose
EXEC_APP := $(COMPOSE) exec -T app

.PHONY: help up down restart logs ps build \
        migrate migrate-public migrate-tenant provision-tenant \
        revision-public revision-tenant \
        test fmt lint typecheck shell psql

help: ## List targets
	@awk 'BEGIN{FS=":.*##"} /^[a-zA-Z_-]+:.*##/ {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

up: ## Start the compose stack (detached)
	$(COMPOSE) up -d

down: ## Stop the compose stack
	$(COMPOSE) down

restart: down up ## Restart the compose stack

logs: ## Tail app logs
	$(COMPOSE) logs -f app

ps: ## Show stack status
	$(COMPOSE) ps

build: ## Build the app image
	$(COMPOSE) build app

migrate: migrate-public ## Apply public-head migrations (tenant head is per-tenant)

migrate-public: ## Apply public-head migrations
	$(EXEC_APP) alembic -x scope=public upgrade public@head

migrate-tenant: ## Apply tenant-head to SCHEMA=tenant_<slug>
	@test -n "$(SCHEMA)" || (echo "usage: make migrate-tenant SCHEMA=tenant_<slug>" && exit 2)
	$(EXEC_APP) alembic -x scope=tenant -x schema=$(SCHEMA) upgrade tenant@head

provision-tenant: ## Create tenant + run tenant migrations. SLUG=acme NAME="ACME Corp"
	@test -n "$(SLUG)" -a -n "$(NAME)" || (echo "usage: make provision-tenant SLUG=acme NAME=\"ACME Corp\"" && exit 2)
	$(EXEC_APP) python scripts/provision_tenant.py --slug $(SLUG) --name "$(NAME)"

revision-public: ## Autogenerate public-head migration. MSG="add foo"
	@test -n "$(MSG)" || (echo "usage: make revision-public MSG=\"add foo\"" && exit 2)
	$(EXEC_APP) alembic -x scope=public revision --autogenerate -m "$(MSG)" --head public@head

revision-tenant: ## Autogenerate tenant-head migration. MSG="add bar" SCHEMA=tenant_<slug>
	@test -n "$(MSG)" -a -n "$(SCHEMA)" || (echo "usage: make revision-tenant MSG=\"add bar\" SCHEMA=tenant_<slug>" && exit 2)
	$(EXEC_APP) alembic -x scope=tenant -x schema=$(SCHEMA) revision --autogenerate -m "$(MSG)" --head tenant@head

test: ## Run pytest inside the app container
	$(EXEC_APP) pytest

fmt: ## Format code (ruff)
	$(EXEC_APP) ruff format src tests
	$(EXEC_APP) ruff check --fix src tests

lint: ## Lint (ruff + mypy)
	$(EXEC_APP) ruff check src tests
	$(EXEC_APP) mypy

typecheck: lint ## Alias for lint (includes mypy)

shell: ## Python REPL inside the app container
	$(EXEC_APP) python

psql: ## psql session against the app database
	$(COMPOSE) exec postgres psql -U app -d app
