# Recipes avoid shell-specific syntax so they run under sh and Windows cmd alike.

API = uv --directory api
PY = uv run --project api
WEB = npm --prefix web

.PHONY: help install db-start db-stop db-reset up down logs lint fmt test corpus eval eval-oracle

help:
	@echo Targets: install db-start db-stop db-reset up down logs lint fmt test corpus eval eval-oracle

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
	$(PY) ruff check .
	$(PY) ruff format --check .
	$(WEB) run lint
	$(WEB) run typecheck

fmt:
	$(PY) ruff check --fix .
	$(PY) ruff format .

test:
	$(API) run pytest

# Regenerates corpus/ground_truth, corpus/documents, the manifest and the vendor seed.
corpus:
	$(PY) python -m corpus.build

# Real LLM run; results go to eval/results. Available from Stage 2.
eval:
	$(PY) python -m eval.run_eval

# Checks the eval harness against ground truth; nothing is saved.
eval-oracle:
	$(PY) python -m eval.run_eval --predictor oracle
