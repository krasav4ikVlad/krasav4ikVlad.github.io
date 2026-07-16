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
import { Select } from "@/components/ui/select";
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

export default function ExperimentsPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-ink">Эксперименты</h1>
      <OpportunitiesSection />
      <AbSection />
    </div>
  );
}
