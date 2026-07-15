"use client";

/** Рефералка: KPI, динамика реферального дохода, топ рефереров, выплаты. */

import { useMemo, useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import { api, fetcher } from "@/lib/api";
import type * as T from "@/lib/types";
import { usePeriod, granularityFor } from "@/lib/period";
import { fmtMoney, fmtNum, fmtPct, fmtBucket, fmtDateTime } from "@/lib/format";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { TableSkeleton } from "@/components/ui/skeleton";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { ChartCard } from "@/components/charts/container";
import { TimeSeries } from "@/components/charts/timeseries";
import { StatCard } from "@/components/live/stat-card";

type TopBy = "turnover" | "earned" | "paying";

const TOP_OPTIONS: { value: TopBy; label: string }[] = [
  { value: "turnover", label: "Оборот" },
  { value: "earned", label: "Заработок" },
  { value: "paying", label: "Платящие" },
];

const DATE_KEYS = ["dt", "date", "created_at"];
const AMOUNT_KEYS = ["amount", "sum"];
const KNOWN_KEYS = new Set([...DATE_KEYS, ...AMOUNT_KEYS, "user_id", "username"]);

/** История выплат приходит замаскированными dict'ами с неизвестными ключами —
 *  достаём дату/сумму по эвристике, остальное показываем как есть. */
function pickDate(entry: Record<string, unknown>): string | null {
  for (const k of DATE_KEYS) {
    const v = entry[k];
    if (typeof v === "string" && v) return v;
  }
  return null;
}

function pickAmount(entry: Record<string, unknown>): number | null {
  for (const k of AMOUNT_KEYS) {
    const v = entry[k];
    if (typeof v === "number" && !Number.isNaN(v)) return v;
    if (typeof v === "string" && v.trim() !== "" && !Number.isNaN(Number(v))) {
      return Number(v);
    }
  }
  return null;
}

function restJson(entry: Record<string, unknown>): string {
  try {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(entry)) {
      if (!KNOWN_KEYS.has(k) && v !== null && v !== undefined && v !== "") {
        rest[k] = v;
      }
    }
    const s = JSON.stringify(rest);
    return s === "{}" ? "" : s;
  } catch {
    return "";
  }
}

function UserLink({
  userId,
  username,
}: {
  userId: unknown;
  username: unknown;
}) {
  const id =
    typeof userId === "number" || typeof userId === "string" ? userId : null;
  const name = typeof username === "string" && username ? `@${username}` : null;
  if (id === null) return <span className="text-muted">{name ?? "—"}</span>;
  return (
    <Link href={`/users/${id}`} className="text-accent hover:underline">
      {name ?? `id ${id}`}
    </Link>
  );
}

