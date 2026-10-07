// Pure review-screen logic: field statuses from check results, the editable form and the
// approve request. Kept free of React so it can be unit tested.

import type {
  ApproveRequest,
  CheckResult,
  DocumentEvent,
  DocumentStatus,
  InvoiceRecord,
  Normalized,
  RawValues,
  Vendor,
} from "@/lib/types";

export const AMOUNT_FIELDS = ["subtotal", "discount", "shipping", "tax_total", "total"] as const;

export const FORM_FIELDS = [
  "vendor_name",
  "vendor_tax_id",
  "invoice_number",
  "invoice_date",
  "due_date",
  "currency",
  ...AMOUNT_FIELDS,
  "line_items",
] as const;

export type FormField = (typeof FORM_FIELDS)[number];
export type FieldStatus = "ok" | "warning" | "error";
export type DateOrder = "DMY" | "MDY";

// Check fields that refer to several form fields or use a different name.
const FIELD_ALIASES: Record<string, FormField[]> = {
  amounts: [...AMOUNT_FIELDS, "line_items"],
  tax_lines: ["tax_total"],
  tax_inclusive_note: ["tax_total"],
};

export const QUEUE_TABS: { key: string; label: string; statuses: DocumentStatus[] }[] = [
  { key: "review", label: "Needs review", statuses: ["needs_review"] },
  { key: "failed", label: "Failed", statuses: ["failed"] },
  { key: "skipped", label: "Skipped", statuses: ["skipped"] },
  { key: "approved", label: "Approved", statuses: ["approved", "auto_approved", "exported"] },
];

export type LineItemDraft = {
  description: string;
  quantity: string;
  unit_price: string;
  amount: string;
};

export type ReviewForm = {
  vendor_name: string;
  vendor_tax_id: string;
  invoice_number: string;
  invoice_date: string;
  due_date: string;
  currency: string;
  subtotal: string;
  discount: string;
  shipping: string;
  tax_total: string;
  total: string;
  tax_inclusive: boolean;
  line_items: LineItemDraft[];
  vendor_id: string | null;
  date_format: DateOrder | null;
  override: boolean;
  comment: string;
};

const DECIMAL = /^-?\d+(\.\d{1,4})?$/;
const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;
const CURRENCY = /^[A-Z]{3}$/;

function formFieldsFor(check: CheckResult): FormField[] {
  if (!check.field) return [];
  if (check.field in FIELD_ALIASES) return FIELD_ALIASES[check.field];
  return (FORM_FIELDS as readonly string[]).includes(check.field) ? [check.field as FormField] : [];
}

/** Failing checks for one form field. */
export function checksFor(checks: CheckResult[], field: FormField): CheckResult[] {
  return checks.filter((c) => c.status === "fail" && formFieldsFor(c).includes(field));
}

/** error if a hard check failed on the field, warning if a warning was raised, else ok. */
export function fieldStatus(checks: CheckResult[], field: FormField): FieldStatus {
  const failing = checksFor(checks, field);
  if (failing.some((c) => c.severity === "hard")) return "error";
  if (failing.length > 0) return "warning";
  return "ok";
}

/** Status shown next to a field: none when no checks ran, or when an empty field has nothing flagged. */
export function shownStatus(checks: CheckResult[], field: FormField, filled: boolean): FieldStatus | null {
  if (checks.length === 0) return null;
  const status = fieldStatus(checks, field);
  return status === "ok" && !filled ? null : status;
}

/** Failing checks that are not tied to a form field, shown above the form. */
export function documentChecks(checks: CheckResult[]): CheckResult[] {
  return checks.filter((c) => c.status === "fail" && formFieldsFor(c).length === 0);
}

/** Value as printed in the document, for showing next to a normalized field. */
export function printedValue(raw: RawValues | null, field: FormField): string | null {
  if (!raw) return null;
  if (field === "tax_total") {
    if (raw.tax_lines.length === 0) return null;
    return raw.tax_lines.map((line) => `${line.label}: ${line.amount_raw}`).join("; ");
  }
  if (field === "line_items") return null;
  return (raw[`${field}_raw` as keyof RawValues] as string | null) ?? null;
}

const blank = (value: string | null | undefined): string => value ?? "";

