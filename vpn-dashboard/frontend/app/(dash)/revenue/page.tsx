"use client";

/** Выручка и финансы: KPI, источники, бонусы, типы трат, LTV, сравнение периодов. */

import { useMemo } from "react";
import useSWR from "swr";
import { api, fetcher } from "@/lib/api";
import type * as T from "@/lib/types";
import { usePeriod, granularityFor } from "@/lib/period";
import { fillTimeBuckets } from "@/lib/series";
import { fmtMoney, fmtNum, fmtPct, fmtBucket } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { TableSkeleton } from "@/components/ui/skeleton";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { ChartCard } from "@/components/charts/container";
import { StackedBars } from "@/components/charts/stacked-bars";
import { Donut } from "@/components/charts/donut";
import { TimeSeries } from "@/components/charts/timeseries";
import { StatCard } from "@/components/live/stat-card";

function CompareRow({
  label,
  current,
  previous,
  changePct,
}: {
  label: string;
  current: number;
  previous: number;
  changePct: number | null;
}) {
  return (
    <div className="rounded-card border border-hairline bg-surface-2/40 p-3">
      <div className="text-xs font-medium text-muted">{label}</div>
      <div className="mt-1 flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className="text-xl font-semibold text-ink">{fmtMoney(current)}</span>
        {changePct !== null ? (
          <span
            className={cn(
              "text-xs font-medium",
              changePct >= 0 ? "text-[var(--delta-good)]" : "text-critical",
            )}
          >
            {fmtPct(changePct, true)}
          </span>
        ) : (
          <span className="text-xs text-muted">—</span>
        )}
      </div>
      <div className="mt-0.5 text-xs text-muted">
        Прошлый период: {fmtMoney(previous)}
      </div>
    </div>
  );
}

