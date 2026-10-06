# Puts the synthetic corpus into a demo Gmail inbox through the Gmail API (messages.insert), as
# if the fictional vendors had e-mailed their invoices. Nothing is sent to anyone. Needs an
# OAuth client of type "Desktop app" saved as .secrets/google_oauth_client.json
# (see docs/integrations.md).

import argparse
import base64
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from typing import Any

from app.config import REPO_ROOT
from corpus.models import GroundTruth
from corpus.storage import document_path, load_corpus

SCOPES = [
    "https://www.googleapis.com/auth/gmail.insert",
    "https://www.googleapis.com/auth/gmail.labels",
]
SECRETS_DIR = REPO_ROOT / ".secrets"
CLIENT_FILE = SECRETS_DIR / "google_oauth_client.json"
TOKEN_FILE = SECRETS_DIR / "gmail_token.json"
INBOX_LABEL = "Invoices/Inbox"
# The fictional company the corpus invoices are billed to.
DEFAULT_RECIPIENT = "accounts-payable@lumen-harbor.example"
WORKFLOW_LABELS = ("Invoices/Received", "Invoices/No-attachment", "Invoices/Ingest-failed")


def invoice_message(doc: GroundTruth, to: str, sent_at: datetime) -> EmailMessage:
    """E-mail from the document's fictional sender with the PDF attached."""
    message = EmailMessage()
    message["From"] = doc.email.sender
    message["To"] = to
    message["Subject"] = doc.email.subject
    message["Date"] = format_datetime(sent_at)
    message["Message-ID"] = make_msgid(domain="synthetic.example")
    message.set_content(
        "Hello,\n\nplease find the attached document.\n\n"
        "This is synthetic test data for the invoice-automation demo.\n"
    )
    message.add_attachment(
        document_path(doc).read_bytes(),
        maintype="application",
        subtype="pdf",
        filename=doc.filename,
    )
    return message


def message_without_attachment(to: str, sent_at: datetime) -> EmailMessage:
    """E-mail that lands in the invoice label but carries no PDF."""
    message = EmailMessage()
    message["From"] = "accounts@velmora-logistics.example"
    message["To"] = to
    message["Subject"] = "Question about next week's delivery"
    message["Date"] = format_datetime(sent_at)
    message["Message-ID"] = make_msgid(domain="synthetic.example")
    message.set_content("Hello,\n\ncould you confirm the delivery address for next week?\n")
    return message


def raw(message: EmailMessage) -> str:
    """Gmail API encoding of a MIME message."""
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


def gmail_service() -> Any:
    """Authorised Gmail API client; opens a browser the first time."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    credentials = None
    if TOKEN_FILE.exists():
        credentials = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    if not credentials or not credentials.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES)
        credentials = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(credentials.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=credentials)


def ensure_labels(service: Any, names: Sequence[str]) -> dict[str, str]:
    """Label ids by name, creating missing labels."""
    existing = service.users().labels().list(userId="me").execute().get("labels", [])
    ids = {label["name"]: label["id"] for label in existing}
    for name in names:
        if name not in ids:
            created = (
                service.users()
                .labels()
                .create(
                    userId="me",
                    body={
                        "name": name,
                        "labelListVisibility": "labelShow",
                        "messageListVisibility": "show",
                    },
                )
                .execute()
            )
            ids[name] = created["id"]
    return ids


def labels_named(labels: dict[str, str]) -> list[str]:
    """Names of the labels this project uses."""
    return [name for name in (INBOX_LABEL, *WORKFLOW_LABELS) if name in labels]


def main(argv: Sequence[str] | None = None) -> int:
    """Insert the corpus e-mails into the demo inbox under the Invoices/Inbox label."""
    parser = argparse.ArgumentParser(
        description="Fill a demo Gmail inbox with the synthetic corpus."
    )
    parser.add_argument(
        "--to",
        default=DEFAULT_RECIPIENT,
        help="To header of the inserted e-mails (the inbox is the authorised account)",
    )
    parser.add_argument("--docs", nargs="*", help="document ids to insert (default: all)")
    parser.add_argument(
        "--labels-only",
        action="store_true",
        help="only create the Invoices/* labels, insert no e-mails",
    )
    parser.add_argument(
        "--no-attachment",
        action="store_true",
        help="also insert one e-mail without a PDF",
    )
    args = parser.parse_args(argv)

    if not CLIENT_FILE.exists():
        print(f"Missing {CLIENT_FILE}; see docs/integrations.md", file=sys.stderr)
        return 1
    service = gmail_service()
    labels = ensure_labels(service, [INBOX_LABEL, *WORKFLOW_LABELS])
    if args.labels_only:
        print(f"Labels ready: {', '.join(labels_named(labels))}")
        return 0
    label_ids = ["INBOX", "UNREAD", labels[INBOX_LABEL]]

    now = datetime.now(UTC)
    docs = [doc for doc in load_corpus() if not args.docs or doc.doc_id in args.docs]
    # One second apart, so documents keep corpus order when the worker sorts by arrival.
    messages = [
        invoice_message(doc, args.to, now + timedelta(seconds=index))
        for index, doc in enumerate(docs)
    ]
    if args.no_attachment:
        messages.append(message_without_attachment(args.to, now + timedelta(seconds=len(docs))))
    for message in messages:
        service.users().messages().insert(
            userId="me",
            body={"raw": raw(message), "labelIds": label_ids},
            internalDateSource="receivedTime",
        ).execute()
        print(f"inserted: {message['Subject']}")
    print(f"{len(messages)} messages are in {INBOX_LABEL}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
