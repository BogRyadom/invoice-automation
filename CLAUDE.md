# Invoice Automation

Portfolio project: invoice processing from Gmail with verifiable extraction, deterministic validation and human review. Stack: n8n, FastAPI, Postgres (Supabase), Next.js.

## Source of truth

`docs/SPEC.md` (written in Russian) is the specification. Read it fully before any work.

- Work stage by stage (SPEC section 18). Do not start the next stage without an explicit instruction.
- If something in the SPEC is ambiguous or looks wrong, ask. Do not guess and do not deviate silently.
- If a deviation is needed, propose it first. After approval, update `docs/SPEC.md` in the same change.

## Communication

- Talk to the user in Russian.
- Everything in the repository is in English: code, comments, commit messages, README, docs other than SPEC.md.
- No em dashes in written text. Use commas, parentheses or shorter sentences.

## Workflow for each stage

1. Ask open questions first.
2. Show a short plan: files, dependencies, anything that touches external services. Wait for confirmation.
3. Implement with tests in the same stage.
4. Run lint and tests, show the output.
5. Report: what was done, how to verify manually, deviations from the SPEC.

Ask for confirmation and show exactly what will happen before any irreversible or external action: deleting or overwriting files, `git push`, force operations, running migrations against a hosted database, creating cloud resources, sending emails or Slack messages.

## Code style

- Python: snake_case, type hints, `Decimal` for money (never float).
- Docstrings on functions only, short. No comments for obvious code.
- English comments only.
- One consistent style across all files. Follow existing patterns before introducing new ones.
- TypeScript: strict mode, no `any` without a stated reason.

## Hard rules from the SPEC

- No LLM self-reported confidence anywhere. Routing is based on check results only.
- The LLM returns raw values as printed in the document. Numbers, dates and currency are normalized by code.
- n8n holds no business state and makes no decisions about invoice data.
- Original documents are never deleted.
- Every retry is bounded.
- Do not use PyMuPDF (AGPL). Use `pdfplumber` and `pypdfium2`.
- Do not hardcode LLM model ids from memory. Read them from env and check current provider docs.
- Synthetic data only. No real personal or financial data in the repo, fixtures or logs.
- No metrics in the README that are not present in `eval/results/`.
- Real LLM calls happen only in eval, never in CI or unit tests.
- Secrets live in env only. Keep `.env.example` complete and free of real values.

## Commands

Run from the repo root. Requires Docker Desktop (running), GNU make, uv, Node.js 24.

- Install: `make install`
- Run locally: `make up` (local Supabase stack, then api, worker, web via docker compose). Stop: `make down`
- Local DB: `make db-start`, `make db-stop`, `make db-reset` (recreates the LOCAL database and reapplies migrations)
- Lint: `make lint` (ruff check, ruff format --check, eslint, tsc)
- Format: `make fmt`
- Test: `make test`. DB tests need `TEST_DATABASE_URL` and the local Supabase Postgres; locally they skip without it, in CI they fail
- New migration: `npx supabase migration new <name>`
- Eval: `make eval`, added in Stage 1
