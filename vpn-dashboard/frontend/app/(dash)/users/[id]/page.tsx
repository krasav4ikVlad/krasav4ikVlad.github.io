"use client";

/** Карточка пользователя: профиль, KPI, транзакции, сегменты, рефералы, логи. */

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import useSWR from "swr";
import { ArrowLeft } from "lucide-react";
import { api, fetcher } from "@/lib/api";
import type * as T from "@/lib/types";
import { fmtDate, fmtDateTime, fmtMoney, fmtNum } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { StatSkeleton, TableSkeleton } from "@/components/ui/skeleton";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { StatCard } from "@/components/live/stat-card";

const KIND_LABELS: Record<string, string> = {
  topup: "Пополнение",
  ref_income: "Реф. доход",
  promo: "Промокод",
  bonus: "Бонус",
  purchase: "Покупка",
  gift: "Подарок",
  unknown: "Прочее",
  renewal: "Продление",
  device: "Устройство",
  bypass: "Обход",
  other: "Другое",
};

function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind;
}

function segmentVariant(segment: string | null): BadgeProps["variant"] {
  if (!segment) return "outline";
  if (segment.includes("expired")) return "critical";
  if (segment.includes("expiring")) return "warning";
  if (segment.startsWith("active")) return "good";
  return "default";
}

/** Defensive access to users_flat fields beyond the typed subset. */
function numField(u: Record<string, unknown>, key: string): number | null {
  const v = u[key];
  return typeof v === "number" && !Number.isNaN(v) ? v : null;
}
function strField(u: Record<string, unknown>, key: string): string | null {
  const v = u[key];
  return typeof v === "string" ? v : null;
}

function Meta({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-0.5 text-sm font-medium text-ink">{value}</div>
    </div>
  );
}

function EmptyNote({ text }: { text: string }) {
  return <div className="py-8 text-center text-sm text-muted">{text}</div>;
}

