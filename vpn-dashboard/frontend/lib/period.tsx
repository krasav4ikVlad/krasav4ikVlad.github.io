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
  /** Shift the window by its own length: -1 = earlier days, +1 = later.
   *  Reaching today snaps back to the anchored preset. */
  shiftWindow: (dir: -1 | 1) => void;
  canShiftBack: boolean;
  canShiftForward: boolean;
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

const DAY_MS = 86_400_000;
const PRESET_DAYS: Partial<Record<PeriodPreset, number>> = {
  today: 1,
  "7d": 7,
  "30d": 30,
  "90d": 90,
};

function toDateStr(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

function todayUtcStartMs(): number {
  const d = new Date();
  d.setUTCHours(0, 0, 0, 0);
  return d.getTime();
}

/** Inclusive [from, to] date-window (ms of UTC midnights) for the state. */
function currentWindow(
  preset: PeriodPreset,
  customFrom: string | null,
  customTo: string | null,
): { fromMs: number; toMs: number } | null {
  const today = todayUtcStartMs();
  const days = PRESET_DAYS[preset];
  if (days !== undefined) {
    // якорные пресеты: N дней, заканчивая сегодняшним
    return { fromMs: today - (days - 1) * DAY_MS, toMs: today };
  }
  if (preset === "custom" && customFrom) {
    const fromMs = new Date(customFrom + "T00:00:00Z").getTime();
    const toMs = customTo
      ? new Date(customTo + "T00:00:00Z").getTime()
      : today;
    if (Number.isNaN(fromMs) || Number.isNaN(toMs)) return null;
    return { fromMs, toMs: Math.max(fromMs, toMs) };
  }
  return null; // "all"
}

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

  const window_ = currentWindow(preset, customFrom, customTo);
  const canShiftBack = window_ !== null;
  const canShiftForward =
    window_ !== null && window_.toMs < todayUtcStartMs();

  const shiftWindow = useCallback(
    (dir: -1 | 1) => {
      const win = currentWindow(preset, customFrom, customTo);
      if (win === null) return; // "всё время" двигать некуда
      const lenDays =
        Math.round((win.toMs - win.fromMs) / DAY_MS) + 1; // inclusive
      const today = todayUtcStartMs();
      const newFrom = win.fromMs + dir * lenDays * DAY_MS;
      const newTo = win.toMs + dir * lenDays * DAY_MS;
      if (dir === 1 && newTo >= today) {
        // окно дошло до сегодня — возвращаемся к живому пресету той же длины
        const presetOfLen = (
          Object.entries(PRESET_DAYS) as [PeriodPreset, number][]
        ).find(([, d]) => d === lenDays)?.[0];
        if (presetOfLen) {
          setPreset(presetOfLen);
        } else {
          setCustom(toDateStr(today - (lenDays - 1) * DAY_MS), toDateStr(today));
        }
        return;
      }
      setCustom(toDateStr(newFrom), toDateStr(newTo));
    },
    [preset, customFrom, customTo, setPreset, setCustom],
  );

  const value = useMemo<PeriodState>(
    () => ({
      preset,
      query: presetToQuery(preset, customFrom, customTo),
      customFrom,
      customTo,
      setPreset,
      setCustom,
      shiftWindow,
      canShiftBack,
      canShiftForward,
    }),
    [preset, customFrom, customTo, setPreset, setCustom, shiftWindow,
     canShiftBack, canShiftForward],
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
