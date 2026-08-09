"use client";

/** Live-обзор (главная): realtime-счётчики, статус платёжных провайдеров и
 *  живая лента событий. Страница не использует глобальный фильтр периода —
 *  всё realtime-only. */

import useSWR from "swr";
import Link from "next/link";
import { api, fetcher } from "@/lib/api";
import type * as T from "@/lib/types";
import { useEventStream } from "@/lib/ws";
import { StatCard } from "@/components/live/stat-card";
import { EventTicker } from "@/components/live/ticker";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ChartSkeleton, TableSkeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { TimeSeries } from "@/components/charts/timeseries";
import { StackedBars } from "@/components/charts/stacked-bars";
import {
  fmtDate,
  fmtDateTime,
  fmtHoursApprox,
  fmtMoney,
  fmtNum,
  fmtPct,
} from "@/lib/format";
import { cn } from "@/lib/utils";

const PACE_STATUS: Record<
  T.PaceResponse["status"],
  { variant: "good" | "warning" | "critical" | "default"; label: string }
> = {
  ahead: { variant: "good", label: "Опережаем" },
  on_track: { variant: "good", label: "В норме" },
  behind: { variant: "critical", label: "Отстаём" },
  no_data: { variant: "default", label: "Мало данных" },
};

function PaceCard() {
  const { data, isLoading } = useSWR<T.PaceResponse>(api.urls.pace(), fetcher, {
    refreshInterval: 60_000,
    keepPreviousData: true,
  });
  const st = data ? PACE_STATUS[data.status] : null;
  const factors = (data?.factors ?? []).filter((f) => f.delta_pct !== null);

  return (
    <Card>
      <CardHeader>
        <div className="min-w-0">
          <CardTitle>Темп дня</CardTitle>
          <p className="mt-0.5 text-xs text-muted">
            {data?.verdict ?? "Сравнение с медианой последних недель, часы UTC"}
          </p>
        </div>
        {st ? <Badge variant={st.variant}>{st.label}</Badge> : null}
      </CardHeader>
      {isLoading && !data ? (
        <ChartSkeleton height={200} />
      ) : !data || data.status === "no_data" ? (
        <div className="py-8 text-center text-sm text-muted">
          Нужен хотя бы день истории — загляните завтра
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[220px_1fr_300px]">
          <div className="space-y-3">
            <div>
              <div className="text-xs text-muted">Сейчас</div>
              <div className="flex items-baseline gap-2">
                <span className="text-2xl font-semibold text-ink">
                  {fmtMoney(data.today_so_far)}
                </span>
                {data.deviation_pct !== null ? (
                  <span
                    className={cn(
                      "text-xs font-medium",
                      data.deviation_pct >= 0
                        ? "text-[var(--delta-good)]"
                        : "text-critical",
                    )}
                  >
                    {fmtPct(data.deviation_pct, true)}
                  </span>
                ) : null}
              </div>
              <div className="text-xs text-muted">
                обычно к этому часу: {fmtMoney(data.expected_so_far)}
              </div>
            </div>
            <div>
              <div className="text-xs text-muted">Прогноз на день</div>
              <div className="text-lg font-semibold text-ink">
                {fmtMoney(data.projected_today)}
              </div>
              <div className="text-xs text-muted">
                обычный день: {fmtMoney(data.expected_full_day)}
              </div>
            </div>
          </div>
          <div className="min-w-0">
            <TimeSeries
              data={data.series as unknown as Record<string, unknown>[]}
              series={[
                { key: "expected", name: "Обычно", kind: "area" },
                { key: "today", name: "Сегодня" },
              ]}
              height={200}
              xKey="hour"
              xFormatter={(h) => `${h}:00`}
              valueFormatter={(v) => fmtMoney(v)}
            />
          </div>
          <div className="space-y-1 self-center">
            {factors.slice(0, 6).map((f) => (
              <div
                key={f.key}
                className="flex items-center justify-between gap-2 text-xs"
              >
                <span className="truncate text-ink-2">{f.label}</span>
                <span className="shrink-0 tabular text-muted">
                  {f.unit === "₽"
                    ? `${fmtMoney(f.today)} / ${fmtMoney(f.expected)}`
                    : `${fmtNum(f.today)} / ${fmtNum(f.expected)}`}
                </span>
                <span
                  className={cn(
                    "w-14 shrink-0 text-right tabular font-medium",
                    (f.delta_pct ?? 0) >= 0
                      ? "text-[var(--delta-good)]"
                      : "text-critical",
                  )}
                >
                  {fmtPct(f.delta_pct, true)}
                </span>
              </div>
            ))}
            <p className="pt-1 text-[10px] text-muted">
              сегодня / обычно к этому часу · медиана за {data.baseline_days}{" "}
              дн
            </p>
          </div>
        </div>
      )}
    </Card>
  );
}

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
              <TH className="text-right">Комиссия 30д</TH>
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
                    {p.failed_24h ? (
                      <span className="ml-1 text-critical" title="неуспешных за 24ч">
                        ({p.failed_24h}✕)
                      </span>
                    ) : null}
                  </TD>
                  <TD className="text-right tabular-nums text-ink-2">
                    {p.commission_30d !== null && p.commission_30d !== undefined
                      ? fmtMoney(p.commission_30d)
                      : "—"}
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

