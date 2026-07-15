"use client";

/** Live-обзор (главная): realtime-счётчики, статус платёжных провайдеров и
 *  живая лента событий. Страница не использует глобальный фильтр периода —
 *  всё realtime-only. */

import useSWR from "swr";
import { api, fetcher } from "@/lib/api";
import type * as T from "@/lib/types";
import { useEventStream } from "@/lib/ws";
import { StatCard } from "@/components/live/stat-card";
import { EventTicker } from "@/components/live/ticker";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { TableSkeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import {
  fmtDateTime,
  fmtHoursApprox,
  fmtMoney,
  fmtNum,
} from "@/lib/format";
import { cn } from "@/lib/utils";

const PROVIDER_STATUS: Record<
  T.ProviderStatus["status"],
  { variant: "good" | "warning" | "critical"; label: string; dot: string }
> = {
  ok: { variant: "good", label: "OK", dot: "bg-good" },
  warning: { variant: "warning", label: "Тихо", dot: "bg-warning" },
  down: { variant: "critical", label: "Молчит", dot: "bg-critical" },
};

function ProvidersCard() {
  const { data, isLoading } = useSWR<T.ProvidersStatusResponse>(
    api.urls.providersStatus(),
    fetcher,
    { refreshInterval: 60_000, keepPreviousData: true },
  );
  const providers = data?.providers ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Платёжные провайдеры</CardTitle>
        <span className="text-xs text-muted">за 30 дней</span>
      </CardHeader>
      {isLoading && !data ? (
        <TableSkeleton rows={5} />
      ) : providers.length === 0 ? (
        <div className="py-8 text-center text-sm text-muted">
          Нет платёжных источников за последние 30 дней
        </div>
      ) : (
        <Table>
          <THead>
            <TR>
              <TH>Источник</TH>
              <TH>Статус</TH>
              <TH>Последний платёж</TH>
              <TH className="text-right">За 24ч</TH>
              <TH className="text-right">Тишина / порог</TH>
            </TR>
          </THead>
          <TBody>
            {providers.map((p) => {
              const st = PROVIDER_STATUS[p.status];
              return (
                <TR key={p.source}>
                  <TD>
                    <span className="flex items-center gap-2">
                      <span
                        className={cn("h-2 w-2 shrink-0 rounded-full", st.dot)}
                      />
                      <span className="font-medium text-ink">{p.source}</span>
                    </span>
                  </TD>
                  <TD>
                    <Badge variant={st.variant}>{st.label}</Badge>
                  </TD>
                  <TD className="whitespace-nowrap text-ink-2">
                    {fmtDateTime(p.last_payment_at)}
                  </TD>
                  <TD className="text-right tabular-nums text-ink-2">
                    {fmtNum(p.payments_24h)}
                  </TD>
                  <TD className="whitespace-nowrap text-right tabular-nums">
                    <span
                      className={cn(
                        p.status === "down"
                          ? "text-critical"
                          : p.status === "warning"
                            ? "text-warning"
                            : "text-ink-2",
                      )}
                    >
                      {fmtHoursApprox(p.silence_hours)}
                    </span>
                    <span className="text-muted">
                      {" "}
                      / {fmtHoursApprox(p.threshold_hours)}
                    </span>
                  </TD>
                </TR>
              );
            })}
          </TBody>
        </Table>
      )}
    </Card>
  );
}

export default function LiveOverviewPage() {
  const { data, isLoading } = useSWR<T.OverviewSummary>(
    api.urls.overviewSummary(),
    fetcher,
    { refreshInterval: 15_000, keepPreviousData: true },
  );
  const { events, connected } = useEventStream();

  const loading = isLoading && !data;
  const panelDown = data ? !data.remnawave_available : false;

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-ink">Live-обзор (главная)</h1>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard
          label="Онлайн"
          loading={loading}
          value={panelDown ? "—" : fmtNum(data?.online_users)}
          hint={panelDown ? "Remnawave недоступна" : "по данным панели"}
        />
        <StatCard
          label="Активные подписки"
          loading={loading}
          value={fmtNum(data?.active_subs)}
        />
        <StatCard
          label="Выручка сегодня"
          loading={loading}
          value={fmtMoney(data?.revenue_today)}
          spark={data?.sparklines.revenue}
          sparkFormatter={fmtMoney}
          hint="net-пополнения, 30 дней на графике"
        />
        <StatCard
          label="Выручка 7д / 30д"
          loading={loading}
          value={fmtMoney(data?.revenue_7d)}
          hint={`за 30 дней: ${fmtMoney(data?.revenue_30d)}`}
        />
        <StatCard
          label="Регистрации сегодня"
          loading={loading}
          value={fmtNum(data?.registrations_today)}
          spark={data?.sparklines.registrations}
          sparkFormatter={fmtNum}
        />
      </div>

      <div className="grid gap-3 lg:grid-cols-[1fr_380px]">
        <ProvidersCard />
        <Card>
          <CardHeader>
            <CardTitle>Живая лента</CardTitle>
          </CardHeader>
          <EventTicker events={events} connected={connected} />
        </Card>
      </div>
    </div>
  );
}
