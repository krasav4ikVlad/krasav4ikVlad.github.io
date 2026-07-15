"use client";

/** Промокоды и кампании: таблица промокодов, подозрения на абьюз,
 *  эффективность кампаний и рассылок. */

import Link from "next/link";
import useSWR from "swr";
import { api, fetcher } from "@/lib/api";
import type * as T from "@/lib/types";
import { usePeriod } from "@/lib/period";
import { fmtDateTime, fmtMoney, fmtNum, fmtPct } from "@/lib/format";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { TableSkeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { ChartCard } from "@/components/charts/container";

function UserLink({ id, username }: { id: number; username: string | null }) {
  return (
    <Link href={`/users/${id}`} className="text-accent hover:underline">
      {username ? `@${username}` : `id ${id}`}
    </Link>
  );
}

function MaxByUserBadge({ value }: { value: number }) {
  if (value >= 3) return <Badge variant="critical">{fmtNum(value)}</Badge>;
  if (value === 2) return <Badge variant="warning">{fmtNum(value)}</Badge>;
  return <span className="text-ink-2">{fmtNum(value)}</span>;
}

function Uplift({ value }: { value: number | null }) {
  if (value === null || Number.isNaN(value)) {
    return <span className="text-muted">—</span>;
  }
  if (value > 0) {
    return (
      <span style={{ color: "var(--delta-good)" }}>{fmtPct(value, true)}</span>
    );
  }
  if (value < 0) {
    return <span className="text-critical">{fmtPct(value, true)}</span>;
  }
  return <span className="text-ink-2">{fmtPct(value, true)}</span>;
}

function PromosTableSection() {
  const { query } = usePeriod();
  const { data, isLoading } = useSWR<{ promos: T.PromoRow[] }>(
    api.urls.promosTable(query),
    fetcher,
    { keepPreviousData: true },
  );
  const promos = data?.promos ?? [];
  return (
    <ChartCard
      title="Промокоды"
      subtitle="Активации за выбранный период"
      loading={isLoading && !data}
      empty={!!data && promos.length === 0}
      csvRows={promos as unknown as Record<string, unknown>[]}
      filename="promos"
      height={200}
    >
      <Table>
        <THead>
          <TR>
            <TH>Код</TH>
            <TH className="text-right">Активации</TH>
            <TH className="text-right">Уник. юзеров</TH>
            <TH className="text-right">Повторные</TH>
            <TH className="text-right">Макс. на юзера</TH>
            <TH className="text-right">Сумма</TH>
            <TH className="text-right">Последняя</TH>
          </TR>
        </THead>
        <TBody>
          {promos.map((p) => (
            <TR key={p.code}>
              <TD className="font-mono text-xs text-ink">{p.code}</TD>
              <TD className="text-right">{fmtNum(p.activations)}</TD>
              <TD className="text-right">{fmtNum(p.unique_users)}</TD>
              <TD className="text-right">{fmtNum(p.repeat_activations)}</TD>
              <TD className="text-right">
                <MaxByUserBadge value={p.max_by_one_user} />
              </TD>
              <TD className="text-right">{fmtMoney(p.amount_total)}</TD>
              <TD className="text-right text-muted">
                {fmtDateTime(p.last_used_at)}
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>
    </ChartCard>
  );
}

function AbuseSection() {
  const { query } = usePeriod();
  const { data, isLoading } = useSWR<{ cases: T.AbuseCase[] }>(
    api.urls.promosAbuse(query),
    fetcher,
    { keepPreviousData: true },
  );
  const cases = data?.cases ?? [];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Подозрение на абьюз</CardTitle>
      </CardHeader>
      {isLoading && !data ? (
        <TableSkeleton rows={4} />
      ) : cases.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted">Абьюза не видно 🎉</p>
      ) : (
        <Table>
          <THead>
            <TR>
              <TH>Пользователь</TH>
              <TH>Код</TH>
              <TH className="text-right">Активаций</TH>
              <TH className="text-right">Сумма</TH>
              <TH className="text-right">Первая</TH>
              <TH className="text-right">Последняя</TH>
            </TR>
          </THead>
          <TBody>
            {cases.map((c) => (
              <TR key={`${c.user_id}-${c.code}`}>
                <TD>
                  <UserLink id={c.user_id} username={c.username} />
                </TD>
                <TD className="font-mono text-xs text-ink">{c.code}</TD>
                <TD className="text-right">
                  <Badge variant="critical">{fmtNum(c.count)}</Badge>
                </TD>
                <TD className="text-right">{fmtMoney(c.amount_total)}</TD>
                <TD className="text-right text-muted">{fmtDateTime(c.first_at)}</TD>
                <TD className="text-right text-muted">{fmtDateTime(c.last_at)}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}
    </Card>
  );
}

function CampaignsSection() {
  const { data, isLoading } = useSWR<{ campaigns: T.CampaignRow[] }>(
    api.urls.campaigns(),
    fetcher,
    { keepPreviousData: true },
  );
  const campaigns = data?.campaigns ?? [];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Кампании</CardTitle>
      </CardHeader>
      {isLoading && !data ? (
        <TableSkeleton rows={4} />
      ) : campaigns.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted">
          Данных по кампаниям нет
        </p>
      ) : (
        <Table>
          <THead>
            <TR>
              <TH>Кампания</TH>
              <TH className="text-right">Юзеров</TH>
              <TH className="text-right">Сконверт.</TH>
              <TH className="text-right">Конверсия</TH>
              <TH className="text-right">Сумма</TH>
            </TR>
          </THead>
          <TBody>
            {campaigns.map((c) => (
              <TR key={c.campaign}>
                <TD className="text-ink">{c.campaign}</TD>
                <TD className="text-right">{fmtNum(c.users)}</TD>
                <TD className="text-right">{fmtNum(c.converted)}</TD>
                <TD className="text-right">{fmtPct(c.conversion_pct)}</TD>
                <TD className="text-right">{fmtMoney(c.converted_amount)}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}
    </Card>
  );
}

function BroadcastsSection() {
  const { data, isLoading } = useSWR<T.BroadcastImpact>(
    api.urls.broadcastImpact(),
    fetcher,
    { keepPreviousData: true },
  );
  return (
    <Card>
      <CardHeader>
        <CardTitle>Эффект рассылок</CardTitle>
      </CardHeader>
      {isLoading && !data ? (
        <TableSkeleton rows={4} />
      ) : !data?.available ? (
        <p className="py-6 text-center text-sm text-muted">
          Коллекция broadcasts не найдена — подключите лог рассылок
        </p>
      ) : data.broadcasts.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted">Рассылок пока нет</p>
      ) : (
        <Table>
          <THead>
            <TR>
              <TH>Рассылка</TH>
              <TH className="text-right">Когда</TH>
              <TH className="text-right">Платежи</TH>
              <TH className="text-right">Выручка</TH>
              <TH className="text-right">Аплифт</TH>
            </TR>
          </THead>
          <TBody>
            {data.broadcasts.map((b, i) => (
              <TR key={`${b.name}-${b.at}-${i}`}>
                <TD className="text-ink">{b.name}</TD>
                <TD className="text-right text-muted">{fmtDateTime(b.at)}</TD>
                <TD className="text-right whitespace-nowrap">
                  {fmtNum(b.topups_before)}{" "}
                  <span className="text-muted">→</span> {fmtNum(b.topups_after)}
                </TD>
                <TD className="text-right whitespace-nowrap">
                  {fmtMoney(b.revenue_before)}{" "}
                  <span className="text-muted">→</span>{" "}
                  {fmtMoney(b.revenue_after)}
                </TD>
                <TD className="text-right">
                  <Uplift value={b.uplift_pct} />
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}
    </Card>
  );
}

export default function PromosPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-ink">Промокоды и кампании</h1>
      <PromosTableSection />
      <AbuseSection />
      <div className="grid gap-3 lg:grid-cols-2">
        <CampaignsSection />
        <BroadcastsSection />
      </div>
    </div>
  );
}
