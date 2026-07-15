"use client";

/** Line/area time series with crosshair tooltip and optional brush zoom. */

import {
  Area,
  Brush,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  Legend,
} from "recharts";
import { SERIES } from "@/lib/utils";
import { ChartTooltip } from "./tooltip";

export interface SeriesDef {
  key: string;
  name: string;
  /** "line" (default) or filled "area" */
  kind?: "line" | "area";
}

export function TimeSeries({
  data,
  series,
  height = 280,
  xKey = "bucket",
  xFormatter,
  valueFormatter,
  brush = false,
}: {
  data: Record<string, unknown>[];
  series: SeriesDef[];
  height?: number;
  xKey?: string;
  xFormatter?: (v: string) => string;
  valueFormatter?: (v: number, name: string) => string;
  brush?: boolean;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
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
            />
          }
          cursor={{ stroke: "var(--baseline)", strokeDasharray: "3 3" }}
        />
        {series.length > 1 ? (
          <Legend
            wrapperStyle={{ fontSize: 12 }}
            formatter={(value) => (
              <span style={{ color: "var(--ink-2)" }}>{value}</span>
            )}
          />
        ) : null}
        {series.map((s, i) =>
          s.kind === "area" ? (
            <Area
              key={s.key}
              dataKey={s.key}
              name={s.name}
              stroke={SERIES[i % SERIES.length]}
              strokeWidth={2}
              fill={SERIES[i % SERIES.length]}
              fillOpacity={0.14}
              dot={false}
              activeDot={{ r: 4 }}
              isAnimationActive={false}
            />
          ) : (
            <Line
              key={s.key}
              dataKey={s.key}
              name={s.name}
              stroke={SERIES[i % SERIES.length]}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4 }}
              isAnimationActive={false}
            />
          ),
        )}
        {brush && data.length > 14 ? (
          <Brush
            dataKey={xKey}
            height={22}
            travellerWidth={8}
            stroke="var(--baseline)"
            fill="var(--surface-2)"
            tickFormatter={xFormatter}
          />
        ) : null}
      </ComposedChart>
    </ResponsiveContainer>
  );
}
