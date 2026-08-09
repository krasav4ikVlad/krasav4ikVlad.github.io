"use client";

/** Эксперименты: сравнение A/B-групп со статистической значимостью и
 *  «Возможности роста» — оценка денег в стандартных рычагах выручки. */

import { useState } from "react";
import useSWR from "swr";
import { TrendingUp } from "lucide-react";
import { api, fetcher } from "@/lib/api";
import type * as T from "@/lib/types";
import { fmtMoney, fmtNum, fmtPct } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { TimeSeries } from "@/components/charts/timeseries";
import { TableSkeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";

function SignificanceBadge({ vs }: { vs: T.AbGroup["vs_control"] }) {
  if (!vs) return <Badge variant="outline">контроль</Badge>;
  if (vs.significant) {
    const better = vs.conversion_diff_pp > 0;
    return (
      <Badge variant={better ? "good" : "critical"}>
        {better ? "+" : ""}
        {vs.conversion_diff_pp} п.п. · p={vs.p_value}
      </Badge>
    );
  }
  return (
    <Badge variant="default" title="Разница не отличима от случайности">
      незначимо · p={vs.p_value}
    </Badge>
  );
}

function AbSection() {
  const [experiment, setExperiment] = useState("ab_group");
  const { data, isLoading } = useSWR<T.AbResponse>(
    api.urls.experimentsAb(experiment),
    fetcher,
    { keepPreviousData: true },
  );
  const groups = data?.groups ?? [];

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>A/B-группы</CardTitle>
          <p className="mt-0.5 text-xs text-muted">
            Конверсия в платящего против контроля (двусторонний z-тест, порог
            p&nbsp;&lt;&nbsp;0.05). Контроль — самая большая группа.
          </p>
        </div>
        <Select
          value={experiment}
          onChange={(e) => setExperiment(e.target.value)}
          options={[
            { value: "ab_group", label: "ab_group" },
            { value: "trial_ab_group", label: "trial_ab_group" },
          ]}
        />
      </CardHeader>
      {isLoading && !data ? (
        <TableSkeleton rows={4} />
      ) : groups.length === 0 ? (
        <div className="py-10 text-center text-sm text-muted">
          В поле growth.{experiment} пока нет групп — раздайте юзерам группы в
          боте, и сравнение появится здесь
        </div>
      ) : (
        <>
          <Table>
            <THead>
              <TR>
                <TH>Группа</TH>
                <TH className="text-right">Юзеров</TH>
                <TH className="text-right">Платящих</TH>
                <TH className="text-right">Конверсия</TH>
                <TH className="text-right">ARPU</TH>
                <TH className="text-right">LTV платящего</TH>
                <TH className="text-right">Продлеваются</TH>
                <TH>Против контроля</TH>
              </TR>
            </THead>
            <TBody>
              {groups.map((g) => (
                <TR key={g.group}>
                  <TD className="font-medium text-ink">{g.group}</TD>
                  <TD className="text-right tabular">{fmtNum(g.users)}</TD>
                  <TD className="text-right tabular">{fmtNum(g.paying)}</TD>
                  <TD
                    className={cn(
                      "text-right tabular font-medium",
                      g.vs_control?.significant &&
                        (g.vs_control.conversion_diff_pp > 0
                          ? "text-[var(--delta-good)]"
                          : "text-critical"),
                    )}
                  >
                    {fmtPct(g.conversion_pct)}
                  </TD>
                  <TD className="text-right tabular">{fmtMoney(g.arpu)}</TD>
                  <TD className="text-right tabular">
                    {fmtMoney(g.avg_ltv_paying)}
                  </TD>
                  <TD className="text-right tabular">
                    {fmtPct(g.renewal_share_pct)}
                  </TD>
                  <TD>
                    <SignificanceBadge vs={g.vs_control} />
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
          {data && data.untagged_users > 0 ? (
            <p className="mt-2 text-xs text-muted">
              Вне эксперимента: {fmtNum(data.untagged_users)} юзеров без группы
            </p>
          ) : null}
        </>
      )}
    </Card>
  );
}

function OpportunitiesSection() {
  const { data, isLoading } = useSWR<T.OpportunitiesResponse>(
    api.urls.opportunities(),
    fetcher,
    { keepPreviousData: true },
  );
  const items = data?.opportunities ?? [];

  return (
    <div>
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <h2 className="text-sm font-medium text-ink-2">Возможности роста</h2>
        {data ? (
          <span className="text-xs text-muted">
            подписка ≈ {fmtMoney(data.monthly_sub_cost)}/мес · медианный чек{" "}
            {fmtMoney(data.median_check)}
          </span>
        ) : null}
      </div>
      {isLoading && !data ? (
        <TableSkeleton rows={5} />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {items.map((o) => (
            <Card key={o.key} className="flex flex-col">
              <div className="flex items-start justify-between gap-2">
                <span className="text-sm font-medium text-ink">{o.title}</span>
                <TrendingUp className="h-4 w-4 shrink-0 text-accent" />
              </div>
              <p className="mt-1 flex-1 text-xs text-ink-2">{o.description}</p>
              <div className="mt-3 flex items-baseline justify-between gap-2">
                <span className="text-xl font-semibold text-ink">
                  {fmtNum(o.users)}
                  <span className="ml-1 text-xs font-normal text-muted">
                    юзеров
                  </span>
                </span>
                <span
                  className="text-right text-sm font-medium text-[var(--delta-good)]"
                  title={`оценка: ${o.assumption}`}
                >
                  ≈ {fmtMoney(o.potential_rub)}
                </span>
              </div>
              <p className="mt-1 text-right text-[10px] text-muted">
                {o.assumption}
              </p>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

function RegEconomicsSection() {
  const { data, isLoading } = useSWR<T.RegEconomics>(
    api.urls.regEconomics(),
    fetcher,
    { keepPreviousData: true },
  );
  const [targetRub, setTargetRub] = useState("2000");

  const value30 = data?.horizons.d30.value_per_reg ?? 0;
  const target = Number(targetRub) || 0;
  // steady state: R рег/день × ценность-30д = дополнительных ₽/сутки
  const regsForTarget = value30 > 0 ? target / value30 : null;
  const regsOffset = data?.regs_per_day_to_offset_churn ?? null;
  const trendData = (data?.trend ?? []).map((t) => ({
    bucket: t.cohort,
    value: t.value_per_reg_30d,
  }));

  const horizonCards: { key: "d7" | "d30" | "d90"; label: string }[] = [
    { key: "d7", label: "за первые 7 дней" },
    { key: "d30", label: "за первые 30 дней" },
    { key: "d90", label: "за первые 90 дней" },
  ];

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Экономика регистраций</CardTitle>
          <p className="mt-0.5 text-xs text-muted">
            Сколько чистыми приносит одна регистрация (только юзеры, у которых
            окно уже закрылось) и сколько регистраций в день нужно под цель
          </p>
        </div>
      </CardHeader>
      {isLoading && !data ? (
        <TableSkeleton rows={4} />
      ) : !data ? null : (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-3">
            {horizonCards.map(({ key, label }) => {
              const h = data.horizons[key];
              return (
                <div
                  key={key}
                  className="rounded-card border border-hairline bg-surface-2/40 p-3"
                >
                  <div className="text-xs text-muted">{label}</div>
                  <div className="text-xl font-semibold text-ink">
                    {fmtMoney(h.value_per_reg, true)}
                  </div>
                  <div className="mt-0.5 text-xs text-muted">
                    платят {fmtPct(h.paying_share_pct)} · платящий приносит{" "}
                    {fmtMoney(h.value_per_paying)}
                  </div>
                </div>
              );
            })}
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <div>
              <div className="mb-1 text-xs text-muted">
                Ценность регистрации (30 дней) по когортам — последняя точка
                ещё «дозревает»
              </div>
              <TimeSeries
                data={trendData as unknown as Record<string, unknown>[]}
                series={[{ key: "value", name: "₽ с регистрации", kind: "area" }]}
                height={200}
                xFormatter={(b) => b}
                valueFormatter={(v) => fmtMoney(v)}
              />
            </div>
            <div className="space-y-3 self-center">
              <div>
                <label className="text-xs text-muted">
                  Хочу дополнительно, ₽/сутки
                </label>
                <Input
                  value={targetRub}
                  onChange={(e) =>
                    setTargetRub(e.target.value.replace(/[^\d]/g, ""))
                  }
                  inputMode="numeric"
                  className="mt-1 max-w-[200px]"
                />
              </div>
              <div className="text-sm text-ink-2">
                {regsForTarget !== null ? (
                  <>
                    Нужно{" "}
                    <span className="text-lg font-semibold text-ink">
                      ≈ {fmtNum(Math.ceil(regsForTarget))} рег/день
                    </span>{" "}
                    <span className="text-muted">
                      (одна регистрация ≈ {fmtMoney(value30)} за первые 30
                      дней ≈ {fmtMoney(value30)} к суточной выручке в
                      устоявшемся режиме)
                    </span>
                  </>
                ) : (
                  "Недостаточно данных для оценки"
                )}
              </div>
              <div className="space-y-1 text-xs text-muted">
                <p>
                  Сейчас: {fmtNum(data.regs_per_day_14d)} рег/день ≈{" "}
                  {fmtMoney(data.regs_per_day_14d * value30)}/сутки (
                  {fmtMoney(data.current_monthly_value)}/мес)
                </p>
                <p>
                  Отток: ≈ {fmtMoney(data.churn_lost_monthly_rub / 30)}/сутки (
                  {fmtNum(data.churned_30d)} юзеров за 30 дней) — чтобы просто
                  стоять на месте, нужно{" "}
                  <span className="font-medium text-ink-2">
                    {regsOffset !== null ? `≈ ${regsOffset} рег/день` : "—"}
                  </span>
                </p>
              </div>
            </div>
          </div>
        </div>
      )}
    </Card>
  );
}

export default function ExperimentsPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-ink">Эксперименты</h1>
      <RegEconomicsSection />
      <OpportunitiesSection />
      <AbSection />
    </div>
  );
}
