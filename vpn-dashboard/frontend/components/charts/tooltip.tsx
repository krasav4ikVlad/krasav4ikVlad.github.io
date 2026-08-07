"use client";

/** Shared Recharts tooltip: surface card, ink text, series swatches. */

import type { TooltipProps } from "recharts";

export function ChartTooltip({
  active,
  payload,
  label,
  labelFormatter,
  valueFormatter,
  showTotal = false,
  totalLabel = "Итого",
}: TooltipProps<number, string> & {
  labelFormatter?: (label: unknown) => string;
  valueFormatter?: (value: number, name: string) => string;
  /** append a summary row over all shown series (for stacked charts) */
  showTotal?: boolean;
  totalLabel?: string;
}) {
  if (!active || !payload || payload.length === 0) return null;
  const shown = payload.filter((p) => p.value !== undefined && p.value !== null);
  if (shown.length === 0) return null;
  const total = shown.reduce((acc, p) => acc + (Number(p.value) || 0), 0);
  return (
    <div className="rounded-md border border-hairline bg-surface px-3 py-2 text-xs shadow-lg">
      {label !== undefined ? (
        <div className="mb-1 font-medium text-ink">
          {labelFormatter ? labelFormatter(label) : String(label)}
        </div>
      ) : null}
      <div className="space-y-0.5">
        {shown.map((p, i) => (
          <div key={i} className="flex items-center gap-2">
            <span
              className="h-2 w-2 shrink-0 rounded-sm"
              style={{ background: (p.color as string) ?? "var(--muted)" }}
            />
            <span className="text-ink-2">{p.name}</span>
            <span className="ml-auto pl-3 font-medium tabular text-ink">
              {valueFormatter
                ? valueFormatter(Number(p.value), String(p.name))
                : String(p.value)}
            </span>
          </div>
        ))}
      </div>
      {showTotal && shown.length > 1 ? (
        <div className="mt-1 flex items-center gap-2 border-t border-hairline pt-1">
          <span className="h-2 w-2 shrink-0" />
          <span className="font-medium text-ink">{totalLabel}</span>
          <span className="ml-auto pl-3 font-semibold tabular text-ink">
            {valueFormatter
              ? valueFormatter(total, totalLabel)
              : String(total)}
          </span>
        </div>
      ) : null}
    </div>
  );
}
