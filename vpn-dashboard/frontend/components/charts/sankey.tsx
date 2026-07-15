"use client";

/** Segment-flow sankey (Recharts) with ink labels and categorical node hues. */

import { Layer, Rectangle, ResponsiveContainer, Sankey, Tooltip } from "recharts";
import { SERIES } from "@/lib/utils";
import { ChartTooltip } from "./tooltip";

export interface SankeyData {
  nodes: string[];
  links: { source: string; target: string; value: number }[];
}

function SankeyNode(props: {
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  index?: number;
  payload?: { name: string; value: number };
  containerWidth?: number;
}) {
  const { x = 0, y = 0, width = 0, height = 0, index = 0, payload } = props;
  const isRight = (props.containerWidth ?? 0) - (x + width) < 80;
  return (
    <Layer key={`node-${index}`}>
      <Rectangle
        x={x}
        y={y}
        width={width}
        height={height}
        fill={SERIES[index % SERIES.length]}
        fillOpacity={0.9}
        radius={2}
      />
      <text
        x={isRight ? x - 6 : x + width + 6}
        y={y + height / 2}
        textAnchor={isRight ? "end" : "start"}
        dominantBaseline="middle"
        fontSize={11}
        fill="var(--ink-2)"
      >
        {payload?.name}
      </text>
    </Layer>
  );
}

export function SegmentSankey({
  data,
  height = 360,
}: {
  data: SankeyData;
  height?: number;
}) {
  if (data.nodes.length === 0 || data.links.length === 0) return null;
  const nodeIndex = new Map(data.nodes.map((n, i) => [n, i]));
  const chartData = {
    nodes: data.nodes.map((name) => ({ name })),
    links: data.links
      .filter(
        (l) =>
          nodeIndex.has(l.source) && nodeIndex.has(l.target) && l.value > 0,
      )
      .map((l) => ({
        source: nodeIndex.get(l.source)!,
        target: nodeIndex.get(l.target)!,
        value: l.value,
      })),
  };
  if (chartData.links.length === 0) return null;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <Sankey
        data={chartData}
        node={<SankeyNode />}
        nodePadding={24}
        margin={{ top: 8, right: 90, bottom: 8, left: 8 }}
        link={{ stroke: "var(--baseline)", strokeOpacity: 0.35 }}
      >
        <Tooltip
          content={
            <ChartTooltip
              valueFormatter={(v) => `${v.toLocaleString("ru-RU")} чел.`}
            />
          }
        />
      </Sankey>
    </ResponsiveContainer>
  );
}
