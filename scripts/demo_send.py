# Sends the synthetic corpus to the ingest API exactly as the n8n Gmail workflow would, so the
# whole pipeline after Gmail can be exercised without any e-mail account.

import argparse
import os
import sys
import uuid
from collections.abc import Sequence

import httpx

from app.config import get_settings
from corpus.storage import document_path, load_corpus

DEFAULT_API_URL = "http://localhost:8000"


def main(argv: Sequence[str] | None = None) -> int:
    """Upload corpus documents to POST /api/documents with their synthetic e-mail metadata."""
    parser = argparse.ArgumentParser(description="Send the synthetic corpus to the ingest API.")
    parser.add_argument("--api-url", default=os.environ.get("NEXT_PUBLIC_API_URL", DEFAULT_API_URL))
    parser.add_argument("--docs", nargs="*", help="document ids to send (default: all)")
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="use new message ids, so the documents count as new e-mails",
    )
    args = parser.parse_args(argv)

    secret = get_settings().ingest_shared_secret.get_secret_value()
    if not secret:
        print("INGEST_SHARED_SECRET is not set in .env", file=sys.stderr)
        return 1
    run = uuid.uuid4().hex[:8] if args.fresh else "demo"
    docs = [doc for doc in load_corpus() if not args.docs or doc.doc_id in args.docs]

    failures = 0
    with httpx.Client(base_url=args.api_url, timeout=60) as client:
        for doc in docs:
            response = client.post(
                "/api/documents",
                headers={"X-Ingest-Secret": secret},
                files={"file": (doc.filename, document_path(doc).read_bytes(), "application/pdf")},
                data={
                    "gmail_message_id": f"{run}-{doc.doc_id}",
                    "attachment_id": "1",
                    "sender": doc.email.sender,
                    "subject": doc.email.subject,
                    "received_at": doc.email.received_at.isoformat(),
                },
            )
            outcome = response.json() if response.is_success else response.text
            print(f"{doc.doc_id:16} HTTP {response.status_code} {outcome}")
            failures += not response.is_success
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
