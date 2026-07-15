"use client";

/** Segment-flow sankey (Recharts) with ink labels and categorical node hues. */

import { Layer, Rectangle, ResponsiveContainer, Sankey, Tooltip } from "recharts";
import { SERIES } from "@/lib/utils";
import { ChartTooltip } from "./tooltip";

export interface SankeyData {
  nodes: string[];
  links: { source: string; target: string; value: number }[];
}

function makeSankeyNode(colorFor: (name: string) => string) {
  return function SankeyNode(props: {
    x?: number;
    y?: number;
    width?: number;
    height?: number;
    index?: number;
    payload?: { name: string; value: number; depth?: number };
    containerWidth?: number;
  }) {
    const { x = 0, y = 0, width = 0, height = 0, index = 0, payload } = props;
    // bipartite layout: depth 0 = left column (label to the right of the
    // node), deeper = right column (label to the left, inside the plot)
    const isRight =
      payload?.depth !== undefined
        ? payload.depth > 0
        : (props.containerWidth ?? 0) - (x + width) < 80;
    return (
      <Layer key={`node-${index}`}>
        <Rectangle
          x={x}
          y={y}
          width={width}
          height={height}
          fill={colorFor(payload?.name ?? "")}
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
  };
}

export function SegmentSankey({
  data,
  height = 360,
}: {
  data: SankeyData;
  height?: number;
}) {
  if (data.nodes.length === 0 || data.links.length === 0) return null;
  // Segment transitions are cyclic (expired → active_paid → expired), and a
  // sankey layout needs a DAG. Split identities into a bipartite "from" →
  // "to" pair of columns: cycles disappear by construction.
  const links = data.links.filter(
    (l) => l.value > 0 && data.nodes.includes(l.source) && data.nodes.includes(l.target),
  );
  const fromNames = [...new Set(links.map((l) => l.source))];
  const toNames = [...new Set(links.map((l) => l.target))];
  const fromIndex = new Map(fromNames.map((n, i) => [n, i]));
  const toIndex = new Map(toNames.map((n, i) => [n, fromNames.length + i]));
  const chartData = {
    nodes: [
      ...fromNames.map((name) => ({ name })),
      ...toNames.map((name) => ({ name })),
    ],
    links: links.map((l) => ({
      source: fromIndex.get(l.source)!,
      target: toIndex.get(l.target)!,
      value: l.value,
    })),
  };
  if (chartData.links.length === 0) return null;
  // hue follows the segment identity, shared across both columns
  const palette = new Map(
    [...new Set([...fromNames, ...toNames])].map((n, i) => [
      n,
      SERIES[i % SERIES.length],
    ]),
  );
  const NodeShape = makeSankeyNode((name) => palette.get(name) ?? SERIES[0]);
  return (
    <ResponsiveContainer width="100%" height={height}>
      <Sankey
        data={chartData}
        node={<NodeShape />}
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
