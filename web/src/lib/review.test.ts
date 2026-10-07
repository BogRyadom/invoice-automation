import { describe, expect, it } from "vitest";

import {
  applyDateOrder,
  assumedDateOrder,
  buildApproveRequest,
  describeEvent,
  documentChecks,
  existingVendorId,
  fieldStatus,
  formatDate,
  formatDateTime,
  formatMoney,
  initialForm,
  printedValue,
  shownStatus,
  swapDayMonth,
  validateForm,
} from "@/lib/review";
import type { CheckResult, Normalized, RawValues, Vendor } from "@/lib/types";

const check = (check_id: string, field: string | null, status: CheckResult["status"] = "fail", message = ""): CheckResult => ({
  check_id,
  severity: check_id.startsWith("H") ? "hard" : "warning",
  status,
  field,
  message,
});

const NORMALIZED: Normalized = {
  document_type: "invoice",
  invoice: {
    vendor_name: "Halvren Maschinenbau GmbH",
    vendor_key: "halvren maschinenbau",
    vendor_tax_id: "ZZ918273645",
    invoice_number: "RE-2026-0318",
    invoice_number_normalized: "RE20260318",
    invoice_date: "2026-03-04",
    due_date: "2026-04-03",
    currency: "EUR",
    subtotal: "607.00",
    discount: null,
    shipping: null,
    tax_lines: [{ label: "MwSt. 19 %", amount: "115.33" }],
    tax_total: "115.33",
    tax_inclusive: false,
    total: "722.33",
    line_items: [{ description: "Kugellager", quantity: "50", unit_price: "4.80", amount: "240.00" }],
  },
  vendor: { vendor_id: "v1", canonical_name: "Halvren Maschinenbau GmbH", method: "tax_id", candidates: [] },
};

const W1 = [
  check("W1", "invoice_date", "fail", "'04.03.2026' read as DMY"),
  check("W1", "due_date", "fail", "'03.04.2026' read as DMY"),
];

describe("field status", () => {
  it.each([
    [[check("H2", "total")], "total", "error"],
    [[check("W6", "total")], "total", "warning"],
    [[check("H2", "total", "pass")], "total", "ok"],
    [[check("W2", "amounts")], "subtotal", "warning"],
    [[check("W2", "amounts")], "line_items", "warning"],
    [[check("H1", "tax_lines")], "tax_total", "error"],
    [[check("W4", "vendor_name"), check("H5", "vendor_name")], "vendor_name", "error"],
  ] as const)("%j on %s is %s", (checks, field, expected) => {
    expect(fieldStatus([...checks], field)).toBe(expected);
  });

  it("shows no status when nothing was checked or an empty field has nothing flagged", () => {
    const checks = [check("H2", "total", "pass"), check("H6", "invoice_number")];
    expect(shownStatus([], "total", true)).toBeNull();
    expect(shownStatus(checks, "discount", false)).toBeNull();
    expect(shownStatus(checks, "total", true)).toBe("ok");
    expect(shownStatus(checks, "invoice_number", false)).toBe("error");
  });

  it("keeps checks without a form field for the document banner", () => {
    const checks = [check("W5", null), check("W9", null), check("W4", "vendor_name"), check("H4", null, "not_run")];
    expect(documentChecks(checks).map((c) => c.check_id)).toEqual(["W5", "W9"]);
  });
});

describe("printed values", () => {
  const raw = {
    total_raw: "722,33",
    tax_lines: [{ label: "MwSt. 19 %", amount_raw: "115,33" }],
  } as unknown as RawValues;

  it("maps form fields to raw values", () => {
    expect(printedValue(raw, "total")).toBe("722,33");
    expect(printedValue(raw, "tax_total")).toBe("MwSt. 19 %: 115,33");
    expect(printedValue(null, "total")).toBeNull();
  });
});

