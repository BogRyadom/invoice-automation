# Connecting Gmail, Google Sheets and Slack

Everything runs locally and works without these accounts: `make demo-send` feeds the synthetic
corpus straight into the API. This guide is for the full demo path
Gmail → n8n → API → review → Google Sheets and Slack.

Use a separate demo Google account and put only synthetic data into it. Secrets go into `.env`
or `.secrets/` (both are git-ignored), never into chat, issues or commits.

## 1. Demo Gmail account

Create a new Google account, for example `invoice-automation-demo@gmail.com`. You only need to
receive mail: the corpus is placed into the inbox by a script, nobody has to send anything.

## 2. Google Cloud project and OAuth clients

Sign in to <https://console.cloud.google.com> with the demo account. No free trial and no billing
account are needed: skip any "Try for free" or "Start free" offer and never add a card. The
Gmail, Sheets and Drive APIs and OAuth clients work without billing.

1. Create a project, for example `invoice-automation-demo`.
2. **APIs and services → Library**: enable **Gmail API**, **Google Sheets API** and
   **Google Drive API**.
3. **Google Auth Platform → Branding / Audience**: user type **External**, app name and support
   e-mail of your choice. Under **Audience → Test users** add the demo Gmail address. The app
   stays in testing mode; Google will show an "unverified app" warning, which is expected.
4. **Clients → Create client**, type **Web application**, name `n8n`. Authorised redirect URI:
   `http://localhost:5678/rest/oauth2-credential/callback`. Download the JSON right away (the
   secret may not be shown again) and save it as `.secrets/n8n_oauth_client.json`; n8n needs
   its client ID and secret in step 5.
5. **Clients → Create client**, type **Desktop app**, name `seed script`. Download the JSON and
   save it as `.secrets/google_oauth_client.json` in the repository.

## 3. Google Sheet

1. Create a spreadsheet in the demo account, rename the first tab to `Invoices`.
2. Put these headers in row 1, one per column:
   `invoice_id, document_id, vendor, vendor_tax_id, invoice_number, invoice_date, due_date,
   currency, subtotal, discount, shipping, tax_total, total, approval_mode, approved_by,
   approved_at, review_url`
3. Copy the spreadsheet id from its URL (`/spreadsheets/d/<id>/edit`) into `.env`:
   `GOOGLE_SHEET_ID=<id>`.

## 4. Slack

1. Create a free workspace at <https://slack.com/get-started> and a channel such as `#invoices`.
2. At <https://api.slack.com/apps> create an app **from scratch** in that workspace.
3. **Incoming Webhooks**: turn on, **Add New Webhook**, pick the channel, copy the URL into
   `.env`: `SLACK_WEBHOOK_URL=<url>`.

## 5. n8n

1. Apply the new `.env` values and load the workflows:
   ```sh
   docker compose up -d n8n
   make n8n-import
   ```
2. Open <http://localhost:5678> and create the local owner account (it stays on your machine).
3. **Credentials → Add**: **Gmail OAuth2 API** and **Google Sheets OAuth2 API**, both with the
   client ID and secret from step 2.4. Click **Sign in with Google** and choose the demo account.
4. Workflow **invoice_ingest**: pick the Gmail credential in every Gmail node; in the three
   label nodes choose `Invoices/Received`, `Invoices/No-attachment` and `Invoices/Ingest-failed`
   (the seed script in step 7 creates them, so run it once first or create the labels by hand).
   Then **Publish**.
5. Workflow **invoice_events**: pick the Google Sheets credential in the Sheets node, then
   **Publish**. Its webhook already checks `N8N_WEBHOOK_SECRET`.

## 6. Reviewer login

Open Supabase Studio at <http://127.0.0.1:54323>, **Authentication → Users → Add user**, enter an
e-mail and password and confirm the user. Sign-up from the UI is disabled.

## 7. Fill the demo inbox

```sh
# once: create the Invoices/* labels (opens a browser for Google consent)
uv run --project api python -m scripts.gmail_seed --labels-only
# once: register the fictional vendors, so their invoices can be auto-approved
make demo-vendors
# insert e-mails for chosen corpus documents, plus one without an attachment
uv run --project api python -m scripts.gmail_seed --docs clean_02 clean_09 european_01 --no-attachment
```

The consent screen shows an "unverified app" warning: choose the demo account, **Advanced**,
then continue. Without `--docs` the whole corpus is inserted (about 50 000 LLM tokens). Each
e-mail is inserted directly into the inbox under `Invoices/Inbox`; nothing is sent. Within a
minute n8n picks them up and hands the PDFs to the API. Auto-approval also needs
`AUTO_APPROVE_ENABLED=true` in `.env` (restart the worker after changing it).

A file the system has already seen is skipped as `duplicate_file`, so a document from the corpus
reaches the LLM only once per database.

## Where to look

| What | Where |
|---|---|
| E-mails picked up | Gmail: label `Invoices/Received` (or `Invoices/No-attachment`) |
| Notifications | Slack channel `#invoices` |
| Approved invoices | the `Invoices` sheet, one row per invoice |
| Each n8n run | <http://localhost:5678> → **Executions** |
| Documents, checks, events | Supabase Studio → Table Editor: `documents`, `check_results`, `document_events`, `outbox_events` |

## What goes where

| Value | Where |
|---|---|
| `GOOGLE_SHEET_ID`, `SLACK_WEBHOOK_URL` | `.env` |
| OAuth client for the seed script | `.secrets/google_oauth_client.json` |
| Gmail and Sheets OAuth credentials for n8n | n8n UI, stored encrypted in the n8n volume |
| Reviewer account | local Supabase Auth |
