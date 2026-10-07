"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Alert, Button, FieldStatusDot, Spinner, StatusBadge, inputClass } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import {
  type DateOrder,
  type FormField,
  type ReviewForm,
  applyDateOrder,
  assumedDateOrder,
  buildApproveRequest,
  checksFor,
  describeEvent,
  documentChecks,
  existingVendorId,
  formFromInvoice,
  formatDate,
  formatDateTime,
  initialForm,
  printedValue,
  shownStatus,
  swapDayMonth,
  validateForm,
} from "@/lib/review";
import type { CheckResult, DocumentDetail, Vendor } from "@/lib/types";

const NEW_VENDOR = "__new__";

type FieldConfig = { field: Exclude<FormField, "line_items">; label: string; type?: string; mono?: boolean };

const HEADER_FIELDS: FieldConfig[] = [
  { field: "invoice_number", label: "Invoice number", mono: true },
  { field: "invoice_date", label: "Invoice date", type: "date" },
  { field: "due_date", label: "Due date", type: "date" },
  { field: "currency", label: "Currency", mono: true },
];

const AMOUNT_FIELDS: FieldConfig[] = [
  { field: "subtotal", label: "Subtotal" },
  { field: "discount", label: "Discount" },
  { field: "shipping", label: "Shipping" },
  { field: "tax_total", label: "Tax" },
  { field: "total", label: "Total" },
];

function CheckMessages({ checks }: { checks: CheckResult[] }) {
  if (checks.length === 0) return null;
  return (
    <ul className="mt-1 space-y-0.5">
      {checks.map((check, index) => (
        <li
          key={`${check.check_id}-${index}`}
          className={`text-xs ${check.severity === "hard" ? "text-red-600 dark:text-red-400" : "text-amber-700 dark:text-amber-400"}`}
        >
          <span className="font-mono font-semibold">{check.check_id}</span> {check.message}
        </li>
      ))}
    </ul>
  );
}

function FieldRow({
  config,
  form,
  setForm,
  checks,
  printed,
  error,
  disabled,
}: {
  config: FieldConfig;
  form: ReviewForm;
  setForm: (form: ReviewForm) => void;
  checks: CheckResult[];
  printed: string | null;
  error?: string;
  disabled: boolean;
}) {
  const { field, label, type = "text", mono } = config;
  const status = shownStatus(checks, field, form[field].trim() !== "");
  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <label htmlFor={field} className="text-xs font-medium text-zinc-600 dark:text-zinc-300">
          {label}
        </label>
        {status && <FieldStatusDot status={status} />}
      </div>
      <input
        id={field}
        type={type}
        disabled={disabled}
        className={`${inputClass} ${mono ? "font-mono" : ""} ${error ? "border-red-400" : ""}`}
        value={form[field]}
        onChange={(e) => setForm({ ...form, [field]: e.target.value })}
      />
      {printed && (
        <p className="mt-1 truncate text-xs text-zinc-500 dark:text-zinc-400" title={printed}>
          Printed: <span className="font-mono">{printed}</span>
        </p>
      )}
      {error && <p className="mt-1 text-xs text-red-600 dark:text-red-400">{error}</p>}
      <CheckMessages checks={checksFor(checks, field)} />
    </div>
  );
}

function VendorPicker({
  detail,
  vendors,
  form,
  setForm,
  disabled,
}: {
  detail: DocumentDetail;
  vendors: Vendor[];
  form: ReviewForm;
  setForm: (form: ReviewForm) => void;
  disabled: boolean;
}) {
  const match = detail.extraction?.normalized?.vendor;
  const suggested = new Set<string>();
  if (match?.vendor_id) suggested.add(match.vendor_id);
  match?.candidates.forEach((c) => suggested.add(c.vendor_id));
  const others = vendors.filter((v) => !suggested.has(v.id));
  return (
    <div>
      <label htmlFor="vendor_id" className="mb-1 block text-xs font-medium text-zinc-600 dark:text-zinc-300">
        Vendor record
      </label>
      <select
        id="vendor_id"
        disabled={disabled}
        className={inputClass}
        value={form.vendor_id ?? NEW_VENDOR}
        onChange={(e) =>
          setForm({ ...form, vendor_id: e.target.value === NEW_VENDOR ? null : e.target.value })
        }
      >
        {match?.vendor_id && (
          <option value={match.vendor_id}>
            {match.canonical_name} (matched by {match.method.replace("_", " ")})
          </option>
        )}
        {match?.candidates.map((c) => (
          <option key={c.vendor_id} value={c.vendor_id}>
            {c.canonical_name} (similar name, score {c.score})
          </option>
        ))}
        {others.length > 0 && (
          <optgroup label="All vendors">
            {others.map((v) => (
              <option key={v.id} value={v.id}>
                {v.canonical_name}
              </option>
            ))}
          </optgroup>
        )}
        <option value={NEW_VENDOR}>New vendor: {form.vendor_name || "name above"}</option>
      </select>
      <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
        {form.vendor_id
          ? "A new spelling of the name is saved as an alias of this vendor."
          : "The vendor is created when the invoice is approved."}
      </p>
    </div>
  );
}