/** Editable form prefilled from the extraction; empty for manual entry. */
export function initialForm(normalized: Normalized | null | undefined): ReviewForm {
  const invoice = normalized?.invoice;
  return {
    vendor_name: blank(invoice?.vendor_name),
    vendor_tax_id: blank(invoice?.vendor_tax_id),
    invoice_number: blank(invoice?.invoice_number),
    invoice_date: blank(invoice?.invoice_date),
    due_date: blank(invoice?.due_date),
    currency: blank(invoice?.currency),
    subtotal: blank(invoice?.subtotal),
    discount: blank(invoice?.discount),
    shipping: blank(invoice?.shipping),
    tax_total: blank(invoice?.tax_total),
    total: blank(invoice?.total),
    tax_inclusive: invoice?.tax_inclusive ?? false,
    line_items: (invoice?.line_items ?? []).map((item) => ({
      description: blank(item.description),
      quantity: blank(item.quantity),
      unit_price: blank(item.unit_price),
      amount: blank(item.amount),
    })),
    vendor_id: normalized?.vendor?.vendor_id ?? null,
    date_format: null,
    override: false,
    comment: "",
  };
}

/** Read-only form showing an invoice that was already approved. */
export function formFromInvoice(invoice: InvoiceRecord): ReviewForm {
  return {
    ...initialForm(null),
    vendor_name: invoice.vendor,
    vendor_tax_id: blank(invoice.vendor_tax_id),
    invoice_number: invoice.invoice_number,
    invoice_date: invoice.invoice_date,
    due_date: blank(invoice.due_date),
    currency: invoice.currency,
    subtotal: blank(invoice.subtotal),
    discount: blank(invoice.discount),
    shipping: blank(invoice.shipping),
    tax_total: blank(invoice.tax_total),
    total: invoice.total,
    line_items: invoice.line_items.map((item) => ({
      description: blank(item.description),
      quantity: blank(item.quantity),
      unit_price: blank(item.unit_price),
      amount: blank(item.amount),
    })),
    vendor_id: invoice.vendor_id,
  };
}

function validDate(value: string): boolean {
  const match = ISO_DATE.exec(value);
  if (!match) return false;
  const [year, month, day] = match.slice(1).map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
}

/** Field errors that must be fixed before the form can be sent. */
export function validateForm(form: ReviewForm): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!form.vendor_name.trim()) errors.vendor_name = "Vendor is required";
  if (!form.invoice_number.trim()) errors.invoice_number = "Invoice number is required";
  if (!validDate(form.invoice_date)) errors.invoice_date = "Use a valid date (YYYY-MM-DD)";
  if (form.due_date && !validDate(form.due_date)) errors.due_date = "Use a valid date (YYYY-MM-DD)";
  if (!CURRENCY.test(form.currency)) errors.currency = "Three-letter ISO code, e.g. USD";
  if (!DECIMAL.test(form.total)) errors.total = "Total is required, e.g. 1320.00";
  for (const field of ["subtotal", "discount", "shipping", "tax_total"] as const) {
    if (form[field] && !DECIMAL.test(form[field])) errors[field] = "Use digits and a dot, e.g. 120.00";
  }
  form.line_items.forEach((item, index) => {
    for (const key of ["quantity", "unit_price", "amount"] as const) {
      if (item[key] && !DECIMAL.test(item[key])) {
        errors[`line_items.${index}.${key}`] = "Use digits and a dot";
      }
    }
  });
  if (form.override && !form.comment.trim()) errors.comment = "An override needs a comment";
  return errors;
}

const orNull = (value: string): string | null => (value.trim() ? value.trim() : null);

/** Request body for POST /api/documents/{id}/approve. */
export function buildApproveRequest(form: ReviewForm): ApproveRequest {
  const lineItems = form.line_items
    .filter((item) => Object.values(item).some((value) => value.trim()))
    .map((item) => ({
      description: orNull(item.description),
      quantity: orNull(item.quantity),
      unit_price: orNull(item.unit_price),
      amount: orNull(item.amount),
    }));
  return {
    values: {
      vendor_name: form.vendor_name.trim(),
      vendor_tax_id: orNull(form.vendor_tax_id),
      invoice_number: form.invoice_number.trim(),
      invoice_date: form.invoice_date,
      due_date: orNull(form.due_date),
      currency: form.currency.trim().toUpperCase(),
      subtotal: orNull(form.subtotal),
      discount: orNull(form.discount),
      shipping: orNull(form.shipping),
      tax_total: orNull(form.tax_total),
      tax_inclusive: form.tax_inclusive,
      total: form.total.trim(),
      line_items: lineItems,
    },
    vendor_id: form.vendor_id,
    date_format: form.date_format,
    override: form.override,
    comment: orNull(form.comment),
  };
}

