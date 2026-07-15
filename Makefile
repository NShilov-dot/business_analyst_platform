# BA Platform (FastAPI + Keycloak + React) — developer task runner.
# Run `make` or `make help` to list targets.
#
# Environments are separate compose stacks:
#   dev  (default): docker-compose.yml + docker-compose.dev.yml   (.env)
#   prod          : docker-compose.yml + docker-compose.prod.yml  (.env.prod)
#
# Pick the env with ENV=prod, or use the prod-* aliases:
#   make up                 # dev
#   make up ENV=prod        # prod   (≡ make prod-up)

SHELL    := /usr/bin/env bash
BACKEND  := backend
ENV      ?= dev

ifeq ($(ENV),prod)
  COMPOSE := docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml
else ifeq ($(ENV),dev)
  COMPOSE := docker compose -f docker-compose.yml -f docker-compose.dev.yml
else
  $(error ENV must be 'dev' or 'prod', got '$(ENV)')
endif

.DEFAULT_GOAL := help

.PHONY: help env env-prod gen-key tls-dev-cert \
        up down restart build logs ps clean config \
        prod-up prod-down prod-restart prod-build prod-logs prod-ps prod-migrate \
        migrate seed-demo revision new-module psql redis-cli backend-shell \
        test lint fmt typecheck check ci fe-install fe-build fe-check fe-dev dev-backend

help: ## Show this help
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n",$$1,$$2}'

# --------------------------------------------------------------------------
# Environment files
# --------------------------------------------------------------------------

env: ## Scaffold ./.env (dev) from backend/.env.example with a fresh encryption key
	@if [ -f .env ]; then echo ".env already exists — not overwriting"; else \
	  cp $(BACKEND)/.env.example .env; \
	  printf '\nSESSION_ENCRYPTION_KEYS=%s\n' "$$($(MAKE) -s gen-key)" >> .env; \
	  echo "Created .env (dev)."; fi

env-prod: ## Scaffold ./.env.prod from .env.prod.example (then fill REAL secrets!)
	@if [ -f .env.prod ]; then echo ".env.prod already exists — not overwriting"; else \
	  cp .env.prod.example .env.prod; \
	  echo "Created .env.prod — edit it and replace every CHANGE_ME (incl. SESSION_ENCRYPTION_KEYS, run 'make gen-key')."; fi

gen-key: ## Print a fresh base64 AES-256 key for SESSION_ENCRYPTION_KEYS
	@cd $(BACKEND) && PYTHONPATH=src .venv/bin/python -c \
	  "from app.core.crypto import generate_key; print(generate_key())"

tls-dev-cert: ## Generate a self-signed cert into nginx/certs (for local prod testing only)
	@mkdir -p nginx/certs
	openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
	  -keyout nginx/certs/privkey.pem -out nginx/certs/fullchain.pem \
	  -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost"
	@echo "Self-signed cert written to nginx/certs/ (NOT for real production)."

# --------------------------------------------------------------------------
# Stack lifecycle (ENV=dev default; ENV=prod or prod-* for production)
# --------------------------------------------------------------------------

up: ## Build & start the stack  (ENV=dev|prod)
	$(COMPOSE) up -d --build

down: ## Stop the stack (keep volumes)  (ENV=dev|prod)
	$(COMPOSE) down

restart: down up ## Restart the stack

build: ## Build images  (ENV=dev|prod)
	$(COMPOSE) build

logs: ## Tail logs (one service: make logs svc=app)
	$(COMPOSE) logs -f $(svc)

ps: ## Show running services
	$(COMPOSE) ps

config: ## Render & validate the merged compose config
	$(COMPOSE) config

clean: ## Stop the stack AND remove volumes (DESTROYS db/redis data)
	$(COMPOSE) down -v

# --- Production aliases (explicit ENV=prod) ---
prod-up:      ## Build & start the PRODUCTION stack
	$(MAKE) up ENV=prod
prod-down:    ## Stop the production stack
	$(MAKE) down ENV=prod
prod-restart: ## Restart the production stack
	$(MAKE) restart ENV=prod
prod-build:   ## Build production images
	$(MAKE) build ENV=prod
prod-logs:    ## Tail production logs (svc=app)
	$(MAKE) logs ENV=prod svc=$(svc)
prod-ps:      ## Show production services
	$(MAKE) ps ENV=prod
prod-migrate: ## Apply public migrations in the production stack
	$(MAKE) migrate ENV=prod

# --------------------------------------------------------------------------
# Database / Keycloak / Redis (inside the running stack; honors ENV)
# --------------------------------------------------------------------------

migrate: ## Apply public-schema migrations (two-head setup: public + tenant)
	$(COMPOSE) exec app alembic -x scope=public upgrade public@head

