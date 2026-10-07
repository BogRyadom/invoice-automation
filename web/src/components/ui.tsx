import type { ButtonHTMLAttributes, ReactNode } from "react";

import type { FieldStatus } from "@/lib/review";
import { statusLabel } from "@/lib/review";
import type { DocumentStatus } from "@/lib/types";

type Variant = "primary" | "secondary" | "danger" | "ghost";

const BUTTON_STYLES: Record<Variant, string> = {
  primary:
    "bg-indigo-600 text-white hover:bg-indigo-500 disabled:bg-indigo-300 dark:disabled:bg-indigo-900",
  secondary:
    "border border-zinc-300 bg-white text-zinc-800 hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100 dark:hover:bg-zinc-800",
  danger:
    "border border-red-300 bg-white text-red-700 hover:bg-red-50 dark:border-red-800 dark:bg-zinc-900 dark:text-red-300 dark:hover:bg-red-950",
  ghost: "text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800",
};

export function Button({
  variant = "secondary",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      {...props}
      className={`inline-flex items-center justify-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed ${BUTTON_STYLES[variant]} ${className}`}
    />
  );
}

const DOCUMENT_STATUS_STYLES: Record<DocumentStatus, string> = {
  received: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  processing: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300",
  needs_review: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  failed: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
  skipped: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400",
  rejected: "bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  auto_approved: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  approved: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  exported: "bg-teal-100 text-teal-800 dark:bg-teal-950 dark:text-teal-300",
};

export function StatusBadge({ status }: { status: DocumentStatus }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${DOCUMENT_STATUS_STYLES[status]}`}
    >
      {statusLabel(status)}
    </span>
  );
}

const FIELD_STYLES: Record<FieldStatus, { dot: string; label: string }> = {
  ok: { dot: "bg-emerald-500", label: "ok" },
  warning: { dot: "bg-amber-500", label: "warning" },
  error: { dot: "bg-red-500", label: "error" },
};

export function FieldStatusDot({ status }: { status: FieldStatus }) {
  const style = FIELD_STYLES[status];
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-zinc-500 dark:text-zinc-400">
      <span className={`h-2 w-2 rounded-full ${style.dot}`} aria-hidden />
      {style.label}
    </span>
  );
}

const ALERT_STYLES = {
  info: "border-sky-200 bg-sky-50 text-sky-900 dark:border-sky-900 dark:bg-sky-950/60 dark:text-sky-200",
  warning:
    "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950/60 dark:text-amber-200",
  error: "border-red-200 bg-red-50 text-red-900 dark:border-red-900 dark:bg-red-950/60 dark:text-red-200",
};

export function Alert({
  tone,
  title,
  children,
}: {
  tone: keyof typeof ALERT_STYLES;
  title?: string;
  children?: ReactNode;
}) {
  return (
    <div className={`rounded-md border px-3 py-2 text-sm ${ALERT_STYLES[tone]}`} role="status">
      {title && <p className="font-medium">{title}</p>}
      {children && <div className={title ? "mt-1" : ""}>{children}</div>}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-zinc-500 dark:text-zinc-400">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-zinc-300 border-t-indigo-600" />
      {label}
    </div>
  );
}

export const inputClass =
  "w-full rounded-md border border-zinc-300 bg-white px-2.5 py-1.5 text-sm text-zinc-900 shadow-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20 disabled:bg-zinc-50 disabled:text-zinc-500 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100 dark:disabled:bg-zinc-900/50";
