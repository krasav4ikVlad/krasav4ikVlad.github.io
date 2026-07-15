"use client";

/** Realtime counter tile with a sparkline under the value. */

import type { ReactNode } from "react";
import { Card } from "@/components/ui/card";
import { StatSkeleton } from "@/components/ui/skeleton";
import { Sparkline } from "@/components/charts/sparkline";
import { fmtPct } from "@/lib/format";
import { cn } from "@/lib/utils";

export function StatCard({
  label,
  value,
  hint,
  deltaPct,
  spark,
  sparkColor,
  sparkFormatter,
  loading,
  icon,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  deltaPct?: number | null;
  spark?: { date: string; value: number }[];
  sparkColor?: string;
  sparkFormatter?: (v: number) => string;
  loading?: boolean;
  icon?: ReactNode;
}) {
  return (
    <Card className="p-4">
      {loading ? (
        <StatSkeleton />
      ) : (
        <>
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-medium text-muted">{label}</span>
            {icon}
          </div>
          <div className="mt-1 flex items-baseline gap-2">
            <span className="text-2xl font-semibold text-ink">{value}</span>
            {deltaPct !== undefined && deltaPct !== null ? (
              <span
                className={cn(
                  "text-xs font-medium",
                  deltaPct >= 0 ? "text-[var(--delta-good)]" : "text-critical",
                )}
              >
                {deltaPct >= 0 ? "↑" : "↓"} {fmtPct(Math.abs(deltaPct))}
              </span>
            ) : null}
          </div>
          {hint ? <div className="mt-0.5 text-xs text-muted">{hint}</div> : null}
          {spark && spark.length > 1 ? (
            <div className="mt-2 -mb-1">
              <Sparkline
                data={spark}
                color={sparkColor}
                valueFormatter={sparkFormatter}
              />
            </div>
          ) : null}
        </>
      )}
    </Card>
  );
}