seed-demo: ## Seed the demo tenant (matches the realm-export seed users) so /v1/tasks works
	$(COMPOSE) exec -T postgres psql -U app -d app -c \
	  "INSERT INTO public.tenants (id, slug, name) VALUES ('11111111-1111-1111-1111-111111111111','demo','Demo Org') ON CONFLICT DO NOTHING;"
	$(COMPOSE) exec -T app alembic -x scope=tenant -x schema=tenant_demo upgrade tenant@head
	$(COMPOSE) exec -T postgres psql -U app -d app -c \
	  "INSERT INTO tenant_demo.departments (id, name, description, is_active, created_at, updated_at) VALUES \
	   ('dddddddd-0000-0000-0000-000000000001','Разработка ПО','Продуктовая backend/frontend разработка',true,now(),now()), \
	   ('dddddddd-0000-0000-0000-000000000002','Мобильная разработка','iOS/Android приложения самообслуживания',true,now(),now()), \
	   ('dddddddd-0000-0000-0000-000000000003','Инфраструктура и эксплуатация (SRE/DevOps)','Кластеры, CI/CD, надёжность',true,now(),now()), \
	   ('dddddddd-0000-0000-0000-000000000004','Сеть и NOC','Сетевая инфраструктура и мониторинг 24/7',true,now(),now()), \
	   ('dddddddd-0000-0000-0000-000000000005','Данные и аналитика','Хранилище данных, BI, ML',true,now(),now()), \
	   ('dddddddd-0000-0000-0000-000000000006','Информационная безопасность','ИБ, комплаенс, аудит',true,now(),now()), \
	   ('dddddddd-0000-0000-0000-000000000007','Биллинг и монетизация','Тарифы, начисления, платежи',true,now(),now()), \
	   ('dddddddd-0000-0000-0000-000000000008','Интеграции и API','Внешние и внутренние интеграции',true,now(),now()), \
	   ('dddddddd-0000-0000-0000-000000000009','QA и тестирование','Ручное и автоматизированное тестирование',true,now(),now()), \
	   ('dddddddd-0000-0000-0000-000000000010','Архитектура и R&D','Целевая архитектура и стандарты',true,now(),now()), \
	   ('dddddddd-0000-0000-0000-000000000011','Поддержка сервисов','L2/L3 поддержка продуктов',true,now(),now()), \
	   ('dddddddd-0000-0000-0000-000000000012','Цифровые каналы','Веб-портал и личный кабинет',true,now(),now()) \
	   ON CONFLICT (id) DO NOTHING;"
	@echo "Demo tenant seeded (12 departments). Login at http://localhost:3000 (password for all: demo-password-123)"
	@echo "core users: demo (admin), requester, ba, business_owner, executor, approver"
	@echo "extra directory users (for the triage picker): cd backend && .venv/bin/python scripts/seed_directory.py"

revision: ## Autogenerate a migration: make revision m="message"
	$(COMPOSE) exec app alembic revision --autogenerate -m "$(m)"

new-module: ## Scaffold a hexagonal module: make new-module NAME=invoices [ENTITY=Invoice]
	@test -n "$(NAME)" || { echo "Usage: make new-module NAME=<name> [ENTITY=<Class>]"; exit 2; }
	cd $(BACKEND) && PYTHONPATH=src .venv/bin/python scripts/scaffold_module.py \
	  --name $(NAME) $(if $(ENTITY),--entity $(ENTITY),)

psql: ## Open psql in the postgres container
	$(COMPOSE) exec postgres psql -U app -d app

redis-cli: ## Open redis-cli in the redis container
	$(COMPOSE) exec redis redis-cli

backend-shell: ## Open a shell in the app container
	$(COMPOSE) exec app bash

# --------------------------------------------------------------------------
# Quality gates (local venv)
# --------------------------------------------------------------------------

test: ## Run backend tests
	cd $(BACKEND) && PYTHONPATH=src .venv/bin/python -m pytest tests -q

lint: ## ruff lint (backend)
	cd $(BACKEND) && .venv/bin/ruff check src tests

fmt: ## ruff autofix + format (backend)
	cd $(BACKEND) && .venv/bin/ruff check --fix src tests && .venv/bin/ruff format src tests

typecheck: ## mypy (backend)
	cd $(BACKEND) && PYTHONPATH=src .venv/bin/mypy src

check: lint typecheck test ## Run lint + typecheck + tests (backend)

ci: check fe-check ## Full CI gate (backend gates + frontend typecheck) — CI entrypoint

# --------------------------------------------------------------------------
# Frontend
# --------------------------------------------------------------------------

fe-install: ## Install frontend deps (npm ci)
	cd frontend && npm ci

fe-build: ## Build the frontend production bundle
	cd frontend && npm run build

fe-check: ## Type-check the frontend without emitting (tsc --noEmit)
	cd frontend && npx tsc --noEmit

fe-dev: ## Run the Vite dev server
	cd frontend && npm run dev

# --------------------------------------------------------------------------
# Local (non-docker) backend dev — loads ./.env, talks to docker-exposed ports
# --------------------------------------------------------------------------

dev-backend: ## Run uvicorn locally with reload (needs `make up` for postgres/redis/keycloak)
	cd $(BACKEND) && set -a && . ../.env && set +a && \
	  PYTHONPATH=src .venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
