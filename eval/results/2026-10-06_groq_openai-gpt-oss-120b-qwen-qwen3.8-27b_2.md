# Eval results 2026-10-06

Provider: groq. Model: openai/gpt-oss-120b+qwen/qwen3.8-27b.
Corpus: 35 synthetic documents (corpus/manifest.json).

| Metric | Value |
|---|---|
| Field accuracy: vendor_key | 100.0% (29/29) |
| Field accuracy: vendor_tax_id | 100.0% (29/29) |
| Field accuracy: invoice_number | 100.0% (29/29) |
| Field accuracy: invoice_date | 100.0% (29/29) |
| Field accuracy: due_date | 100.0% (29/29) |
| Field accuracy: currency | 100.0% (29/29) |
| Field accuracy: subtotal | 100.0% (29/29) |
| Field accuracy: discount | 100.0% (29/29) |
| Field accuracy: shipping | 100.0% (29/29) |
| Field accuracy: tax_total | 100.0% (29/29) |
| Field accuracy: total | 100.0% (29/29) |
| Line items: row count match | 100.0% (29/29) |
| Line items: amounts match | 100.0% (29/29) |
| Document type accuracy | 100.0% (32/32) |
| Review rate | 48.3% (14/29) |
| Auto-approve precision (key fields) | 100.0% (15/15) |
| Documents with a field error sent to review | n/a (0/0) |
| Latency per document, ms | mean 2189, median 1895 (n=32) |
| Input tokens per document | mean 1166, median 974 (n=32) |
| Output tokens per document | mean 566, median 525 (n=32) |
