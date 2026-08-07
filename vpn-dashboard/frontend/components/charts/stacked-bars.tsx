"use client";

/** Stacked bars with 2px surface gaps between segments (dataviz mark spec). */

import {
  Bar,
  BarChart,
  Brush,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { SERIES } from "@/lib/utils";
import { ChartTooltip } from "./tooltip";

export function StackedBars({
  data,
  keys,
  names,
  height = 300,
  xKey = "bucket",
  xFormatter,
  valueFormatter,
  brush = false,
}: {
  data: Record<string, unknown>[];
  /** stack keys in fixed order — hue follows the entity */
  keys: string[];
  names?: Record<string, string>;
  height?: number;
  xKey?: string;
  xFormatter?: (v: string) => string;
  valueFormatter?: (v: number, name: string) => string;
  brush?: boolean;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid stroke="var(--grid)" strokeWidth={1} vertical={false} />
        <XAxis
          dataKey={xKey}
          tickFormatter={xFormatter}
          tick={{ fill: "var(--muted)", fontSize: 11 }}
          axisLine={{ stroke: "var(--baseline)" }}
          tickLine={false}
          minTickGap={24}
        />
        <YAxis
          tick={{ fill: "var(--muted)", fontSize: 11 }}
          axisLine={false}
          tickLine={false}
          width={52}
        />
        <Tooltip
          content={
            <ChartTooltip
              labelFormatter={(l) => (xFormatter ? xFormatter(String(l)) : String(l))}
              valueFormatter={valueFormatter}
              showTotal={keys.length > 1}
            />
          }
          cursor={{ fill: "var(--hairline)" }}
        />
        {keys.length > 1 ? (
          <Legend
            wrapperStyle={{ fontSize: 12 }}
            formatter={(value) => (
              <span style={{ color: "var(--ink-2)" }}>{value}</span>
            )}
          />
        ) : null}
        {keys.map((key, i) => (
          <Bar
            key={key}
            dataKey={key}
            name={names?.[key] ?? key}
            stackId="stack"
            fill={SERIES[i % SERIES.length]}
            stroke="var(--surface)"
            strokeWidth={1}
            maxBarSize={40}
            isAnimationActive={false}
          />
        ))}
        {brush && data.length > 20 ? (
          <Brush
            dataKey={xKey}
            height={22}
            travellerWidth={8}
            stroke="var(--baseline)"
            fill="var(--surface-2)"
            tickFormatter={xFormatter}
          />
        ) : null}
      </BarChart>
    </ResponsiveContainer>
  );
}
