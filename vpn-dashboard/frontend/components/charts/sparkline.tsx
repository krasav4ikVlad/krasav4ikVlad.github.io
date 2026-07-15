"use client";

/** Tiny area sparkline for stat cards — no axes, hover shows the value. */

import { Area, AreaChart, ResponsiveContainer, Tooltip } from "recharts";
import { ChartTooltip } from "./tooltip";

export function Sparkline({
  data,
  height = 36,
  color = "var(--series-1)",
  valueFormatter,
}: {
  data: { date: string; value: number }[];
  height?: number;
  color?: string;
  valueFormatter?: (v: number) => string;
}) {
  if (!data || data.length === 0) return <div style={{ height }} />;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
        <Tooltip
          content={
            <ChartTooltip
              labelFormatter={() => ""}
              valueFormatter={(v) => (valueFormatter ? valueFormatter(v) : String(v))}
            />
          }
          cursor={{ stroke: "var(--baseline)" }}
        />
        <Area
          dataKey="value"
          name="значение"
          stroke={color}
          strokeWidth={1.5}
          fill={color}
          fillOpacity={0.15}
          dot={false}
          activeDot={{ r: 3 }}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
