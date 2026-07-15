"use client";

/** Donut for categorical shares; caps at 8 slots ("другое" folds the tail). */

import { Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { SERIES } from "@/lib/utils";
import { ChartTooltip } from "./tooltip";

export interface DonutDatum {
  name: string;
  value: number;
}

export function Donut({
  data,
  height = 260,
  valueFormatter,
  centerLabel,
}: {
  data: DonutDatum[];
  height?: number;
  valueFormatter?: (v: number, name: string) => string;
  centerLabel?: string;
}) {
  const sorted = [...data].sort((a, b) => b.value - a.value);
  const shown =
    sorted.length <= 8
      ? sorted
      : [
          ...sorted.slice(0, 7),
          {
            name: "другое",
            value: sorted.slice(7).reduce((acc, d) => acc + d.value, 0),
          },
        ];
  const total = shown.reduce((acc, d) => acc + d.value, 0);
  return (
    <div className="relative">
      <ResponsiveContainer width="100%" height={height}>
        <PieChart>
          <Tooltip content={<ChartTooltip valueFormatter={valueFormatter} />} />
          <Legend
            layout="vertical"
            align="right"
            verticalAlign="middle"
            wrapperStyle={{ fontSize: 12, maxWidth: "45%" }}
            formatter={(value) => (
              <span style={{ color: "var(--ink-2)" }}>{value}</span>
            )}
          />
          <Pie
            data={shown}
            dataKey="value"
            nameKey="name"
            innerRadius="62%"
            outerRadius="88%"
            paddingAngle={2}
            stroke="var(--surface)"
            strokeWidth={2}
            isAnimationActive={false}
          >
            {shown.map((_, i) => (
              <Cell key={i} fill={SERIES[i % SERIES.length]} />
            ))}
          </Pie>
        </PieChart>
      </ResponsiveContainer>
      {centerLabel ? (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center pr-[45%]">
          <div className="text-center">
            <div className="text-xl font-semibold text-ink">{total.toLocaleString("ru-RU")}</div>
            <div className="text-xs text-muted">{centerLabel}</div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
