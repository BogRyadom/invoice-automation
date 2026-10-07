import { supabase } from "@/lib/supabase";
import type {
  ApproveRequest,
  DocumentDetail,
  DocumentStatus,
  QueueRow,
  Stats,
  Vendor,
} from "@/lib/types";

const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: unknown,
  ) {
    super(typeof detail === "string" ? detail : `Request failed with HTTP ${status}`);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const { data } = await supabase().auth.getSession();
  if (!data.session) throw new ApiError(401, "Not signed in");
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      Authorization: `Bearer ${data.session.access_token}`,
    },
  });
  if (!response.ok) {
    let detail: unknown = response.statusText;
    try {
      detail = ((await response.json()) as { detail?: unknown }).detail ?? detail;
    } catch {
      // Non-JSON error body: keep the status text.
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

export const api = {
  queue(statuses: DocumentStatus[]): Promise<QueueRow[]> {
    const query = statuses.map((s) => `status=${s}`).join("&");
    return request(`/api/documents?${query}&limit=200`);
  },
  document(id: string): Promise<DocumentDetail> {
    return request(`/api/documents/${id}`);
  },
  vendors(): Promise<Vendor[]> {
    return request("/api/vendors");
  },
  stats(): Promise<Stats> {
    return request("/api/stats");
  },
  approve(id: string, body: ApproveRequest): Promise<{ status: string; invoice_id: string }> {
    return request(`/api/documents/${id}/approve`, { method: "POST", body: JSON.stringify(body) });
  },
  reject(id: string, reason: string): Promise<{ status: string }> {
    return request(`/api/documents/${id}/reject`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    });
  },
  reprocess(id: string): Promise<{ status: string }> {
    return request(`/api/documents/${id}/reprocess`, { method: "POST" });
  },
};
