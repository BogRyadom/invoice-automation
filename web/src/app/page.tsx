"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { AuthGate } from "@/components/AuthGate";
import { Alert, Spinner, StatusBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { QUEUE_TABS, formatDateTime, formatMoney } from "@/lib/review";
import type { DocumentStatus, QueueRow, Stats } from "@/lib/types";

function reason(row: QueueRow): string | null {
  return (row.failure_reason ?? row.skip_reason)?.replaceAll("_", " ") ?? null;
}

function Queue() {
  const router = useRouter();
  const params = useSearchParams();
  const tab = QUEUE_TABS.find((t) => t.key === params.get("tab")) ?? QUEUE_TABS[0];
  const [rows, setRows] = useState<QueueRow[] | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.queue(tab.statuses), api.stats()])
      .then(([queue, counts]) => {
        if (cancelled) return;
        setRows(queue);
        setStats(counts);
        setError(null);
      })
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [tab]);

  const count = (statuses: DocumentStatus[]) =>
    statuses.reduce((sum, s) => sum + (stats?.documents_by_status[s] ?? 0), 0);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Documents</h1>
          <p className="text-sm text-zinc-500 dark:text-zinc-400">
            Invoices from the shared mailbox, newest first.
          </p>
        </div>
      </div>

      <nav className="flex gap-1 border-b border-zinc-200 dark:border-zinc-800" aria-label="Queue">
        {QUEUE_TABS.map((t) => {
          const active = t.key === tab.key;
          return (
            <button
              key={t.key}
              onClick={() => {
                setRows(null);
                router.replace(`/?tab=${t.key}`);
              }}
              className={`-mb-px flex items-center gap-2 border-b-2 px-3 py-2 text-sm font-medium ${
                active
                  ? "border-indigo-600 text-indigo-600 dark:text-indigo-400"
                  : "border-transparent text-zinc-500 hover:text-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-200"
              }`}
            >
              {t.label}
              <span className="rounded-full bg-zinc-100 px-1.5 text-xs text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
                {stats ? count(t.statuses) : "·"}
              </span>
            </button>
          );
        })}
      </nav>

      {error && <Alert tone="error" title="Could not load documents">{error}</Alert>}
      {!rows && !error && <Spinner />}
      {rows && rows.length === 0 && (
        <p className="py-12 text-center text-sm text-zinc-500 dark:text-zinc-400">
          Nothing here.
        </p>
      )}
      {rows && rows.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
          <table className="w-full text-left text-sm">
            <thead className="bg-zinc-50 text-xs uppercase tracking-wide text-zinc-500 dark:bg-zinc-900 dark:text-zinc-400">
              <tr>
                <th className="px-4 py-2 font-medium">Vendor</th>
                <th className="px-4 py-2 font-medium">Invoice no.</th>
                <th className="px-4 py-2 text-right font-medium">Amount</th>
                <th className="px-4 py-2 font-medium">Received</th>
                <th className="px-4 py-2 font-medium">Status</th>
                <th className="px-4 py-2 text-right font-medium">Flags</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
              {rows.map((row) => (
                <tr key={row.id} className="hover:bg-zinc-50 dark:hover:bg-zinc-900/60">
                  <td className="px-4 py-2.5">
                    <Link href={`/documents/${row.id}`} className="font-medium hover:underline">
                      {row.vendor ?? row.filename}
                    </Link>
                    {row.sender && (
                      <div className="text-xs text-zinc-500 dark:text-zinc-400">{row.sender}</div>
                    )}
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs">{row.invoice_number ?? "-"}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums">
                    {formatMoney(row.total, row.currency) || "-"}
                  </td>
                  <td className="px-4 py-2.5 text-zinc-500 dark:text-zinc-400">
                    {formatDateTime(row.received_at)}
                  </td>
                  <td className="px-4 py-2.5">
                    <StatusBadge status={row.status} />
                    {reason(row) && (
                      <div className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400">{reason(row)}</div>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    {row.flag_count > 0 ? (
                      <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-950 dark:text-amber-300">
                        {row.flag_count}
                      </span>
                    ) : (
                      <span className="text-xs text-zinc-400">0</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function QueuePage() {
  return (
    <AuthGate>
      <Suspense fallback={<Spinner />}>
        <Queue />
      </Suspense>
    </AuthGate>
  );
}
