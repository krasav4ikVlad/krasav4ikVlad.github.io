"use client";

/** Global period filter: today / 7d / 30d / 90d / all / custom range.
 *  Persisted in the URL (?period=30d or ?from=&to=) so links are shareable
 *  and the choice survives navigation between sections. */

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  type ReactNode,
} from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import type { PeriodQuery } from "./api";

export type PeriodPreset = "today" | "7d" | "30d" | "90d" | "all" | "custom";

export const PRESET_LABELS: Record<PeriodPreset, string> = {
  today: "Сегодня",
  "7d": "7 дней",
  "30d": "30 дней",
  "90d": "90 дней",
  all: "Всё время",
  custom: "Диапазон",
};

export interface PeriodState {
  preset: PeriodPreset;
  /** ISO range actually sent to the API (from is null for "all"). */
  query: PeriodQuery;
  customFrom: string | null;
  customTo: string | null;
  setPreset: (p: PeriodPreset) => void;
  setCustom: (from: string, to: string) => void;
}

/** N full UTC days back from today's UTC midnight — aligned with the
 *  backend's $dateTrunc day buckets, so the first bar is a complete day. */
function utcDaysBack(days: number): string {
  const start = new Date();
  start.setUTCHours(0, 0, 0, 0);
  start.setUTCDate(start.getUTCDate() - days);
  return start.toISOString();
}

function presetToQuery(
  preset: PeriodPreset,
  customFrom: string | null,
  customTo: string | null,
): PeriodQuery {
  const dayMs = 86_400_000;
  switch (preset) {
    case "today": {
      const start = new Date();
      start.setHours(0, 0, 0, 0);
      return { from: start.toISOString() };
    }
    case "7d":
      return { from: utcDaysBack(7) };
    case "30d":
      return { from: utcDaysBack(30) };
    case "90d":
      return { from: utcDaysBack(90) };
    case "all":
      return {};
    case "custom":
      return {
        from: customFrom ? new Date(customFrom).toISOString() : undefined,
        to: customTo
          ? new Date(new Date(customTo).getTime() + dayMs).toISOString()
          : undefined,
      };
  }
}

const PeriodContext = createContext<PeriodState | null>(null);

export function PeriodProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  const rawPreset = params.get("period");
  const customFrom = params.get("from");
  const customTo = params.get("to");
  const preset: PeriodPreset =
    rawPreset && rawPreset in PRESET_LABELS
      ? (rawPreset as PeriodPreset)
      : customFrom || customTo
        ? "custom"
        : "30d";

  const navigate = useCallback(
    (next: URLSearchParams) => {
      router.replace(`${pathname}?${next.toString()}`, { scroll: false });
    },
    [router, pathname],
  );

  const setPreset = useCallback(
    (p: PeriodPreset) => {
      const next = new URLSearchParams(params.toString());
      next.set("period", p);
      next.delete("from");
      next.delete("to");
      navigate(next);
    },
    [params, navigate],
  );

  const setCustom = useCallback(
    (from: string, to: string) => {
      const next = new URLSearchParams(params.toString());
      next.set("period", "custom");
      next.set("from", from);
      next.set("to", to);
      navigate(next);
    },
    [params, navigate],
  );

  const value = useMemo<PeriodState>(
    () => ({
      preset,
      query: presetToQuery(preset, customFrom, customTo),
      customFrom,
      customTo,
      setPreset,
      setCustom,
    }),
    [preset, customFrom, customTo, setPreset, setCustom],
  );

  return <PeriodContext.Provider value={value}>{children}</PeriodContext.Provider>;
}

export function usePeriod(): PeriodState {
  const ctx = useContext(PeriodContext);
  if (!ctx) throw new Error("usePeriod must be used inside PeriodProvider");
  return ctx;
}

/** Sensible chart granularity for the active period. */
export function granularityFor(preset: PeriodPreset): "day" | "week" | "month" {
  if (preset === "all") return "month";
  if (preset === "90d") return "week";
  return "day";
}