export default function RevenuePage() {
  const { query, preset } = usePeriod();
  const granularity = granularityFor(preset);

  const { data: kpis, isLoading: kpisLoading } = useSWR<T.RevenueKpis>(
    api.urls.revenueKpis(query),
    fetcher,
    { keepPreviousData: true },
  );
  const { data: ts, isLoading: tsLoading } = useSWR<T.RevenueTimeseries>(
    api.urls.revenueTimeseries(query, granularity),
    fetcher,
    { keepPreviousData: true },
  );
  const { data: bonus, isLoading: bonusLoading } = useSWR<T.BonusShare>(
    api.urls.bonusShare(query, granularity),
    fetcher,
    { keepPreviousData: true },
  );
  const { data: byType, isLoading: byTypeLoading } = useSWR<{
    types: T.RevenueByTypeRow[];
  }>(api.urls.revenueByType(query), fetcher, { keepPreviousData: true });
  const { data: ltv, isLoading: ltvLoading } = useSWR<{ cohorts: T.LtvCohort[] }>(
    api.urls.ltvCohorts(),
    fetcher,
    { keepPreviousData: true },
  );
  const { data: cmp, isLoading: cmpLoading } = useSWR<T.RevenueCompare>(
    api.urls.revenueCompare(),
    fetcher,
    { keepPreviousData: true },
  );

  const kpiLoad = kpisLoading && !kpis;

  // [{bucket, source, revenue}] -> [{bucket, [source]: revenue}]
  const sourceData = useMemo(() => {
    if (!ts) return [];
    const byBucket = new Map<string, Record<string, unknown>>();
    for (const row of ts.series) {
      let entry = byBucket.get(row.bucket);
      if (!entry) {
        entry = { bucket: row.bucket };
        byBucket.set(row.bucket, entry);
      }
      entry[row.source] = (Number(entry[row.source]) || 0) + row.revenue;
    }
    const sorted = [...byBucket.values()].sort((a, b) =>
      String(a.bucket).localeCompare(String(b.bucket)),
    );
    // empty periods must stay visible as gaps, not silently vanish
    return fillTimeBuckets(sorted, granularity, (bucket) => ({ bucket }));
  }, [ts, granularity]);

  const donutData = useMemo(
    () =>
      (byType?.types ?? [])
        .filter((t) => t.amount > 0)
        .map((t) => ({ name: t.label, value: t.amount })),
    [byType],
  );

  const bonusData = useMemo(
    () =>
      fillTimeBuckets(
        (bonus?.series ?? []) as unknown as Record<string, unknown>[],
        granularity,
        (bucket) => ({ bucket, net: 0, bonus: 0, promo: 0 }),
      ),
    [bonus, granularity],
  );

  const money = (v: number) => fmtMoney(v);
  const xBucket = (b: string) => fmtBucket(b, granularity);

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-ink">Выручка и финансы</h1>

      {/* KPI */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        <StatCard
          label="Выручка"
          value={fmtMoney(kpis?.revenue)}
          deltaPct={kpis?.revenue_change_pct}
          hint={`Прошлый период: ${fmtMoney(kpis?.prev_revenue)}`}
          loading={kpiLoad}
        />
        <StatCard
          label="MRR"
          value={fmtMoney(kpis?.mrr)}
          hint={`Доп. устройства: ${fmtMoney(kpis?.device_mrr)}`}
          loading={kpiLoad}
        />
        <StatCard label="ARPU" value={fmtMoney(kpis?.arpu)} loading={kpiLoad} />
        <StatCard
          label="Средний чек"
          value={fmtMoney(kpis?.avg_check)}
          loading={kpiLoad}
        />
        <StatCard
          label="Медианный чек"
          value={fmtMoney(kpis?.median_check)}
          loading={kpiLoad}
        />
        <StatCard
          label="Платящих юзеров"
          value={fmtNum(kpis?.paying_users)}
          hint={`Платежей: ${fmtNum(kpis?.payments_count)}`}
          loading={kpiLoad}
        />
      </div>

      {/* Выручка по источникам */}
      <ChartCard
        title="Выручка по источникам"
        subtitle="Чистые пополнения (net) по платёжным провайдерам"
        loading={tsLoading && !ts}
        empty={!!ts && ts.series.length === 0}
        csvRows={ts?.series.map((r) => ({ ...r }))}
        filename="revenue-by-source"
        height={300}
      >
        <StackedBars
          data={sourceData}
          keys={ts?.sources ?? []}
          height={300}
          brush
          xFormatter={xBucket}
          valueFormatter={money}
        />
      </ChartCard>

      <div className="grid gap-3 lg:grid-cols-2">
        {/* Доля бонусов и акций */}
        <ChartCard
          title="Доля бонусов и акций"
          subtitle="Живые деньги против подаренных бонусов и промокодов"
          loading={bonusLoading && !bonus}
          empty={!!bonus && bonus.series.length === 0}
          csvRows={bonus?.series}
          filename="bonus-share"
          height={260}
        >
          <>
            <StackedBars
              data={bonusData}
              keys={["net", "bonus", "promo"]}
              names={{
                net: "Живые деньги",
                bonus: "Бонусы",
                promo: "Промокоды",
              }}
              height={260}
              xFormatter={xBucket}
              valueFormatter={money}
            />
            {bonus ? (
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
                <span>
                  Живые деньги:{" "}
                  <span className="font-medium text-ink-2">
                    {fmtMoney(bonus.totals.net)}
                  </span>
                </span>
                <span>
                  Бонусы:{" "}
                  <span className="font-medium text-ink-2">
                    {fmtMoney(bonus.totals.bonus)}
                  </span>
                </span>
                <span>
                  Промокоды:{" "}
                  <span className="font-medium text-ink-2">
                    {fmtMoney(bonus.totals.promo)}
                  </span>
                </span>
                <span>
                  Доля подарков:{" "}
                  <span className="font-medium text-ink-2">
                    {fmtPct(bonus.totals.share_pct)}
                  </span>
                </span>
              </div>
            ) : null}
          </>
        </ChartCard>

        {/* Выручка по типам */}
        <ChartCard
          title="Выручка по типам"
          subtitle="На что тратят: списания по видам услуг"
          loading={byTypeLoading && !byType}
          empty={!!byType && donutData.length === 0}
          csvRows={byType?.types.map((t) => ({ ...t }))}
          filename="revenue-by-type"
          height={260}
        >
          <Donut data={donutData} height={260} valueFormatter={money} />
        </ChartCard>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        {/* LTV по когортам */}
        <Card>
          <CardHeader>
            <CardTitle>LTV по когортам</CardTitle>
            <span className="text-xs text-muted">по месяцу регистрации</span>
          </CardHeader>
          {ltvLoading && !ltv ? (
            <TableSkeleton rows={8} />
          ) : !ltv || ltv.cohorts.length === 0 ? (
            <div className="py-8 text-center text-sm text-muted">
              Нет данных
            </div>
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>Когорта</TH>
                  <TH className="text-right">Пользователей</TH>
                  <TH className="text-right">Выручка</TH>
                  <TH className="text-right">LTV</TH>
                </TR>
              </THead>
              <TBody>
                {ltv.cohorts.map((c) => (
                  <TR key={c.cohort}>
                    <TD className="font-medium text-ink">{c.cohort}</TD>
                    <TD className="text-right text-ink-2">{fmtNum(c.users)}</TD>
                    <TD className="text-right text-ink-2">
                      {fmtMoney(c.revenue)}
                    </TD>
                    <TD className="text-right font-medium text-ink">
                      {fmtMoney(c.ltv)}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </Card>

        {/* Сравнение периодов */}
        <ChartCard
          title="Сравнение периодов"
          subtitle="Месяц и неделя против прошлых, выручка по неделям"
          loading={cmpLoading && !cmp}
          empty={!cmp}
          csvRows={cmp?.wow}
          filename="revenue-wow"
          height={200}
        >
          <>
            {cmp ? (
              <div className="mb-3 grid gap-3 sm:grid-cols-2">
                <CompareRow
                  label="Месяц к прошлому месяцу"
                  current={cmp.month.current}
                  previous={cmp.month.previous}
                  changePct={cmp.month.change_pct}
                />
                <CompareRow
                  label="Неделя к прошлой неделе"
                  current={cmp.week.current}
                  previous={cmp.week.previous}
                  changePct={cmp.week.change_pct}
                />
              </div>
            ) : null}
            <TimeSeries
              data={cmp?.wow ?? []}
              series={[{ key: "revenue", name: "Выручка", kind: "area" }]}
              height={200}
              xKey="week"
              valueFormatter={money}
            />
          </>
        </ChartCard>
      </div>
    </div>
  );
}
