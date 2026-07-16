"use client";

/** Продукт: доп. устройства, ByPass, предпочитаемые клиенты. */

import { useMemo } from "react";
import useSWR from "swr";
import Link from "next/link";
import { api, fetcher } from "@/lib/api";
import type * as T from "@/lib/types";
import { usePeriod } from "@/lib/period";
import { fillTimeBuckets } from "@/lib/series";
import { fmtMoney, fmtNum, fmtPct, fmtBucket } from "@/lib/format";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { ChartCard } from "@/components/charts/container";
import { TimeSeries } from "@/components/charts/timeseries";
import { Donut } from "@/components/charts/donut";
import { StatCard } from "@/components/live/stat-card";

function UserLink({
  userId,
  username,
}: {
  userId: number;
  username: string | null;
}) {
  return (
    <Link href={`/users/${userId}`} className="text-accent hover:underline">
      {username ? `@${username}` : `id ${userId}`}
    </Link>
  );
}

export default function ProductPage() {
  const { query } = usePeriod();

  const { data: devices, isLoading: devicesLoading } =
    useSWR<T.DevicesResponse>(api.urls.devices(), fetcher, {
      keepPreviousData: true,
    });
  const { data: bypass, isLoading: bypassLoading } = useSWR<T.BypassResponse>(
    api.urls.bypass(query),
    fetcher,
    { keepPreviousData: true },
  );
  const { data: clients, isLoading: clientsLoading } =
    useSWR<T.ClientsResponse>(api.urls.clients(), fetcher, {
      keepPreviousData: true,
    });

  const devicesKpiLoad = devicesLoading && !devices;
  const bypassKpiLoad = bypassLoading && !bypass;

  const devicesTs = useMemo(
    () =>
      fillTimeBuckets(
        devices?.timeseries.map((r) => ({ ...r })) ?? [],
        "month",
        (bucket) => ({ bucket, amount: 0, count: 0 }),
      ),
    [devices],
  );
  const bypassTs = useMemo(
    () =>
      fillTimeBuckets(
        bypass?.timeseries.map((r) => ({ ...r })) ?? [],
        "day", // бэкенд бакетирует ByPass по дням
        (bucket) => ({ bucket, amount: 0, count: 0 }),
      ),
    [bypass],
  );
  const clientRows = clients?.clients ?? [];
  const donutData = useMemo(
    () => clientRows.map((c) => ({ name: c.client, value: c.count })),
    [clientRows],
  );
  const topConsumers = bypass?.top_consumers ?? [];

  const money = (v: number) => fmtMoney(v);
  // Таймсерия устройств — помесячная (последние 12 месяцев).
  const xMonth = (b: string) => fmtBucket(b, "month");
  // ByPass бэкенд бакетирует по дням (granularity не принимает).
  const xDay = (b: string) => fmtBucket(b, "day");
  const bypassValue = (v: number, name: string) =>
    name === "Сумма" ? fmtMoney(v) : fmtNum(v);

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-ink">Продукт</h1>

      {/* --- Доп. устройства --- */}
      <h2 className="text-sm font-medium text-ink-2">Доп. устройства</h2>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        <StatCard
          label="Активных слотов"
          value={fmtNum(devices?.active_slots)}
          loading={devicesKpiLoad}
        />
        <StatCard
          label="Юзеров с устройствами"
          value={fmtNum(devices?.users_with_devices)}
          loading={devicesKpiLoad}
        />
        <StatCard
          label="MRR устройств"
          value={fmtMoney(devices?.device_mrr)}
          hint="списания за 30 дней"
          loading={devicesKpiLoad}
        />
        <StatCard
          label="Отключённых слотов"
          value={fmtNum(devices?.churned_slots)}
          loading={devicesKpiLoad}
        />
        <StatCard
          label="Выручка за всё время"
          value={fmtMoney(devices?.revenue_total)}
          loading={devicesKpiLoad}
        />
      </div>

      <ChartCard
        title="Выручка с доп. устройств"
        subtitle="Помесячно, последние 12 месяцев"
        loading={devicesLoading && !devices}
        empty={!!devices && devices.timeseries.length === 0}
        csvRows={devicesTs}
        filename="device-revenue-monthly"
        height={280}
      >
        <TimeSeries
          data={devicesTs}
          series={[{ key: "amount", name: "Выручка", kind: "area" }]}
          height={280}
          xFormatter={xMonth}
          valueFormatter={money}
        />
      </ChartCard>

      {/* --- ByPass --- */}
      <h2 className="text-sm font-medium text-ink-2">ByPass</h2>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        <StatCard
          label="Покупок"
          value={fmtNum(bypass?.purchases)}
          loading={bypassKpiLoad}
        />
        <StatCard
          label="Выручка"
          value={fmtMoney(bypass?.revenue)}
          loading={bypassKpiLoad}
        />
        <StatCard
          label="Уникальных покупателей"
          value={fmtNum(bypass?.unique_buyers)}
          loading={bypassKpiLoad}
        />
        <StatCard
          label="Повторные покупки"
          value={fmtPct(bypass?.repeat_rate_pct)}
          hint={
            bypass ? `${fmtNum(bypass.repeat_buyers)} повторных покупателей` : undefined
          }
          loading={bypassKpiLoad}
        />
        <StatCard
          label="Средний интервал"
          value={
            bypass?.avg_days_between !== null &&
            bypass?.avg_days_between !== undefined
              ? `${fmtNum(bypass.avg_days_between)} дней`
              : "—"
          }
          hint="между повторными покупками"
          loading={bypassKpiLoad}
        />
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <ChartCard
          title="Динамика покупок ByPass"
          subtitle="Количество и сумма за период"
          loading={bypassLoading && !bypass}
          empty={!!bypass && bypass.timeseries.length === 0}
          csvRows={bypassTs}
          filename="bypass-timeseries"
          height={280}
        >
          <TimeSeries
            data={bypassTs}
            series={[
              { key: "count", name: "Покупки" },
              { key: "amount", name: "Сумма" },
            ]}
            height={280}
            brush
            xFormatter={xDay}
            valueFormatter={bypassValue}
          />
        </ChartCard>

        <ChartCard
          title="Топ потребители"
          subtitle="Покупатели ByPass за период"
          loading={bypassLoading && !bypass}
          empty={!!bypass && topConsumers.length === 0}
          csvRows={topConsumers.map((r) => ({ ...r }))}
          filename="bypass-top-consumers"
          height={280}
        >
          <Table>
            <THead>
              <TR>
                <TH>Пользователь</TH>
                <TH className="text-right">Покупок</TH>
                <TH className="text-right">Сумма</TH>
              </TR>
            </THead>
            <TBody>
              {topConsumers.map((r) => (
                <TR key={r.user_id}>
                  <TD className="font-medium">
                    <UserLink userId={r.user_id} username={r.username} />
                  </TD>
                  <TD className="text-right text-ink-2">
                    {fmtNum(r.purchases)}
                  </TD>
                  <TD className="text-right font-medium text-ink">
                    {fmtMoney(r.amount)}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </ChartCard>
      </div>

      {/* --- Клиенты --- */}
      <h2 className="text-sm font-medium text-ink-2">Клиенты</h2>
      <ChartCard
        title="Предпочитаемые клиенты"
        subtitle="Приложение, выбранное пользователем"
        loading={clientsLoading && !clients}
        empty={!!clients && clientRows.length === 0}
        csvRows={clientRows.map((r) => ({ ...r }))}
        filename="preferred-clients"
        height={260}
      >
        <Donut
          data={donutData}
          height={260}
          centerLabel="юзеров"
          valueFormatter={(v) => fmtNum(v)}
        />
      </ChartCard>
    </div>
  );
}