function DateOrderPicker({
  checks,
  original,
  form,
  setForm,
}: {
  checks: CheckResult[];
  original: ReviewForm;
  form: ReviewForm;
  setForm: (form: ReviewForm) => void;
}) {
  const assumed = assumedDateOrder(checks);
  if (!assumed) return null;
  const other: DateOrder = assumed === "DMY" ? "MDY" : "DMY";
  const swapped = swapDayMonth(original.invoice_date);
  const options: { order: DateOrder; date: string | null }[] = [
    { order: assumed, date: original.invoice_date },
    { order: other, date: swapped },
  ];
  const chosen = form.date_format ?? assumed;
  return (
    <fieldset className="rounded-md border border-amber-200 bg-amber-50 p-3 dark:border-amber-900 dark:bg-amber-950/40">
      <legend className="px-1 text-xs font-medium text-amber-900 dark:text-amber-200">
        Ambiguous date: which did the vendor mean?
      </legend>
      <div className="space-y-1.5">
        {options.map(({ order, date }) => (
          <label key={order} className="flex items-center gap-2 text-sm">
            <input
              type="radio"
              name="date_order"
              disabled={!date}
              checked={chosen === order}
              onChange={() => setForm(applyDateOrder(original, form, checks, order))}
            />
            {date ? formatDate(date) : "not a valid date"}
            <span className="text-xs text-zinc-500 dark:text-zinc-400">
              ({order === "DMY" ? "day first" : "month first"})
            </span>
          </label>
        ))}
      </div>
      <p className="mt-2 text-xs text-amber-900/80 dark:text-amber-200/80">
        The choice is remembered for this vendor, so its next invoices are not flagged.
      </p>
    </fieldset>
  );
}

