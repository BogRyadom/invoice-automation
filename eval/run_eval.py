import argparse
import sys
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

from app.config import get_settings
from app.extraction.groq_provider import GroqProvider
from corpus.storage import load_corpus
from eval.metrics import EvalReport, Prediction, evaluate
from eval.predictors import EvalAborted, OraclePredictor, PipelinePredictor, Predictor
from eval.report import RESULTS_DIR, render_markdown, write_results

# One text invoice, one scan and one non-invoice: exercises every path in a few calls.
SMOKE_DOCS = ("clean_04", "scan_01", "not_invoice_03")
SMOKE_FAILURES = frozenset({"llm_unavailable", "invalid_extraction"})


def build_predictor(name: str) -> Predictor:
    """Predictor selected on the command line."""
    if name == "oracle":
        return OraclePredictor()
    settings = get_settings()
    try:
        provider = GroqProvider(settings)
    except ValueError as exc:
        raise SystemExit(f"Cannot start the LLM eval: {exc}. See .env.example.") from exc
    return PipelinePredictor(provider, settings, admin_url=settings.database_url)


def oracle_failures(report: EvalReport) -> list[str]:
    """Metrics that must be perfect when answers come straight from ground truth."""
    checks = {
        **{f"field {name}": ratio for name, ratio in report.field_accuracy.items()},
        "line item count": report.line_item_count_match,
        "line item amounts": report.line_item_amounts_match,
        "document type": report.document_type_accuracy,
        "auto-approve precision": report.auto_approve_precision,
    }
    failures = [
        name for name, ratio in checks.items() if ratio.total == 0 or ratio.hits != ratio.total
    ]
    if report.errors_sent_to_review.total:
        failures.append("field errors found")
    return failures


def document_lines(report: EvalReport) -> list[str]:
    """One line per document: outcome, raised checks, route verdict and wrong fields."""
    lines = []
    for doc in report.documents:
        flags = ",".join(doc.predicted_flags) or "-"
        verdict = "route ok" if doc.route_matches else f"route expected {doc.expected_status}"
        wrong = ", ".join(doc.wrong_fields) or "-"
        lines.append(
            f"  {doc.doc_id:16} {doc.outcome or '-':30} {flags:10} {verdict:32} wrong: {wrong}"
        )
    return lines


def token_summary(predictions: Sequence[Prediction]) -> str:
    """Total tokens spent by the run."""
    tokens_in = sum(p.input_tokens or 0 for p in predictions)
    tokens_out = sum(p.output_tokens or 0 for p in predictions)
    return f"Tokens used: {tokens_in} input, {tokens_out} output."


def main(
    argv: Sequence[str] | None = None,
    *,
    today: date | None = None,
    results_dir: Path = RESULTS_DIR,
    predictor_factory: Callable[[str], Predictor] = build_predictor,
) -> int:
    """Run the eval on the synthetic corpus and save results for real full runs."""
    parser = argparse.ArgumentParser(description="Evaluate extraction on the synthetic corpus.")
    parser.add_argument("--predictor", choices=["llm", "oracle"], default="llm")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help=f"run only {', '.join(SMOKE_DOCS)} to check the setup; nothing is saved",
    )
    args = parser.parse_args(argv)

    docs = load_corpus()
    if args.smoke:
        docs = [doc for doc in docs if doc.doc_id in SMOKE_DOCS]
    predictor = predictor_factory(args.predictor)
    try:
        predictions = predictor.predict(docs)
    except EvalAborted as exc:
        print(f"Eval stopped, nothing saved: {exc}", file=sys.stderr)
        return 2

    report = evaluate(docs, predictions)
    run_date = today or date.today()
    print(
        render_markdown(
            report, provider=predictor.provider, model=predictor.model, run_date=run_date
        )
    )
    print("Documents:")
    print("\n".join(document_lines(report)))

    if predictor.provider == "oracle":
        if failures := oracle_failures(report):
            print(f"Harness check failed: {', '.join(failures)}", file=sys.stderr)
            return 1
        print("Harness check passed. Oracle results are not saved.")
        return 0

    print(token_summary(predictions))
    if args.smoke:
        broken = [p.doc_id for p in predictions if p.status is None or p.reason in SMOKE_FAILURES]
        if broken:
            print(f"Smoke check failed for: {', '.join(broken)}", file=sys.stderr)
            return 1
        print("Smoke check passed. Smoke results are not saved.")
        return 0

    path = write_results(
        report,
        provider=predictor.provider,
        model=predictor.model,
        run_date=run_date,
        results_dir=results_dir,
    )
    print(f"Saved {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
