"use client";

/** Пользователи и сегменты: распределение по сегментам, переходы (sankey),
 *  воронка конверсии, retention-когорты и churn с зоной риска. */

import Link from "next/link";
import useSWR from "swr";
import { api, fetcher } from "@/lib/api";
import type * as T from "@/lib/types";
import { usePeriod } from "@/lib/period";
import { ChartCard } from "@/components/charts/container";
import { Donut } from "@/components/charts/donut";
import { SegmentSankey } from "@/components/charts/sankey";
import { Funnel } from "@/components/charts/funnel";
import { CohortGrid } from "@/components/charts/cohort-grid";
import { TimeSeries } from "@/components/charts/timeseries";
import { StackedBars } from "@/components/charts/stacked-bars";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { TableSkeleton } from "@/components/ui/skeleton";
import {
  fmtBucket,
  fmtHoursApprox,
  fmtMoney,
  fmtNum,
  fmtPct,
} from "@/lib/format";

function fmtMonths(days: number | null): string {
  if (days === null) return "—";
  if (days < 45) return `${Math.round(days)} дн`;
  const months = days / 30;
  return `${months.toLocaleString("ru-RU", { maximumFractionDigits: 1 })} мес`;
}

function LifetimeCard() {
  const { data, isLoading } = useSWR<T.LifetimeResponse>(
    api.urls.lifetime(),
    fetcher,
    { keepPreviousData: true },
  );
  const survivalData = (data?.survival ?? []).map((s) => ({ ...s }));
  const histData = (data?.histogram ?? []).map((h) => ({ ...h }));
  const ltvByLife =
    data?.median_survival_months != null
      ? data.median_survival_months * data.monthly_sub_cost
      : null;

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Время жизни платящего</CardTitle>
          <p className="mt-0.5 text-xs text-muted">
            От первой оплаты до ухода. Живые юзеры учтены как «дожил и живёт
            дальше» — среднее они не занижают
          </p>
        </div>
        {data ? (
          <Badge variant="outline">
            живых {fmtNum(data.alive)} · ушло {fmtNum(data.departed)}
          </Badge>
        ) : null}
      </CardHeader>
      {isLoading && !data ? (
        <TableSkeleton rows={4} />
      ) : !data || data.paying_total === 0 ? (
        <div className="py-8 text-center text-sm text-muted">
          Нет платящих пользователей
        </div>
      ) : (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div className="rounded-card border border-hairline bg-surface-2/40 p-3">
              <div className="text-xs text-muted">Половина доживает до</div>
              <div className="text-xl font-semibold text-ink">
                {data.median_survival_months != null
                  ? `${data.median_survival_months} мес`
                  : `${data.survival.length}+ мес`}
              </div>
              <div className="mt-0.5 text-xs text-muted">
                по кривой выживаемости
              </div>
            </div>
            <div className="rounded-card border border-hairline bg-surface-2/40 p-3">
              <div className="text-xs text-muted">Жизнь ушедшего</div>
              <div className="text-xl font-semibold text-ink">
                {fmtMonths(data.median_lifetime_days)}
              </div>
              <div className="mt-0.5 text-xs text-muted">
                медиана · среднее {fmtMonths(data.avg_lifetime_days)}
              </div>
            </div>
            <div className="rounded-card border border-hairline bg-surface-2/40 p-3">
              <div className="text-xs text-muted">Ушедший принёс</div>
              <div className="text-xl font-semibold text-ink">
                {fmtMoney(data.avg_ltv_departed)}
              </div>
              <div className="mt-0.5 text-xs text-muted">
                факт. LTV за жизнь
              </div>
            </div>
            <div className="rounded-card border border-hairline bg-surface-2/40 p-3">
              <div className="text-xs text-muted">Живые уже с нами</div>
              <div className="text-xl font-semibold text-ink">
                {fmtMonths(data.avg_alive_age_days)}
              </div>
              <div className="mt-0.5 text-xs text-muted">
                в среднем · LTV ≥{" "}
                {ltvByLife != null ? fmtMoney(ltvByLife) : "—"}
              </div>
            </div>
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <div className="min-w-0">
              <div className="mb-1 text-xs text-muted">
                Кривая выживаемости: % платящих, доживших до N-го месяца
              </div>
              <TimeSeries
                data={survivalData as unknown as Record<string, unknown>[]}
                series={[{ key: "pct", name: "Живы", kind: "area" }]}
                height={210}
                xKey="month"
                xFormatter={(m) => `${m} мес`}
                valueFormatter={(v) => fmtPct(v)}
              />
            </div>
            <div className="min-w-0">
              <div className="mb-1 text-xs text-muted">
                Через сколько уходят (только ушедшие)
              </div>
              <StackedBars
                data={histData as unknown as Record<string, unknown>[]}
                keys={["count"]}
                names={{ count: "Ушли" }}
                height={210}
                xKey="bucket"
                xFormatter={(b) => b}
                valueFormatter={(v) => fmtNum(v)}
              />
            </div>
          </div>
        </div>
      )}
    </Card>
  );
}

function segmentVariant(
  segment: string | null,
): "good" | "warning" | "critical" | "default" {
  if (!segment) return "default";
  if (segment.includes("expired")) return "critical";
  if (segment.includes("expir")) return "warning";
  if (segment.startsWith("active")) return "good";
  return "default";
}