/** The same date with day and month swapped, or null when the swap is impossible. */
export function swapDayMonth(iso: string): string | null {
  const match = ISO_DATE.exec(iso);
  if (!match) return null;
  const [, year, month, day] = match;
  const swapped = `${year}-${day}-${month}`;
  return validDate(swapped) ? swapped : null;
}

/** Day/month order normalization assumed for ambiguous dates (from the W1 message). */
export function assumedDateOrder(checks: CheckResult[]): DateOrder | null {
  for (const check of checks) {
    if (check.check_id !== "W1" || check.status !== "fail") continue;
    const match = /read as (DMY|MDY)/.exec(check.message);
    if (match) return match[1] as DateOrder;
  }
  return null;
}

/** Form dates for a chosen order: unchanged when it matches the assumption, else swapped. */
export function applyDateOrder(
  original: ReviewForm,
  form: ReviewForm,
  checks: CheckResult[],
  order: DateOrder,
): ReviewForm {
  const assumed = assumedDateOrder(checks);
  const ambiguous = new Set(
    checks.filter((c) => c.check_id === "W1" && c.status === "fail").map((c) => c.field),
  );
  const pick = (field: "invoice_date" | "due_date"): string => {
    if (order === assumed || !ambiguous.has(field)) return original[field];
    return swapDayMonth(original[field]) ?? original[field];
  };
  return {
    ...form,
    invoice_date: pick("invoice_date"),
    due_date: pick("due_date"),
    date_format: order,
  };
}

/** Amount with thousands separators and its currency, without changing its value. */
export function formatMoney(value: string | null, currency: string | null): string {
  if (!value) return "";
  const [whole, fraction] = value.split(".");
  const negative = whole.startsWith("-");
  const digits = negative ? whole.slice(1) : whole;
  const grouped = digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const text = `${negative ? "-" : ""}${grouped}${fraction !== undefined ? `.${fraction}` : ""}`;
  return currency ? `${text} ${currency}` : text;
}

/** Date and time with the month spelled out, e.g. 6 Oct 2026, 23:38, so day and month never swap. */
export function formatDateTime(iso: string, timeZone?: string): string {
  return new Date(iso).toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone,
  });
}

/** Long English date, e.g. 4 March 2026; the input is a YYYY-MM-DD date. */
export function formatDate(iso: string | null): string {
  if (!iso || !validDate(iso)) return iso ?? "";
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day)).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
}

const STATUS_LABELS: Record<DocumentStatus, string> = {
  received: "Received",
  processing: "Processing",
  skipped: "Skipped",
  failed: "Failed",
  needs_review: "Needs review",
  auto_approved: "Auto-approved",
  approved: "Approved",
  rejected: "Rejected",
  exported: "Exported",
};

export function statusLabel(status: DocumentStatus): string {
  return STATUS_LABELS[status] ?? status;
}

/** One-line description of a timeline event. */
export function describeEvent(event: DocumentEvent): string {
  const p = event.payload;
  if (event.event_type === "received") return `Received ${String(p.filename ?? "")}`.trim();
  if (event.event_type === "requeued") return `Retried after a crash (attempt ${String(p.attempts)})`;
  if (event.event_type !== "status_changed") return event.event_type;
  const parts = [`${statusLabel(p.from as DocumentStatus)} → ${statusLabel(p.to as DocumentStatus)}`];
  if (p.reason) parts.push(String(p.reason).replaceAll("_", " "));
  if (Array.isArray(p.flags) && p.flags.length) parts.push(`checks: ${p.flags.join(", ")}`);
  if (p.approval_mode) parts.push(String(p.approval_mode).replaceAll("_", " "));
  if (p.comment) parts.push(`“${String(p.comment)}”`);
  return parts.join(" · ");
}

/** Id of the vendor the server says already exists (409 on approve), if it is in the list. */
export function existingVendorId(detail: unknown, vendors: Vendor[]): string | null {
  if (typeof detail !== "object" || detail === null || !("vendor_id" in detail)) return null;
  const id = detail.vendor_id;
  return typeof id === "string" && vendors.some((v) => v.id === id) ? id : null;
}
