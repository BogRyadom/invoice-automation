# Invoice Automation

Invoice processing from Gmail with verifiable extraction, deterministic validation and human review.

Portfolio project, work in progress. Current state: Stage 3 (checks, vendor matching, duplicates, routing, status machine and the queue worker). The specification and stage plan live in [docs/SPEC.md](docs/SPEC.md) (in Russian).

## Stack

n8n, FastAPI, Postgres (Supabase), Next.js. LLM provider: Groq.

## Prerequisites

- Docker Desktop, running
- GNU make, [uv](https://docs.astral.sh/uv/), Node.js 24
- On Windows: `winget install ezwinports.make astral-sh.uv`, then open a new terminal

## Quickstart

```sh
cp .env.example .env
make install
make up
```

`make up` starts the local Supabase stack (Postgres, Storage, Auth) and then the api, worker and web containers.

| Service | URL |
|---|---|
| API health | http://localhost:8000/health |
| Review UI | http://localhost:3000 |
| Supabase Studio | http://127.0.0.1:54323 |

Supabase keys for `.env` (`SUPABASE_SECRET_KEY`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`) are printed by `npx supabase status`. Stage 0 does not need them yet.

Stop everything with `make down`.

## Development

| Command | What it does |
|---|---|
| `make lint` | ruff check, ruff format check, eslint, tsc |
| `make test` | pytest. DB tests use `TEST_DATABASE_URL` and the local Supabase Postgres |
| `make fmt` | ruff autofix and format |
| `make db-reset` | recreate the local database and reapply all migrations |
| `make corpus` | regenerate the synthetic corpus (ground truth JSON and PDFs) |
| `make eval-oracle` | check the eval harness against ground truth; nothing is saved |
| `make eval-smoke` | real LLM on three documents to check the setup; nothing is saved |
| `make eval` | real LLM on the whole corpus; results go to `eval/results/` |
| `npx supabase migration new <name>` | create a new SQL migration in `supabase/migrations` |

## Repository layout

```text
api/                  FastAPI, worker, domain logic, tests
web/                  Next.js review UI
n8n/                  exported workflows
supabase/migrations/  SQL migrations
corpus/               generator, templates, ground truth
eval/                 run_eval.py, results/
docs/                 SPEC.md, architecture, screenshots
```

## License

MIT