function LineItemsEditor({
  form,
  setForm,
  checks,
  errors,
  disabled,
}: {
  form: ReviewForm;
  setForm: (form: ReviewForm) => void;
  checks: CheckResult[];
  errors: Record<string, string>;
  disabled: boolean;
}) {
  const status = shownStatus(checks, "line_items", form.line_items.length > 0);
  const update = (index: number, key: keyof ReviewForm["line_items"][number], value: string) =>
    setForm({
      ...form,
      line_items: form.line_items.map((item, i) => (i === index ? { ...item, [key]: value } : item)),
    });
  return (
    <section>
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-semibold">Line items</h3>
        {status && <FieldStatusDot status={status} />}
      </div>
      <CheckMessages checks={checksFor(checks, "line_items")} />
      <div className="mt-2 overflow-x-auto rounded-md border border-zinc-200 dark:border-zinc-800">
        <table className="w-full text-sm">
          <thead className="bg-zinc-50 text-xs text-zinc-500 dark:bg-zinc-900 dark:text-zinc-400">
            <tr>
              <th className="px-2 py-1.5 text-left font-medium">Description</th>
              <th className="w-20 px-2 py-1.5 text-right font-medium">Qty</th>
              <th className="w-28 px-2 py-1.5 text-right font-medium">Unit price</th>
              <th className="w-28 px-2 py-1.5 text-right font-medium">Amount</th>
              {!disabled && <th className="w-8" aria-label="Remove" />}
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
            {form.line_items.map((item, index) => (
              <tr key={index}>
                <td className="p-1">
                  <input
                    aria-label={`Line ${index + 1} description`}
                    disabled={disabled}
                    className={inputClass}
                    value={item.description}
                    onChange={(e) => update(index, "description", e.target.value)}
                  />
                </td>
                {(["quantity", "unit_price", "amount"] as const).map((key) => (
                  <td key={key} className="p-1">
                    <input
                      aria-label={`Line ${index + 1} ${key.replace("_", " ")}`}
                      disabled={disabled}
                      className={`${inputClass} text-right tabular-nums ${errors[`line_items.${index}.${key}`] ? "border-red-400" : ""}`}
                      value={item[key]}
                      onChange={(e) => update(index, key, e.target.value)}
                    />
                  </td>
                ))}
                {!disabled && (
                  <td className="p-1 text-center">
                    <button
                      type="button"
                      aria-label={`Remove line ${index + 1}`}
                      className="rounded px-1.5 text-zinc-400 hover:bg-zinc-100 hover:text-red-600 dark:hover:bg-zinc-800"
                      onClick={() =>
                        setForm({ ...form, line_items: form.line_items.filter((_, i) => i !== index) })
                      }
                    >
                      ×
                    </button>
                  </td>
                )}
              </tr>
            ))}
            {form.line_items.length === 0 && (
              <tr>
                <td colSpan={5} className="px-2 py-3 text-center text-xs text-zinc-500">
                  No line items.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {!disabled && (
        <Button
          type="button"
          variant="ghost"
          className="mt-1"
          onClick={() =>
            setForm({
              ...form,
              line_items: [...form.line_items, { description: "", quantity: "", unit_price: "", amount: "" }],
            })
          }
        >
          + Add line
        </Button>
      )}
    </section>
  );
}

function Banners({ detail }: { detail: DocumentDetail }) {
  const { document, extraction, checks } = detail;
  const normalized = extraction?.normalized;
  const banners = [];
  if (normalized?.duplicate) {
    banners.push(
      <Alert key="h5" tone="error" title="Duplicate invoice">
        The same vendor and invoice number already exist ({normalized.duplicate.status.replaceAll("_", " ")}).{" "}
        <Link className="underline" href={`/documents/${normalized.duplicate.document_id}`}>
          Open the other document
        </Link>
      </Alert>,
    );
  }
  if (normalized?.possible_duplicate) {
    banners.push(
      <Alert key="w8" tone="warning" title="Possible duplicate">
        Same number, total and date as another invoice.{" "}
        <Link className="underline" href={`/documents/${normalized.possible_duplicate.document_id}`}>
          Compare
        </Link>
      </Alert>,
    );
  }
  if (document.duplicate_of_document_id) {
    banners.push(
      <Alert key="file" tone="info" title="Same file as an earlier e-mail">
        This attachment was skipped without reading it again.{" "}
        <Link className="underline" href={`/documents/${document.duplicate_of_document_id}`}>
          Open the original
        </Link>
      </Alert>,
    );
  }
  if (document.status === "failed") {
    banners.push(
      <Alert key="failed" tone="error" title={`Processing failed: ${document.failure_reason?.replaceAll("_", " ")}`}>
        The original is kept. Reprocess it, or enter the invoice manually below.
        {document.last_error && <p className="mt-1 font-mono text-xs opacity-80">{document.last_error}</p>}
      </Alert>,
    );
  }
  if (document.status === "skipped" && !document.duplicate_of_document_id) {
    banners.push(
      <Alert key="skipped" tone="info" title={`Skipped: ${document.skip_reason?.replaceAll("_", " ")}`} />,
    );
  }
  for (const check of documentChecks(checks)) {
    banners.push(
      <Alert key={`${check.check_id}-${check.message}`} tone={check.severity === "hard" ? "error" : "warning"}>
        <span className="font-mono font-semibold">{check.check_id}</span> {check.message}
      </Alert>,
    );
  }
  return banners.length ? <div className="space-y-2">{banners}</div> : null;
}

function ReviewPanel({
  detail,
  vendors,
  onChanged,
}: {
  detail: DocumentDetail;
  vendors: Vendor[];
  onChanged: (message: string) => void;
}) {
  const status = detail.document.status;
  const editable = status === "needs_review" || status === "failed";
  const original = detail.invoice
    ? formFromInvoice(detail.invoice)
    : initialForm(detail.extraction?.normalized);
  const [form, setForm] = useState<ReviewForm>(original);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [blocking, setBlocking] = useState<CheckResult[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const checks = detail.checks;

  async function run(action: () => Promise<unknown>, done: string) {
    setBusy(true);
    setProblem(null);
    try {
      await action();
      onChanged(done);
    } catch (e) {
      if (e instanceof ApiError && e.status === 422 && typeof e.detail === "object" && e.detail) {
        const detailBody = e.detail as { checks?: CheckResult[]; message?: string };
        if (detailBody.checks) setBlocking(detailBody.checks);
        else setProblem(detailBody.message ?? "The server rejected the values.");
      } else if (e instanceof ApiError && e.status === 409 && existingVendorId(e.detail, vendors)) {
        setForm({ ...form, vendor_id: existingVendorId(e.detail, vendors) });
        setProblem("A vendor with this name already exists. It is now selected above: check it and approve again.");
      } else if (e instanceof ApiError && typeof e.detail === "object" && e.detail) {
        setProblem((e.detail as { message?: string }).message ?? e.message);
      } else {
        setProblem(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setBusy(false);
    }
  }

  function approve() {
    const found = validateForm(form);
    setErrors(found);
    if (Object.keys(found).length) return;
    void run(() => api.approve(detail.document.id, buildApproveRequest(form)), "Approved");
  }

  const fieldProps = (config: FieldConfig) => ({
    config,
    form,
    setForm,
    checks,
    printed: printedValue(detail.raw_values, config.field),
    error: errors[config.field],
    disabled: !editable,
  });

  if (!editable && !detail.extraction && !detail.invoice) return <Banners detail={detail} />;

  return (
    <div className="space-y-5">
      <Banners detail={detail} />

      <section className="space-y-3">
        <h3 className="text-sm font-semibold">Vendor</h3>
        <div className="grid gap-3 sm:grid-cols-2">
          <FieldRow {...fieldProps({ field: "vendor_name", label: "Vendor name" })} />
          <FieldRow {...fieldProps({ field: "vendor_tax_id", label: "Tax ID", mono: true })} />
        </div>
        {editable && (
          <VendorPicker detail={detail} vendors={vendors} form={form} setForm={setForm} disabled={!editable} />
        )}
      </section>

      <section className="space-y-3">
        <h3 className="text-sm font-semibold">Invoice</h3>
        <div className="grid gap-3 sm:grid-cols-2">
          {HEADER_FIELDS.map((config) => (
            <FieldRow key={config.field} {...fieldProps(config)} />
          ))}
        </div>
        {editable && <DateOrderPicker checks={checks} original={original} form={form} setForm={setForm} />}
      </section>

      <section className="space-y-3">
        <h3 className="text-sm font-semibold">Amounts</h3>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {AMOUNT_FIELDS.map((config) => (
            <FieldRow key={config.field} {...fieldProps(config)} />
          ))}
        </div>
        {form.tax_inclusive && (
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            Prices include tax: the total is checked without adding the tax again.
          </p>
        )}
      </section>

      <LineItemsEditor form={form} setForm={setForm} checks={checks} errors={errors} disabled={!editable} />

      {detail.invoice && (
        <Alert tone="info" title={`Approved (${detail.invoice.approval_mode.replaceAll("_", " ")})`}>
          {detail.invoice.approved_by ? `By ${detail.invoice.approved_by}. ` : ""}
          {detail.invoice.exported_at ? "Exported to Google Sheets." : "Not exported yet."}
        </Alert>
      )}

      {editable && (
        <section className="space-y-3 border-t border-zinc-200 pt-4 dark:border-zinc-800">
          {Object.keys(errors).length > 0 && (
            <Alert tone="error" title="Fix the highlighted fields first" />
          )}
          {problem && <Alert tone="error" title={problem} />}
          {blocking && (
            <div className="space-y-2 rounded-md border border-red-200 p-3 dark:border-red-900">
              <p className="text-sm font-medium">These checks still fail on the values above:</p>
              <CheckMessages checks={blocking} />
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={form.override}
                  onChange={(e) => setForm({ ...form, override: e.target.checked })}
                />
                Approve anyway (override)
              </label>
              {form.override && (
                <div>
                  <textarea
                    aria-label="Override comment"
                    placeholder="Why is it correct anyway? This comment is recorded."
                    className={`${inputClass} min-h-16`}
                    value={form.comment}
                    onChange={(e) => setForm({ ...form, comment: e.target.value })}
                  />
                  {errors.comment && <p className="text-xs text-red-600">{errors.comment}</p>}
                </div>
              )}
            </div>
          )}

          {rejecting ? (
            <div className="space-y-2">
              <textarea
                aria-label="Reject reason"
                placeholder="Reason for rejecting (required)"
                className={`${inputClass} min-h-16`}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
              <div className="flex gap-2">
                <Button
                  variant="danger"
                  disabled={busy || !reason.trim()}
                  onClick={() => void run(() => api.reject(detail.document.id, reason.trim()), "Rejected")}
                >
                  Confirm reject
                </Button>
                <Button variant="ghost" onClick={() => setRejecting(false)}>
                  Cancel
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              <Button variant="primary" disabled={busy} onClick={approve}>
                {status === "failed" ? "Approve manual entry" : "Approve"}
              </Button>
              {status === "needs_review" && (
                <Button variant="danger" disabled={busy} onClick={() => setRejecting(true)}>
                  Reject
                </Button>
              )}
              {status === "failed" && (
                <Button
                  disabled={busy}
                  onClick={() => void run(() => api.reprocess(detail.document.id), "Sent for reprocessing")}
                >
                  Reprocess
                </Button>
              )}
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function Timeline({ detail }: { detail: DocumentDetail }) {
  const { extraction } = detail;
  return (
    <section className="space-y-3">
      <h3 className="text-sm font-semibold">Timeline</h3>
      <ol className="space-y-2 border-l border-zinc-200 pl-4 dark:border-zinc-800">
        {detail.events.map((event, index) => (
          <li key={index} className="relative text-sm">
            <span className="absolute -left-[21px] top-1.5 h-2 w-2 rounded-full bg-zinc-300 dark:bg-zinc-600" />
            <span>{describeEvent(event)}</span>
            <span className="ml-2 text-xs text-zinc-500 dark:text-zinc-400">
              {formatDateTime(event.created_at)} · {event.actor}
            </span>
          </li>
        ))}
      </ol>
      {extraction && (
        <p className="text-xs text-zinc-500 dark:text-zinc-400">
          Read by {extraction.provider} {extraction.model} (prompt {extraction.prompt_version})
          {extraction.latency_ms !== null && ` in ${(extraction.latency_ms / 1000).toFixed(1)} s`}
          {extraction.input_tokens !== null &&
            `, ${extraction.input_tokens + (extraction.output_tokens ?? 0)} tokens`}
          .
        </p>
      )}
    </section>
  );
}

/** Document page: the original on the left, extracted values and actions on the right. */
export function DocumentView({ id }: { id: string }) {
  const [detail, setDetail] = useState<DocumentDetail | null>(null);
  const [vendors, setVendors] = useState<Vendor[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(() => {
    Promise.all([api.document(id), api.vendors()])
      .then(([doc, vendorList]) => {
        setDetail(doc);
        setVendors(vendorList);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, [id]);

  useEffect(load, [load]);

  if (error) return <Alert tone="error" title="Could not load the document">{error}</Alert>;
  if (!detail) return <Spinner />;
  const { document } = detail;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link href="/" className="text-sm text-indigo-600 hover:underline dark:text-indigo-400">
            ← Documents
          </Link>
          <h1 className="mt-1 flex items-center gap-3 text-xl font-semibold">
            {document.filename}
            <StatusBadge status={document.status} />
          </h1>
          <p className="text-sm text-zinc-500 dark:text-zinc-400">
            {document.sender ?? "unknown sender"} · {document.subject ?? "no subject"} · received{" "}
            {formatDateTime(document.received_at)}
            {document.extraction_path && ` · read from ${document.extraction_path === "text" ? "text layer" : "page images"}`}
          </p>
        </div>
      </div>
      {notice && <Alert tone="info" title={notice} />}

      <div className="grid gap-6 lg:grid-cols-2">
        <div className="lg:sticky lg:top-16 lg:h-[calc(100vh-6rem)]">
          {detail.pdf_url ? (
            <>
              <iframe
                title="Original document"
                src={detail.pdf_url}
                className="h-[70vh] w-full rounded-lg border border-zinc-200 bg-white lg:h-[calc(100%-1.75rem)] dark:border-zinc-800"
              />
              <a
                href={detail.pdf_url}
                target="_blank"
                rel="noreferrer"
                className="mt-1.5 inline-block text-xs text-indigo-600 hover:underline dark:text-indigo-400"
              >
                Open the original in a new tab
              </a>
            </>
          ) : (
            <div className="flex h-64 items-center justify-center rounded-lg border border-dashed border-zinc-300 text-sm text-zinc-500 dark:border-zinc-700">
              Preview unavailable
            </div>
          )}
        </div>
        <div className="space-y-8">
          <ReviewPanel
            key={`${document.status}-${detail.events.length}`}
            detail={detail}
            vendors={vendors}
            onChanged={(message) => {
              setNotice(message);
              load();
            }}
          />
          <Timeline detail={detail} />
        </div>
      </div>
    </div>
  );
}