describe("form", () => {
  it("prefills from the extraction and is empty for manual entry", () => {
    expect(initialForm(NORMALIZED).total).toBe("722.33");
    expect(initialForm(NORMALIZED).vendor_id).toBe("v1");
    expect(initialForm(null).invoice_number).toBe("");
    expect(initialForm(null).line_items).toEqual([]);
  });

  it("accepts a correct form", () => {
    expect(validateForm(initialForm(NORMALIZED))).toEqual({});
  });

  it.each([
    ["invoice_date", "2026-02-30"],
    ["currency", "eu"],
    ["total", "1,320.00"],
    ["subtotal", "abc"],
  ] as const)("rejects %s = %s", (field, value) => {
    const form = { ...initialForm(NORMALIZED), [field]: value };
    expect(Object.keys(validateForm(form))).toContain(field);
  });

  it("requires a comment for an override", () => {
    const form = { ...initialForm(NORMALIZED), override: true };
    expect(validateForm(form).comment).toBeDefined();
    expect(validateForm({ ...form, comment: "Checked with the vendor" }).comment).toBeUndefined();
  });

  it("builds the approve request", () => {
    const form = {
      ...initialForm(NORMALIZED),
      currency: "eur",
      vendor_tax_id: " ",
      line_items: [...initialForm(NORMALIZED).line_items, { description: "", quantity: "", unit_price: "", amount: "" }],
    };
    const request = buildApproveRequest(form);
    expect(request.values.currency).toBe("EUR");
    expect(request.values.vendor_tax_id).toBeNull();
    expect(request.values.discount).toBeNull();
    expect(request.values.line_items).toHaveLength(1);
    expect(request.vendor_id).toBe("v1");
    expect(request.comment).toBeNull();
  });
});

describe("ambiguous dates", () => {
  it.each([
    ["2026-03-04", "2026-04-03"],
    ["2026-12-01", "2026-01-12"],
    ["2026-03-14", null],
    ["not a date", null],
  ])("swaps %s to %s", (iso, expected) => {
    expect(swapDayMonth(iso)).toBe(expected);
  });

  it("reads the assumed order from W1", () => {
    expect(assumedDateOrder(W1)).toBe("DMY");
    expect(assumedDateOrder([])).toBeNull();
  });

  it("swaps only ambiguous dates when the reviewer picks the other order", () => {
    const original = initialForm(NORMALIZED);
    const mdy = applyDateOrder(original, original, W1, "MDY");
    expect([mdy.invoice_date, mdy.due_date, mdy.date_format]).toEqual(["2026-04-03", "2026-03-04", "MDY"]);
    const back = applyDateOrder(original, mdy, W1, "DMY");
    expect([back.invoice_date, back.due_date, back.date_format]).toEqual(["2026-03-04", "2026-04-03", "DMY"]);
    const onlyInvoiceDate = applyDateOrder(original, original, [W1[0]], "MDY");
    expect(onlyInvoiceDate.due_date).toBe("2026-04-03");
  });
});

describe("formatting", () => {
  it.each([
    ["1234567.50", "USD", "1,234,567.50 USD"],
    ["722.33", null, "722.33"],
    ["-82.57", "EUR", "-82.57 EUR"],
    [null, "EUR", ""],
  ])("money %s %s", (value, currency, expected) => {
    expect(formatMoney(value, currency)).toBe(expected);
  });

  it("formats dates in long English form", () => {
    expect(formatDate("2026-03-04")).toBe("4 March 2026");
    expect(formatDate(null)).toBe("");
  });

  it("formats date and time with the month spelled out", () => {
    expect(formatDateTime("2026-10-06T21:38:43Z", "UTC")).toBe("6 Oct 2026, 21:38");
    expect(formatDateTime("2026-10-06T21:38:43Z", "Europe/Kyiv")).toBe("7 Oct 2026, 00:38");
  });

  it("describes timeline events", () => {
    const event = (payload: Record<string, unknown>) => ({ event_type: "status_changed", actor: "worker", payload, created_at: "" });
    expect(describeEvent(event({ from: "processing", to: "needs_review", flags: ["W4"] }))).toBe(
      "Processing → Needs review · checks: W4",
    );
    expect(describeEvent(event({ from: "needs_review", to: "approved", approval_mode: "human_override", comment: "ok" }))).toBe(
      "Needs review → Approved · human override · “ok”",
    );
  });
});

describe("existing vendor on approve", () => {
  const vendors: Vendor[] = [
    { id: "v1", canonical_name: "Ostberg Facilities Co.", normalized_name: "ostberg facilities", tax_id: null, date_format: null },
  ];

  it("picks the vendor the server points to", () => {
    expect(existingVendorId({ message: "vendor already exists", vendor_id: "v1" }, vendors)).toBe("v1");
  });

  it("ignores unknown vendors and other error bodies", () => {
    expect(existingVendorId({ vendor_id: "v2" }, vendors)).toBeNull();
    expect(existingVendorId("vendor not found", vendors)).toBeNull();
    expect(existingVendorId(null, vendors)).toBeNull();
  });
});
