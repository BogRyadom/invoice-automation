// Shapes returned by the FastAPI review endpoints. Amounts arrive as exact decimal strings.

export type DocumentStatus =
  | "received"
  | "processing"
  | "skipped"
  | "failed"
  | "needs_review"
  | "auto_approved"
  | "approved"
  | "rejected"
  | "exported";

export type QueueRow = {
  id: string;
  status: DocumentStatus;
  skip_reason: string | null;
  failure_reason: string | null;
  filename: string;
  sender: string | null;
  received_at: string;
  vendor: string | null;
  invoice_number: string | null;
  total: string | null;
  currency: string | null;
  flag_count: number;
};

export type CheckResult = {
  check_id: string;
  severity: "hard" | "warning";
  status: "pass" | "fail" | "not_run";
  field: string | null;
  message: string;
};

export type DocumentEvent = {
  event_type: string;
  actor: string;
  payload: Record<string, unknown>;
  created_at: string;
};

export type NormalizedLineItem = {
  description: string | null;
  quantity: string | null;
  unit_price: string | null;
  amount: string | null;
};

export type NormalizedInvoice = {
  vendor_name: string | null;
  vendor_key: string | null;
  vendor_tax_id: string | null;
  invoice_number: string | null;
  invoice_number_normalized: string | null;
  invoice_date: string | null;
  due_date: string | null;
  currency: string | null;
  subtotal: string | null;
  discount: string | null;
  shipping: string | null;
  tax_lines: { label: string; amount: string | null }[];
  tax_total: string | null;
  tax_inclusive: boolean;
  total: string | null;
  line_items: NormalizedLineItem[];
};

export type VendorCandidate = { vendor_id: string; canonical_name: string; score: number };

export type VendorMatch = {
  vendor_id: string | null;
  canonical_name: string | null;
  method: "tax_id" | "name" | "alias" | "none";
  candidates: VendorCandidate[];
};

export type DuplicateRef = {
  document_id: string;
  invoice_id: string | null;
  invoice_number: string;
  status: string;
};

export type Normalized = {
  document_type: "invoice" | "credit_note" | "other";
  invoice?: NormalizedInvoice;
  vendor?: VendorMatch;
  duplicate?: DuplicateRef | null;
  possible_duplicate?: DuplicateRef | null;
};

export type RawValues = {
  document_type: string;
  vendor_name_raw: string | null;
  vendor_tax_id_raw: string | null;
  invoice_number_raw: string | null;
  invoice_date_raw: string | null;
  due_date_raw: string | null;
  currency_raw: string | null;
  subtotal_raw: string | null;
  discount_raw: string | null;
  shipping_raw: string | null;
  tax_lines: { label: string; amount_raw: string }[];
  tax_inclusive_note_raw: string | null;
  total_raw: string | null;
  line_items: {
    description: string | null;
    quantity_raw: string | null;
    unit_price_raw: string | null;
    amount_raw: string | null;
  }[];
};

export type InvoiceRecord = {
  id: string;
  vendor_id: string;
  vendor: string;
  vendor_tax_id: string | null;
  invoice_number: string;
  invoice_date: string;
  due_date: string | null;
  currency: string;
  subtotal: string | null;
  discount: string | null;
  shipping: string | null;
  tax_total: string | null;
  total: string;
  approval_mode: "auto" | "human" | "human_override" | "manual_entry";
  approved_by: string | null;
  approved_at: string;
  exported_at: string | null;
  line_items: (NormalizedLineItem & { position: number })[];
};

export type DocumentDetail = {
  document: {
    id: string;
    status: DocumentStatus;
    skip_reason: string | null;
    failure_reason: string | null;
    duplicate_of_document_id: string | null;
    filename: string;
    sender: string | null;
    subject: string | null;
    received_at: string;
    extraction_path: "text" | "vision" | null;
    attempts: number;
    last_error: string | null;
  };
  extraction: {
    id: string;
    provider: string;
    model: string;
    prompt_version: string;
    normalized: Normalized | null;
    latency_ms: number | null;
    input_tokens: number | null;
    output_tokens: number | null;
  } | null;
  raw_values: RawValues | null;
  checks: CheckResult[];
  events: DocumentEvent[];
  invoice: InvoiceRecord | null;
  pdf_url: string | null;
};

export type Vendor = {
  id: string;
  canonical_name: string;
  normalized_name: string;
  tax_id: string | null;
  date_format: "DMY" | "MDY" | null;
};

export type Stats = {
  documents_by_status: Partial<Record<DocumentStatus, number>>;
  review_rate: { hits: number; total: number };
  edit_rate_by_field: Record<string, { hits: number; total: number }>;
  failures_by_reason: Record<string, number>;
  outbox: { pending: number; undeliverable: number };
};

export type ApproveRequest = {
  values: {
    vendor_name: string;
    vendor_tax_id: string | null;
    invoice_number: string;
    invoice_date: string;
    due_date: string | null;
    currency: string;
    subtotal: string | null;
    discount: string | null;
    shipping: string | null;
    tax_total: string | null;
    tax_inclusive: boolean;
    total: string;
    line_items: NormalizedLineItem[];
  };
  vendor_id: string | null;
  date_format: "DMY" | "MDY" | null;
  override: boolean;
  comment: string | null;
};