export default function ReferralsPage() {
  const { query, preset } = usePeriod();
  const granularity = granularityFor(preset);
  const [topBy, setTopBy] = useState<TopBy>("turnover");

  const { data: summary, isLoading: summaryLoading } =
    useSWR<T.ReferralsSummary>(api.urls.referralsSummary(), fetcher, {
      keepPreviousData: true,
    });
  const { data: ts, isLoading: tsLoading } = useSWR<{
    series: T.RefTimeseriesRow[];
  }>(api.urls.refTimeseries(query, granularity), fetcher, {
    keepPreviousData: true,
  });
  const { data: top, isLoading: topLoading } = useSWR<{
    referrers: T.ReferrerRow[];
  }>(api.urls.referralsTop(topBy), fetcher, { keepPreviousData: true });
  const { data: payouts, isLoading: payoutsLoading } =
    useSWR<T.PayoutsResponse>(api.urls.payouts(), fetcher, {
      keepPreviousData: true,
    });

  const kpiLoad = summaryLoading && !summary;

  const tsData = useMemo(() => ts?.series.map((r) => ({ ...r })) ?? [], [ts]);
  const topRows = top?.referrers ?? [];

  const money = (v: number) => fmtMoney(v);
  const xBucket = (b: string) => fmtBucket(b, granularity);

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-ink">Рефералка</h1>

      {/* KPI */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        <StatCard
          label="Рефереров"
          value={fmtNum(summary?.total_referrers)}
          loading={kpiLoad}
        />
        <StatCard
          label="Рефералов"
          value={fmtNum(summary?.total_referrals)}
          loading={kpiLoad}
        />
        <StatCard
          label="Платящих"
          value={fmtNum(summary?.total_paying)}
          loading={kpiLoad}
        />
        <StatCard
          label="Конверсия"
          value={fmtPct(summary?.conversion_pct)}
          loading={kpiLoad}
        />
        <StatCard
          label="К выплате"
          value={fmtMoney(summary?.payout_pending_total)}
          loading={kpiLoad}
        />
        <StatCard
          label="Заработано"
          value={fmtMoney(summary?.earned_total)}
          hint={`Оборот рефералов: ${fmtMoney(summary?.turnover_total)}`}
          loading={kpiLoad}
        />
      </div>

      {/* Динамика реферального дохода */}
      <ChartCard
        title="Динамика реферального дохода"
        subtitle="Начисления партнёрам (ref_income)"
        loading={tsLoading && !ts}
        empty={!!ts && ts.series.length === 0}
        csvRows={tsData}
        filename="referral-income"
        height={280}
      >
        <TimeSeries
          data={tsData}
          series={[{ key: "amount", name: "Доход", kind: "area" }]}
          height={280}
          brush
          xFormatter={xBucket}
          valueFormatter={money}
        />
      </ChartCard>

      {/* Топ рефереров */}
      <ChartCard
        title="Топ рефереров"
        subtitle="Партнёры с хотя бы одним рефералом"
        loading={topLoading && !top}
        empty={!!top && topRows.length === 0}
        csvRows={topRows.map((r) => ({ ...r }))}
        filename={`top-referrers-${topBy}`}
        height={280}
        actions={
          <Select
            aria-label="Сортировка"
            options={TOP_OPTIONS}
            value={topBy}
            onChange={(e) => setTopBy(e.target.value as TopBy)}
          />
        }
      >
        <Table>
          <THead>
            <TR>
              <TH>Партнёр</TH>
              <TH className="text-right">Оборот</TH>
              <TH className="text-right">Заработано</TH>
              <TH className="text-right">Рефералов</TH>
              <TH className="text-right">Платящих</TH>
              <TH className="text-right">Конверсия</TH>
              <TH className="text-right">К выплате</TH>
            </TR>
          </THead>
          <TBody>
            {topRows.map((r) => (
              <TR key={r.user_id}>
                <TD className="font-medium">
                  <UserLink userId={r.user_id} username={r.username} />
                </TD>
                <TD className="text-right text-ink-2">
                  {fmtMoney(r.turnover_total)}
                </TD>
                <TD className="text-right font-medium text-ink">
                  {fmtMoney(r.earned_total)}
                </TD>
                <TD className="text-right text-ink-2">{fmtNum(r.referrals)}</TD>
                <TD className="text-right text-ink-2">
                  {fmtNum(r.paying_referrals)}
                </TD>
                <TD className="text-right text-ink-2">
                  {fmtPct(r.conversion_pct)}
                </TD>
                <TD className="text-right text-ink-2">
                  {fmtMoney(r.payout_pending)}
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      </ChartCard>

      <div className="grid gap-3 lg:grid-cols-2">
        {/* Очередь выплат */}
        <Card>
          <CardHeader>
            <CardTitle>Очередь выплат</CardTitle>
            <span className="text-sm font-semibold text-ink">
              {fmtMoney(payouts?.pending_total)}
            </span>
          </CardHeader>
          {payoutsLoading && !payouts ? (
            <TableSkeleton rows={6} />
          ) : !payouts || payouts.queue.length === 0 ? (
            <div className="py-8 text-center text-sm text-muted">
              Очередь пуста
            </div>
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>Партнёр</TH>
                  <TH className="text-right">К выплате</TH>
                </TR>
              </THead>
              <TBody>
                {payouts.queue.map((q) => (
                  <TR key={q.user_id}>
                    <TD className="font-medium">
                      <UserLink userId={q.user_id} username={q.username} />
                    </TD>
                    <TD className="text-right font-medium text-ink">
                      {fmtMoney(q.payout_pending)}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </Card>

        {/* История выплат */}
        <Card>
          <CardHeader>
            <CardTitle>История выплат</CardTitle>
            <span className="text-xs text-muted">реквизиты замаскированы</span>
          </CardHeader>
          {payoutsLoading && !payouts ? (
            <TableSkeleton rows={6} />
          ) : !payouts || payouts.history.length === 0 ? (
            <div className="py-8 text-center text-sm text-muted">
              Выплат ещё не было
            </div>
          ) : (
            <div className="max-h-96 space-y-2 overflow-y-auto pr-1">
              {payouts.history.map((raw, i) => {
                const maybe: unknown = raw;
                if (typeof maybe !== "object" || maybe === null) {
                  return (
                    <div
                      key={i}
                      className="rounded-md border border-hairline bg-surface-2/40 p-2 text-xs text-muted"
                    >
                      {String(maybe)}
                    </div>
                  );
                }
                const entry = maybe as Record<string, unknown>;
                const dt = pickDate(entry);
                const amount = pickAmount(entry);
                const rest = restJson(entry);
                return (
                  <div
                    key={i}
                    className="rounded-md border border-hairline bg-surface-2/40 p-2"
                  >
                    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 text-sm">
                      <span className="min-w-0">
                        <UserLink
                          userId={entry.user_id}
                          username={entry.username}
                        />
                      </span>
                      <span className="flex items-baseline gap-2">
                        <span className="font-medium text-ink">
                          {amount !== null ? fmtMoney(amount, true) : "—"}
                        </span>
                        <span className="text-xs text-muted">
                          {fmtDateTime(dt)}
                        </span>
                      </span>
                    </div>
                    {rest ? (
                      <div className="mt-1 break-all font-mono text-[11px] leading-4 text-muted">
                        {rest}
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