export default function UserCardPage() {
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const id = params?.id;

  const { data, error, isLoading } = useSWR<T.UserCard>(
    id ? api.urls.userCard(id) : null,
    fetcher,
    { keepPreviousData: true },
  );

  if (error) {
    return (
      <div className="flex min-h-[50vh] flex-col items-center justify-center gap-3">
        <div className="text-sm text-muted">Пользователь не найден</div>
        <Button variant="outline" size="sm" onClick={() => router.back()}>
          <ArrowLeft className="h-4 w-4" />
          Назад
        </Button>
      </div>
    );
  }

  const loading = isLoading && !data;
  const user = data?.user;
  const txs = data?.transactions ?? [];
  const history = [...(user?.segment_history ?? [])].reverse();
  const referrals = data?.referrals ?? [];
  const logs = data?.recent_logs ?? [];

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-ink">Карточка пользователя</h1>

      {/* Шапка профиля */}
      {loading || !user ? (
        <Card>
          <StatSkeleton />
        </Card>
      ) : (
        <Card>
          <div className="flex items-start gap-3">
            <Button
              variant="ghost"
              size="icon"
              aria-label="Назад"
              onClick={() => router.back()}
              className="shrink-0"
            >
              <ArrowLeft className="h-4 w-4" />
            </Button>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="truncate text-xl font-semibold text-ink">
                  {user.username ? `@${user.username}` : `#${user._id}`}
                </span>
                <span className="text-sm text-muted">#{user._id}</span>
                {user.segment ? (
                  <Badge variant={segmentVariant(user.segment)}>
                    {user.segment}
                  </Badge>
                ) : null}
              </div>
              <div className="mt-3 grid gap-x-6 gap-y-3 grid-cols-2 sm:grid-cols-3 lg:grid-cols-5">
                <Meta label="Регистрация" value={fmtDate(user.joined_at)} />
                <Meta
                  label="До окончания"
                  value={
                    user.days_to_expire === null
                      ? "—"
                      : `${fmtNum(user.days_to_expire)} дн`
                  }
                />
                <Meta label="Баланс" value={fmtMoney(user.balance, true)} />
                <Meta label="Клиент" value={user.preferred_client ?? "—"} />
                <Meta
                  label="Доп. устройств"
                  value={fmtNum(user.extra_devices_active)}
                />
              </div>
            </div>
          </div>
        </Card>
      )}

      {/* KPI */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Пополнения"
          value={fmtMoney(user?.topup_total)}
          hint={
            user
              ? `Платежей: ${fmtNum(numField(user, "topup_count"))} · Бонусов: ${fmtMoney(numField(user, "bonus_total"))}`
              : undefined
          }
          loading={loading}
        />
        <StatCard
          label="Списания"
          value={fmtMoney(user?.spend_total)}
          hint={
            user
              ? `Устройства: ${fmtMoney(numField(user, "device_spend_total"))}`
              : undefined
          }
          loading={loading}
        />
        <StatCard
          label="Продления"
          value={fmtNum(user?.renewals_count)}
          hint={
            user
              ? `Последнее: ${fmtDate(strField(user, "last_renewal_at"))}`
              : undefined
          }
          loading={loading}
        />
        <StatCard
          label="Обход блокировок"
          value={user ? fmtMoney(numField(user, "bypass_total")) : "—"}
          hint={
            user
              ? `Покупок: ${fmtNum(numField(user, "bypass_count"))}`
              : undefined
          }
          loading={loading}
        />
      </div>

      {/* Вкладки */}
      <Card>
        <Tabs defaultValue="tx">
          <TabsList>
            <TabsTrigger value="tx">Транзакции</TabsTrigger>
            <TabsTrigger value="segments">Сегменты</TabsTrigger>
            <TabsTrigger value="refs">Рефералы</TabsTrigger>
            <TabsTrigger value="logs">Логи</TabsTrigger>
          </TabsList>

          {/* Транзакции */}
          <TabsContent value="tx">
            {loading ? (
              <TableSkeleton rows={8} />
            ) : txs.length === 0 ? (
              <EmptyNote text="Нет транзакций" />
            ) : (
              <Table>
                <THead>
                  <TR>
                    <TH>Дата</TH>
                    <TH></TH>
                    <TH>Тип</TH>
                    <TH className="text-right">Сумма</TH>
                    <TH>Источник</TH>
                    <TH>Промокод</TH>
                    <TH>Описание</TH>
                  </TR>
                </THead>
                <TBody>
                  {txs.map((tx, i) => (
                    <TR key={i}>
                      <TD className="whitespace-nowrap text-ink-2">
                        {fmtDateTime(tx.dt)}
                      </TD>
                      <TD
                        className={cn(
                          "text-center",
                          tx.direction === "credit"
                            ? "text-[var(--delta-good)]"
                            : "text-muted",
                        )}
                      >
                        {tx.direction === "credit" ? "↑" : "↓"}
                      </TD>
                      <TD className="whitespace-nowrap text-ink-2">
                        {kindLabel(tx.kind)}
                      </TD>
                      <TD
                        className={cn(
                          "whitespace-nowrap text-right font-medium",
                          tx.direction === "credit"
                            ? "text-[var(--delta-good)]"
                            : "text-ink-2",
                        )}
                      >
                        {tx.direction === "credit" ? "+" : "−"}
                        {fmtMoney(tx.amount, true)}
                      </TD>
                      <TD className="text-muted">{tx.source ?? "—"}</TD>
                      <TD className="text-muted">{tx.promo_code ?? "—"}</TD>
                      <TD className="max-w-[18rem]">
                        <span
                          className="block truncate text-muted"
                          title={tx.desc ?? undefined}
                        >
                          {tx.desc ?? "—"}
                        </span>
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </TabsContent>

          {/* Сегменты */}
          <TabsContent value="segments">
            {loading ? (
              <TableSkeleton rows={6} />
            ) : history.length === 0 ? (
              <EmptyNote text="Нет истории сегментов" />
            ) : (
              <ol className="ml-2 space-y-4 border-l border-hairline pl-4">
                {history.map((h, i) => (
                  <li key={i} className="relative">
                    <span
                      aria-hidden
                      className={cn(
                        "absolute -left-[21px] top-1.5 h-2 w-2 rounded-full",
                        i === 0 ? "bg-accent" : "bg-surface-2",
                      )}
                    />
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant={segmentVariant(h.segment)}>
                        {h.segment}
                      </Badge>
                      <span className="text-xs text-muted">
                        {fmtDateTime(h.dt)}
                      </span>
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </TabsContent>

          {/* Рефералы */}
          <TabsContent value="refs">
            {loading ? (
              <TableSkeleton rows={6} />
            ) : referrals.length === 0 ? (
              <EmptyNote text="Нет рефералов" />
            ) : (
              <Table>
                <THead>
                  <TR>
                    <TH>Пользователь</TH>
                    <TH className="text-right">Пополнения</TH>
                    <TH className="text-right">Регистрация</TH>
                  </TR>
                </THead>
                <TBody>
                  {referrals.map((r) => (
                    <TR key={r.user_id}>
                      <TD>
                        <Link
                          href={`/users/${r.user_id}`}
                          className="text-accent hover:underline"
                        >
                          {r.username ? `@${r.username}` : `#${r.user_id}`}
                        </Link>
                      </TD>
                      <TD className="text-right text-ink-2">
                        {fmtMoney(r.topup_total)}
                      </TD>
                      <TD className="whitespace-nowrap text-right text-muted">
                        {fmtDate(r.joined_at)}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </TabsContent>

          {/* Логи */}
          <TabsContent value="logs">
            {loading ? (
              <TableSkeleton rows={8} />
            ) : logs.length === 0 ? (
              <EmptyNote text="Нет логов" />
            ) : (
              <ul className="space-y-1.5">
                {logs.map((l, i) => (
                  <li key={i} className="flex gap-3 font-mono text-xs">
                    <span className="shrink-0 whitespace-nowrap text-muted">
                      {fmtDateTime(l.dt)}
                    </span>
                    <span className="min-w-0 break-words text-ink-2">
                      {l.text}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </TabsContent>
        </Tabs>
      </Card>
    </div>
  );
}
