/** Typed API client. All aggregation calls accept the global period filter. */

import type * as T from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<R>(path: string, init?: RequestInit): Promise<R> {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (res.status === 401 && typeof window !== "undefined") {
    if (!window.location.pathname.startsWith("/login")) {
      window.location.href = "/login";
    }
    throw new ApiError(401, "Not authenticated");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* keep statusText */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<R>;
}

export interface PeriodQuery {
  from?: string | null;
  to?: string | null;
}

function qs(
  period?: PeriodQuery,
  extra?: Record<string, string | number | undefined>,
): string {
  const params = new URLSearchParams();
  if (period?.from) params.set("from", period.from);
  if (period?.to) params.set("to", period.to);
  for (const [k, v] of Object.entries(extra ?? {})) {
    if (v !== undefined && v !== "") params.set(k, String(v));
  }
  const s = params.toString();
  return s ? `?${s}` : "";
}

// SWR-friendly: the key IS the URL, the fetcher IS request.
export const fetcher = <R,>(url: string) => request<R>(url);

export const api = {
  // auth
  login: (username: string, password: string) =>
    request<{ ok: boolean; username: string }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),
  me: () => request<{ username: string }>("/api/auth/me"),

  // url builders (used as SWR keys)
  urls: {
    overviewSummary: () => "/api/overview/summary",
    providersStatus: () => "/api/overview/providers-status",
    recentEvents: (limit = 50) => `/api/overview/events/recent${qs(undefined, { limit })}`,

    revenueTimeseries: (p: PeriodQuery, granularity: string) =>
      `/api/revenue/timeseries${qs(p, { granularity })}`,
    revenueKpis: (p: PeriodQuery) => `/api/revenue/kpis${qs(p)}`,
    ltvCohorts: () => "/api/revenue/ltv-cohorts",
    revenueByType: (p: PeriodQuery) => `/api/revenue/by-type${qs(p)}`,
    bonusShare: (p: PeriodQuery, granularity: string) =>
      `/api/revenue/bonus-share${qs(p, { granularity })}`,
    revenueCompare: () => "/api/revenue/compare",

    segments: () => "/api/users/segments",
    segmentFlows: (p: PeriodQuery) => `/api/users/segment-flows${qs(p)}`,
    retentionCohorts: (months = 12) =>
      `/api/users/retention-cohorts${qs(undefined, { months })}`,
    funnel: (p: PeriodQuery) => `/api/users/funnel${qs(p)}`,
    churn: () => "/api/users/churn",
    userSearch: (q: string) => `/api/users/search${qs(undefined, { q })}`,
    userCard: (id: number | string) => `/api/users/${id}/card`,

    referralsTop: (by: string, limit = 20) =>
      `/api/referrals/top${qs(undefined, { by, limit })}`,
    referralsSummary: () => "/api/referrals/summary",
    payouts: () => "/api/referrals/payouts",
    refTimeseries: (p: PeriodQuery, granularity: string) =>
      `/api/referrals/timeseries${qs(p, { granularity })}`,

    promosTable: (p: PeriodQuery) => `/api/promos/table${qs(p)}`,
    promosAbuse: (p: PeriodQuery) => `/api/promos/abuse${qs(p)}`,
    campaigns: () => "/api/promos/campaigns",
    broadcastImpact: (hours = 24) =>
      `/api/promos/broadcast-impact${qs(undefined, { hours })}`,

    devices: () => "/api/product/devices",
    bypass: (p: PeriodQuery) => `/api/product/bypass${qs(p)}`,
    clients: () => "/api/product/clients",

    nodes: () => "/api/infra/nodes",
    heatmap: () => "/api/infra/heatmap",
    peakHours: () => "/api/infra/peak-hours",

    alertsRecent: (limit = 50) => `/api/alerts/recent${qs(undefined, { limit })}`,
    alertsStatus: () => "/api/alerts/status",
  },

  alertsTest: () => request<{ sent: boolean }>("/api/alerts/test", { method: "POST" }),
};

export type { T };