const CHURN_REASONS: Record<string, string> = {
  too_expensive: "дорого",
  too_cheap_no_trust: "дёшево — не доверяю",
  not_needed_now: "сейчас не нужно",
  problems: "были проблемы",
  found_other: "нашёл другой сервис",
  hard_to_use: "сложно пользоваться",
  forgot: "забыл продлить",
};

function RenewalOutlookCard() {
  const { data, isLoading } = useSWR<T.RenewalOutlook>(
    api.urls.renewalOutlook(),
    fetcher,
    { refreshInterval: 120_000, keepPreviousData: true },
  );
  const today = data?.today;
  const needing = (today?.users ?? []).filter((u) => u.expected_topup > 0);
  const churnData = (data?.history ?? []).map((h) => ({
    day: h.day,
    gone: Math.max(0, h.churned - h.returned),
    returned: h.returned,
  }));
  const lost14 = (data?.history ?? []).reduce((a, h) => a + h.churned, 0);
  const lostRub14 = (data?.history ?? []).reduce(
    (a, h) => a + h.lost_monthly_rub,
    0,
  );

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Продления сегодня</CardTitle>
          <p className="mt-0.5 text-xs text-muted">
            У кого сегодня кончается подписка, сколько денег они должны
            принести (мин. пополнение {fmtMoney(data?.min_topup ?? 75)}) и
            сколько юзеров мы теряем
          </p>
        </div>
      </CardHeader>
      {isLoading && !data ? (
        <ChartSkeleton height={220} />
      ) : !data ? null : (
        <div className="grid gap-4 lg:grid-cols-[230px_1fr_1fr]">
          <div className="space-y-3">
            <div>
              <div className="text-xs text-muted">Истекает сегодня</div>
              <div className="text-2xl font-semibold text-ink">
                {fmtNum(today?.expiring)}
              </div>
              <div className="text-xs text-muted">
                с баланса продлятся: {fmtNum(today?.can_renew_from_balance)} ·
                нужно пополнить: {fmtNum(today?.need_topup)}
              </div>
            </div>
            <div>
              <div className="text-xs text-muted">Ожидаемые пополнения</div>
              <div className="text-lg font-semibold text-[var(--delta-good)]">
                ≈ {fmtMoney(today?.potential_topup_rub)}
              </div>
              <div className="text-xs text-muted">
                весь объём когорты: {fmtMoney(today?.potential_monthly_rub)}
                /мес
              </div>
            </div>
            <div>
              <div className="text-xs text-muted">Ушло за 14 дней</div>
              <div className="text-lg font-semibold text-critical">
                {fmtNum(lost14)}{" "}
                <span className="text-xs font-normal text-muted">
                  ≈ {fmtMoney(lostRub14)}/мес
                </span>
              </div>
              {(data.churn_reasons ?? []).length > 0 ? (
                <div className="mt-1 space-y-0.5 text-[11px] text-muted">
                  <div className="font-medium text-ink-2">
                    Почему уходят (опрос, 30д):
                  </div>
                  {data.churn_reasons.slice(0, 4).map((r) => (
                    <div key={r.reason} className="flex justify-between gap-2">
                      <span>{CHURN_REASONS[r.reason] ?? r.reason}</span>
                      <span className="tabular">{fmtNum(r.count)}</span>
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          </div>
          <div className="min-w-0">
            <div className="mb-1 text-xs text-muted">
              Отвал по дням (ушли в expired / вернулись позже)
            </div>
            <StackedBars
              data={churnData as unknown as Record<string, unknown>[]}
              keys={["gone", "returned"]}
              names={{ gone: "Ушли", returned: "Вернулись" }}
              height={190}
              xKey="day"
              xFormatter={(d) => fmtDate(d)}
              valueFormatter={(v) => fmtNum(v)}
            />
          </div>
          <div className="min-w-0">
            <div className="mb-1 text-xs text-muted">
              Кому нужно пополнить (топ по сумме)
            </div>
            {needing.length === 0 ? (
              <div className="py-8 text-center text-sm text-muted">
                Сегодня всем хватает баланса 🎉
              </div>
            ) : (
              <div className="max-h-[210px] overflow-y-auto pr-1">
                <Table>
                  <THead>
                    <TR>
                      <TH>Юзер</TH>
                      <TH className="text-right">Баланс</TH>
                      <TH className="text-right">Цена</TH>
                      <TH className="text-right">Ждём</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {needing.slice(0, 10).map((u) => (
                      <TR key={u.user_id}>
                        <TD>
                          <Link
                            href={`/users/${u.user_id}`}
                            className="text-accent hover:underline"
                          >
                            {u.username ? `@${u.username}` : `#${u.user_id}`}
                          </Link>
                        </TD>
                        <TD className="text-right tabular text-ink-2">
                          {fmtMoney(u.balance)}
                        </TD>
                        <TD className="text-right tabular text-ink-2">
                          {fmtMoney(u.personal_cost)}
                        </TD>
                        <TD className="text-right tabular font-medium text-ink">
                          {fmtMoney(u.expected_topup)}
                        </TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </div>
            )}
          </div>
        </div>
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

      <PaceCard />
      <RenewalOutlookCard />

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
