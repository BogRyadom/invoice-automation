# Recipes avoid shell-specific syntax so they run under sh and Windows cmd alike.

API = uv --directory api
WEB = npm --prefix web

.PHONY: help install db-start db-stop db-reset up down logs lint fmt test

help:
	@echo Targets: install db-start db-stop db-reset up down logs lint fmt test

install:
	npm ci
	$(API) sync
	$(WEB) ci

db-start:
	npx supabase start

db-stop:
	npx supabase stop

# Recreates the LOCAL database and reapplies all migrations.
db-reset:
	npx supabase db reset

up: db-start
	docker compose up --build -d

down:
	docker compose down
	npx supabase stop

logs:
	docker compose logs -f

lint:
	$(API) run ruff check .
	$(API) run ruff format --check .
	$(WEB) run lint
	$(WEB) run typecheck

fmt:
	$(API) run ruff check --fix .
	$(API) run ruff format .

test:
	$(API) run pytest