export default function UsersPage() {
  const { query } = usePeriod();

  const segments = useSWR<T.SegmentsResponse>(api.urls.segments(), fetcher, {
    keepPreviousData: true,
  });
  const flows = useSWR<T.SegmentFlows>(api.urls.segmentFlows(query), fetcher, {
    keepPreviousData: true,
  });
  const funnel = useSWR<{ steps: T.FunnelStep[] }>(
    api.urls.funnel(query),
    fetcher,
    { keepPreviousData: true },
  );
  const retention = useSWR<{ cohorts: T.RetentionCohort[] }>(
    api.urls.retentionCohorts(),
    fetcher,
    { keepPreviousData: true },
  );
  const churn = useSWR<T.ChurnResponse>(api.urls.churn(), fetcher, {
    keepPreviousData: true,
  });

  const donutData = (segments.data?.segments ?? []).map((s) => ({
    name: s.segment,
    value: s.count,
  }));
  const atRisk = churn.data?.at_risk ?? [];

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-ink">Пользователи и сегменты</h1>

      {/* Segments donut + segment-flow sankey */}
      <div className="grid gap-3 lg:grid-cols-2">
        <ChartCard
          title="Сегменты"
          subtitle={
            segments.data ? `Всего: ${fmtNum(segments.data.total)}` : undefined
          }
          loading={segments.isLoading && !segments.data}
          empty={!!segments.data && segments.data.segments.length === 0}
          csvRows={segments.data?.segments.map((s) => ({ ...s }))}
          filename="segments"
          height={260}
        >
          <Donut
            data={donutData}
            height={260}
            centerLabel="юзеров"
            valueFormatter={(v) => fmtNum(v)}
          />
        </ChartCard>

        <ChartCard
          title="Переходы сегментов"
          subtitle="За выбранный период"
          loading={flows.isLoading && !flows.data}
          empty={!!flows.data && flows.data.links.length === 0}
          csvRows={flows.data?.links}
          filename="segment-flows"
          height={360}
        >
          <SegmentSankey
            data={flows.data ?? { nodes: [], links: [] }}
            height={360}
          />
        </ChartCard>
      </div>

      {/* Lifetime / survival */}
      <LifetimeCard />

      {/* Conversion funnel */}
      <ChartCard
        title="Воронка"
        subtitle="Пользователи, зарегистрированные в периоде"
        loading={funnel.isLoading && !funnel.data}
        empty={!!funnel.data && funnel.data.steps.length === 0}
        csvRows={funnel.data?.steps.map((s) => ({ ...s }))}
        filename="funnel"
        height={200}
      >
        <Funnel steps={funnel.data?.steps ?? []} />
      </ChartCard>

      {/* Retention cohorts */}
      <ChartCard
        title="Retention по когортам"
        subtitle="Доля когорты с продлением в месяце M0, M1, …"
        loading={retention.isLoading && !retention.data}
        empty={!!retention.data && retention.data.cohorts.length === 0}
        csvRows={retention.data?.cohorts.map((c) => ({
          cohort: c.cohort,
          size: c.size,
          ...Object.fromEntries(c.retention.map((v, i) => [`m${i}`, v])),
        }))}
        filename="retention-cohorts"
        height={240}
      >
        <CohortGrid cohorts={retention.data?.cohorts ?? []} />
      </ChartCard>

      {/* Churn timeseries + at-risk list */}
      <div className="grid gap-3 lg:grid-cols-[1fr_360px]">
        <ChartCard
          title="Churn"
          subtitle="Доля оттока по месяцам"
          loading={churn.isLoading && !churn.data}
          empty={!!churn.data && churn.data.monthly.length === 0}
          csvRows={churn.data?.monthly}
          filename="churn"
        >
          <TimeSeries
            data={churn.data?.monthly ?? []}
            series={[{ key: "churn_rate_pct", name: "Отток, %" }]}
            xKey="month"
            xFormatter={(m) => fmtBucket(m, "month")}
            valueFormatter={(v) => fmtPct(v)}
          />
        </ChartCard>

        <Card>
          <CardHeader>
            <CardTitle>
              В зоне риска ({fmtNum(churn.data?.at_risk_count ?? 0)})
            </CardTitle>
          </CardHeader>
          {churn.isLoading && !churn.data ? (
            <TableSkeleton rows={8} />
          ) : atRisk.length === 0 ? (
            <div className="py-8 text-center text-sm text-muted">
              Нет пользователей в зоне риска
            </div>
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>Юзер</TH>
                  <TH>Сегмент</TH>
                  <TH className="text-right">Истекает</TH>
                  <TH className="text-right">Баланс</TH>
                </TR>
              </THead>
              <TBody>
                {atRisk.map((r) => (
                  <TR key={r.user_id}>
                    <TD>
                      <Link
                        href={`/users/${r.user_id}`}
                        className="text-accent hover:underline"
                      >
                        {r.username ? `@${r.username}` : `#${r.user_id}`}
                      </Link>
                    </TD>
                    <TD>
                      <Badge variant={segmentVariant(r.segment)}>
                        {r.segment ?? "—"}
                      </Badge>
                    </TD>
                    <TD className="text-right text-ink-2">
                      {r.days_to_expire === null
                        ? "—"
                        : fmtHoursApprox(Math.max(0, r.days_to_expire) * 24)}
                    </TD>
                    <TD className="text-right text-ink-2">
                      {fmtMoney(r.balance)}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </Card>
      </div>
    </div>
  );
}
