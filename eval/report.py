import json
import re
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from eval.metrics import EvalReport, Ratio, Stats

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def slug(value: str) -> str:
    """File-name-safe form of a provider or model id."""
    return re.sub(r"[^a-z0-9.]+", "-", value.lower()).strip("-")


def ratio_cell(ratio: Ratio) -> str:
    return f"{ratio.percent()} ({ratio.hits}/{ratio.total})"


def stats_cell(value: Stats) -> str:
    if value.count == 0:
        return "n/a"
    return f"mean {value.mean}, median {value.median} (n={value.count})"


def metric_rows(report: EvalReport) -> list[tuple[str, str]]:
    """Metric name and formatted value, in the order of docs/SPEC.md section 14."""
    rows = [(f"Field accuracy: {name}", ratio_cell(r)) for name, r in report.field_accuracy.items()]
    rows += [
        ("Line items: row count match", ratio_cell(report.line_item_count_match)),
        ("Line items: amounts match", ratio_cell(report.line_item_amounts_match)),
        ("Document type accuracy", ratio_cell(report.document_type_accuracy)),
        ("Review rate", ratio_cell(report.review_rate)),
        ("Auto-approve precision (key fields)", ratio_cell(report.auto_approve_precision)),
        ("Documents with a field error sent to review", ratio_cell(report.errors_sent_to_review)),
        ("Latency per document, ms", stats_cell(report.latency_ms)),
        ("Input tokens per document", stats_cell(report.input_tokens)),
        ("Output tokens per document", stats_cell(report.output_tokens)),
    ]
    return rows


def render_markdown(report: EvalReport, *, provider: str, model: str, run_date: date) -> str:
    """Results table as Markdown."""
    lines = [
        f"# Eval results {run_date.isoformat()}",
        "",
        f"Provider: {provider}. Model: {model}.",
        f"Corpus: {report.corpus_size} synthetic documents (corpus/manifest.json).",
        "",
        "| Metric | Value |",
        "|---|---|",
        *(f"| {name} | {value} |" for name, value in metric_rows(report)),
    ]
    return "\n".join(lines) + "\n"


def report_to_dict(
    report: EvalReport, *, provider: str, model: str, run_date: date
) -> dict[str, Any]:
    """Machine-readable results, including the per-document outcome."""
    return {
        "date": run_date.isoformat(),
        "provider": provider,
        "model": model,
        "corpus": {"size": report.corpus_size, "synthetic": True},
        **asdict(report),
    }


def write_results(
    report: EvalReport,
    *,
    provider: str,
    model: str,
    run_date: date,
    results_dir: Path = RESULTS_DIR,
) -> Path:
    """Save JSON and Markdown results; an existing run of the same day is never overwritten."""
    if provider == "oracle":
        raise ValueError("oracle results check the harness and are never saved")
    stem = f"{run_date.isoformat()}_{slug(provider)}_{slug(model)}"
    suffix, attempt = "", 1
    while (results_dir / f"{stem}{suffix}.json").exists():
        attempt += 1
        suffix = f"_{attempt}"
    json_path = results_dir / f"{stem}{suffix}.json"
    data = report_to_dict(report, provider=provider, model=model, run_date=run_date)
    json_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    json_path.with_suffix(".md").write_text(
        render_markdown(report, provider=provider, model=model, run_date=run_date),
        encoding="utf-8",
        newline="\n",
    )
    return json_path
