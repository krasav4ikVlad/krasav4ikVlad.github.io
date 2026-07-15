"use client";

/** Hour × weekday activity heatmap — sequential blue ramp (one hue,
 *  light → dark), CSS grid cells with hover tooltips. */

import { useMemo, useState } from "react";

const DOW_LABELS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const RAMP = [
  "var(--surface-2)",
  "var(--seq-100)",
  "var(--seq-250)",
  "var(--seq-400)",
  "var(--seq-550)",
  "var(--seq-700)",
] as const;

export function ActivityHeatmap({
  cells,
}: {
  cells: { dow: number; hour: number; count: number }[];
}) {
  const [hover, setHover] = useState<string | null>(null);
  const { grid, max } = useMemo(() => {
    const g = new Map<string, number>();
    let m = 0;
    for (const c of cells) {
      g.set(`${c.dow}:${c.hour}`, c.count);
      if (c.count > m) m = c.count;
    }
    return { grid: g, max: m };
  }, [cells]);

  const color = (count: number) => {
    if (max === 0 || count === 0) return RAMP[0];
    const idx = 1 + Math.min(4, Math.floor((count / max) * 5));
    return RAMP[idx];
  };

  return (
    <div className="overflow-x-auto">
      <div className="min-w-[560px]">
        <div className="grid grid-cols-[28px_repeat(24,1fr)] gap-[2px]">
          <div />
          {Array.from({ length: 24 }).map((_, h) => (
            <div key={h} className="pb-1 text-center text-[10px] text-muted">
              {h % 3 === 0 ? h : ""}
            </div>
          ))}
          {DOW_LABELS.map((label, dow) => (
            <>
              <div
                key={`l${dow}`}
                className="flex items-center pr-1 text-[11px] text-muted"
              >
                {label}
              </div>
              {Array.from({ length: 24 }).map((_, hour) => {
                const count = grid.get(`${dow}:${hour}`) ?? 0;
                const key = `${dow}:${hour}`;
                return (
                  <div
                    key={key}
                    title={`${label} ${hour}:00 — ${count.toLocaleString("ru-RU")} действий`}
                    onMouseEnter={() => setHover(key)}
                    onMouseLeave={() => setHover(null)}
                    className="aspect-square min-h-[14px] rounded-[3px]"
                    style={{
                      background: color(count),
                      outline:
                        hover === key ? "2px solid var(--ink-2)" : undefined,
                      outlineOffset: -1,
                    }}
                  />
                );
              })}
            </>
          ))}
        </div>
        <div className="mt-2 flex items-center justify-end gap-1 text-[10px] text-muted">
          меньше
          {RAMP.map((c, i) => (
            <span
              key={i}
              className="h-2.5 w-2.5 rounded-[2px]"
              style={{ background: c }}
            />
          ))}
          больше
        </div>
      </div>
    </div>
  );
}
