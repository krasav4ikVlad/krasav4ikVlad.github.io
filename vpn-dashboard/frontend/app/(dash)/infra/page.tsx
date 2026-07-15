"use client";

/** Инфраструктура: ноды Remnawave (15s live), пики нагрузки по часам,
 *  heatmap активности по логам. */

import { useMemo } from "react";
import useSWR from "swr";
import { api, fetcher } from "@/lib/api";
import type {
  HeatmapResponse,
  NodesResponse,
  PeakHoursResponse,
} from "@/lib/types";
import { fmtBytes, fmtDateTime, fmtNum, fmtPct } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ChartCard } from "@/components/charts/container";
import { StackedBars } from "@/components/charts/stacked-bars";
import { ActivityHeatmap } from "@/components/charts/heatmap";
import { StatCard } from "@/components/live/stat-card";

type RealtimeItem = NonNullable<NodesResponse["realtime"]>[number];

function NodesSection() {
  const { data, isLoading } = useSWR<NodesResponse>(api.urls.nodes(), fetcher, {
    keepPreviousData: true,
    refreshInterval: 15000,
  });
  const loading = isLoading && !data;

  const realtimeByUuid = useMemo(() => {
    const m = new Map<string, RealtimeItem>();
    for (const r of data?.realtime ?? []) {
      if (r.node_uuid) m.set(r.node_uuid, r);
    }
    return m;
  }, [data]);

  const totals = useMemo(() => {
    let down = 0;
    let up = 0;
    for (const r of data?.realtime ?? []) {
      down += r.download_speed_bps;
      up += r.upload_speed_bps;
    }
    return { down, up };
  }, [data]);

  if (loading) {
    return (
      <div className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-36 w-full" />
          ))}
        </div>
      </div>
    );
  }

  if (data && !data.available) {
    return (
      <Card className="border-warning/40">
        <CardHeader className="mb-0">
          <CardTitle className="text-warning">Remnawave недоступна</CardTitle>
        </CardHeader>
        <p className="mt-2 text-sm text-muted">
          Панель не отвечает — статусы нод и онлайн временно недоступны.
          Данные обновятся автоматически, когда панель снова поднимется.
        </p>
      </Card>
    );
  }

  const nodes = data?.nodes ?? [];
  const onlineNodes = nodes.filter((n) => n.is_online).length;

  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <StatCard
          label="Пользователей онлайн"
          value={fmtNum(data?.online_total)}
          hint="по данным Remnawave"
        />
        <StatCard
          label="Нод онлайн"
          value={`${fmtNum(onlineNodes)} / ${fmtNum(nodes.length)}`}
        />
        <StatCard
          label="Скорость сейчас"
          value={
            data?.realtime
              ? `↓ ${fmtBytes(totals.down)}/с`
              : "—"
          }
          hint={data?.realtime ? `↑ ${fmtBytes(totals.up)}/с` : "нет realtime-данных"}
        />
      </div>

      {nodes.length === 0 ? (
        <Card>
          <p className="text-sm text-muted">Ноды не найдены.</p>
        </Card>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {nodes.map((node, i) => {
            const rt = node.uuid ? realtimeByUuid.get(node.uuid) : undefined;
            return (
              <Card key={node.uuid ?? `${node.name}-${i}`}>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span
                        className={cn(
                          "h-2 w-2 shrink-0 rounded-full",
                          node.is_online ? "bg-good" : "bg-critical",
                        )}
                      />
                      <span className="truncate text-sm font-medium text-ink">
                        {node.name}
                      </span>
                    </div>
                    {node.country ? (
                      <div className="mt-0.5 text-xs text-muted">{node.country}</div>
                    ) : null}
                  </div>
                  <span className="shrink-0 text-xs text-muted">
                    {node.is_online ? "онлайн" : "офлайн"}
                  </span>
                </div>

                <dl className="mt-3 space-y-1.5 text-xs">
                  <div className="flex items-center justify-between gap-2">
                    <dt className="text-muted">Пользователи</dt>
                    <dd className="text-ink-2">{fmtNum(node.users_online)}</dd>
                  </div>
                  <div className="flex items-center justify-between gap-2">
                    <dt className="text-muted">Трафик</dt>
                    <dd className="text-ink-2">
                      {fmtBytes(node.traffic_used_bytes)}
                      {" / "}
                      {node.traffic_limit_bytes > 0
                        ? fmtBytes(node.traffic_limit_bytes)
                        : "без лимита"}
                    </dd>
                  </div>
                  {node.cpu_percent !== null ? (
                    <div className="flex items-center justify-between gap-2">
                      <dt className="text-muted">CPU</dt>
                      <dd className="text-ink-2">{fmtPct(node.cpu_percent)}</dd>
                    </div>
                  ) : null}
                  {node.mem_percent !== null ? (
                    <div className="flex items-center justify-between gap-2">
                      <dt className="text-muted">Память</dt>
                      <dd className="text-ink-2">{fmtPct(node.mem_percent)}</dd>
                    </div>
                  ) : null}
                  {rt ? (
                    <div className="flex items-center justify-between gap-2">
                      <dt className="text-muted">Скорость</dt>
                      <dd className="text-ink-2">
                        ↓ {fmtBytes(rt.download_speed_bps)}/с · ↑{" "}
                        {fmtBytes(rt.upload_speed_bps)}/с
                      </dd>
                    </div>
                  ) : null}
                </dl>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}

function PeakHoursSection() {
  const { data, isLoading } = useSWR<PeakHoursResponse>(
    api.urls.peakHours(),
    fetcher,
    { keepPreviousData: true },
  );
  const hours = data?.hours ?? [];
  return (
    <ChartCard
      title="Пики нагрузки по часам"
      subtitle="сумма действий по логам за все дни недели, UTC"
      loading={isLoading && !data}
      empty={hours.length === 0 || hours.every((h) => h.count === 0)}
      csvRows={hours}
      filename="peak-hours"
      height={260}
    >
      <StackedBars
        data={hours}
        keys={["count"]}
        names={{ count: "Действия" }}
        xKey="hour"
        xFormatter={(h) => `${h}:00`}
        valueFormatter={(v) => fmtNum(v)}
        height={260}
      />
    </ChartCard>
  );
}

function HeatmapSection() {
  const { data, isLoading } = useSWR<HeatmapResponse>(
    api.urls.heatmap(),
    fetcher,
    { keepPreviousData: true },
  );
  const cells = data?.cells ?? [];
  const subtitle = data?.computed_at
    ? `по логам действий, UTC · обновлено ${fmtDateTime(data.computed_at)}`
    : "по логам действий, UTC";
  return (
    <ChartCard
      title="Heatmap активности"
      subtitle={subtitle}
      loading={isLoading && !data}
      empty={cells.length === 0}
      csvRows={cells}
      filename="activity-heatmap"
      height={240}
    >
      <ActivityHeatmap cells={cells} />
    </ChartCard>
  );
}

export default function InfraPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-ink">Инфраструктура</h1>
      <NodesSection />
      <PeakHoursSection />
      <HeatmapSection />
    </div>
  );
}
