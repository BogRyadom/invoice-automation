import argparse
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from corpus.storage import load_corpus
from eval.metrics import EvalReport, evaluate
from eval.predictors import OraclePredictor, Predictor
from eval.report import RESULTS_DIR, render_markdown, write_results

NOT_IMPLEMENTED = (
    "The LLM extraction pipeline is not implemented yet (Stage 2). "
    "Run `make eval-oracle` to check the eval harness."
)


def build_predictor(name: str) -> Predictor:
    """Predictor selected on the command line."""
    if name == "oracle":
        return OraclePredictor()
    raise SystemExit(NOT_IMPLEMENTED)


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


def main(
    argv: Sequence[str] | None = None,
    *,
    today: date | None = None,
    results_dir: Path = RESULTS_DIR,
) -> int:
    """Run the eval on the synthetic corpus and save results for real providers."""
    parser = argparse.ArgumentParser(description="Evaluate extraction on the synthetic corpus.")
    parser.add_argument("--predictor", choices=["llm", "oracle"], default="llm")
    args = parser.parse_args(argv)

    predictor = build_predictor(args.predictor)
    docs = load_corpus()
    report = evaluate(docs, predictor.predict(docs))
    run_date = today or date.today()
    print(
        render_markdown(
            report, provider=predictor.provider, model=predictor.model, run_date=run_date
        )
    )

    if predictor.provider == "oracle":
        if failures := oracle_failures(report):
            print(f"Harness check failed: {', '.join(failures)}", file=sys.stderr)
            return 1
        print("Harness check passed. Oracle results are not saved.")
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
