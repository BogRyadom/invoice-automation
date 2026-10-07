# Recipes avoid shell-specific syntax so they run under sh and Windows cmd alike.

API = uv --directory api
PY = uv run --project api
WEB = npm --prefix web

.PHONY: help install db-start db-stop db-reset up down logs lint fmt test corpus eval eval-smoke eval-oracle demo-send demo-vendors n8n-import

help:
	@echo Targets: install db-start db-stop db-reset up down logs lint fmt test corpus eval eval-smoke eval-oracle demo-send demo-vendors n8n-import

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
	$(WEB) test

# Regenerates corpus/ground_truth, corpus/documents, the manifest and the vendor seed.
corpus:
	$(PY) python -m corpus.build

# Real LLM run on the whole corpus; results go to eval/results.
eval:
	$(PY) python -m eval.run_eval

# Real LLM run on three documents to check the setup within rate limits; nothing is saved.
eval-smoke:
	$(PY) python -m eval.run_eval --smoke

# Checks the eval harness against ground truth; nothing is saved.
eval-oracle:
	$(PY) python -m eval.run_eval --predictor oracle

# Sends the synthetic corpus to the running API as if n8n had delivered it from Gmail.
# Add ARGS="--fresh" to send it again as new e-mails.
demo-send:
	$(PY) python -m scripts.demo_send $(ARGS)

# Adds the fictional known vendors to the local database so the demo can auto-approve.
demo-vendors:
	$(PY) python -m scripts.demo_vendors

# Loads the workflows from n8n/ into the local n8n and publishes the events webhook.
# invoice_ingest is published from the n8n UI once its Gmail credential is set.
n8n-import:
	docker compose cp n8n/error_handler.json n8n:/tmp/error_handler.json
	docker compose cp n8n/invoice_events.json n8n:/tmp/invoice_events.json
	docker compose cp n8n/invoice_ingest.json n8n:/tmp/invoice_ingest.json
	docker compose exec -T n8n n8n import:workflow --input=/tmp/error_handler.json
	docker compose exec -T n8n n8n import:workflow --input=/tmp/invoice_events.json
	docker compose exec -T n8n n8n import:workflow --input=/tmp/invoice_ingest.json
	docker compose exec -T n8n n8n publish:workflow --id=invoiceEvents001
	docker compose restart n8n
