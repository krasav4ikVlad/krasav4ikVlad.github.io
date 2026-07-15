"use client";

/** Алерты: статус системы уведомлений (Telegram, активные проверки, кулдаун,
 *  тестовое уведомление) и лента последних алертов из коллекции `alerts`. */

import { useState } from "react";
import useSWR from "swr";
import { AlertCircle, AlertTriangle, Info, Send } from "lucide-react";
import { api, fetcher } from "@/lib/api";
import type * as T from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton, TableSkeleton } from "@/components/ui/skeleton";
import { fmtDateTime, fmtHoursApprox } from "@/lib/format";
import { cn } from "@/lib/utils";

const CHECK_LABELS: Record<string, string> = {
  provider_silence: "Тишина провайдера",
  revenue_anomaly: "Аномалия выручки (2σ)",
  promo_abuse: "Абьюз промокодов",
  error_burst: "Всплеск ошибок",
};

const SEVERITY: Record<
  T.AlertRow["severity"],
  {
    Icon: typeof AlertTriangle;
    iconClass: string;
    badge: "critical" | "warning" | "default";
    label: string;
  }
> = {
  critical: {
    Icon: AlertTriangle,
    iconClass: "text-critical",
    badge: "critical",
    label: "Критично",
  },
  warning: {
    Icon: AlertCircle,
    iconClass: "text-warning",
    badge: "warning",
    label: "Внимание",
  },
  info: {
    Icon: Info,
    iconClass: "text-accent",
    badge: "default",
    label: "Инфо",
  },
};

/** Defensive rendering of arbitrary `details` values. */
function fmtDetail(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "да" : "нет";
  if (typeof value === "number") {
    return Number.isInteger(value) ? String(value) : value.toFixed(2);
  }
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function StatusCard() {
  const { data, isLoading } = useSWR<T.AlertsStatus>(
    api.urls.alertsStatus(),
    fetcher,
    { keepPreviousData: true },
  );

  const [sending, setSending] = useState(false);
  const [testResult, setTestResult] = useState<"sent" | "failed" | null>(null);

  async function sendTest() {
    setSending(true);
    setTestResult(null);
    try {
      const res = await api.alertsTest();
      setTestResult(res.sent ? "sent" : "failed");
    } catch {
      setTestResult("failed");
    } finally {
      setSending(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Статус уведомлений</CardTitle>
      </CardHeader>
      {isLoading && !data ? (
        <div className="space-y-3">
          <Skeleton className="h-6 w-40" />
          <Skeleton className="h-6 w-full" />
          <Skeleton className="h-9 w-48" />
        </div>
      ) : (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            {data?.telegram_configured ? (
              <Badge variant="good">Telegram подключён</Badge>
            ) : (
              <Badge variant="warning">TG_BOT_TOKEN не настроен</Badge>
            )}
            <span className="text-xs text-muted">
              Кулдаун: {fmtHoursApprox(data?.cooldown_hours)}
            </span>
          </div>

          <div>
            <div className="mb-1.5 text-xs text-muted">Активные проверки</div>
            <div className="flex flex-wrap gap-1.5">
              {(data?.checks ?? []).map((check) => (
                <Badge key={check} variant="outline">
                  {CHECK_LABELS[check] ?? check}
                </Badge>
              ))}
              {(data?.checks ?? []).length === 0 && (
                <span className="text-xs text-muted">нет проверок</span>
              )}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <Button variant="outline" size="sm" onClick={sendTest} disabled={sending}>
              <Send className="h-3.5 w-3.5" />
              {sending ? "Отправка…" : "Тестовое уведомление"}
            </Button>
            {testResult === "sent" && (
              <span className="text-xs text-good">отправлено</span>
            )}
            {testResult === "failed" && (
              <span className="text-xs text-critical">
                не отправлено (проверь конфиг)
              </span>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}

function AlertCard({ alert }: { alert: T.AlertRow }) {
  const sev = SEVERITY[alert.severity] ?? SEVERITY.info;
  const { Icon } = sev;
  const details = Object.entries(alert.details ?? {});

  return (
    <Card>
      <div className="flex items-start gap-3">
        <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", sev.iconClass)} />
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
            <span className="text-sm font-medium text-ink">{alert.title}</span>
            <span className="flex items-center gap-2">
              <Badge variant={sev.badge}>{sev.label}</Badge>
              <span className="whitespace-nowrap text-xs text-muted">
                {fmtDateTime(alert.created_at)}
              </span>
            </span>
          </div>
          {details.length > 0 && (
            <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-muted">
              {details.map(([k, v]) => (
                <span key={k} className="break-all">
                  {k}: {fmtDetail(v)}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}

function RecentAlerts() {
  const { data, isLoading } = useSWR<{ alerts: T.AlertRow[] }>(
    api.urls.alertsRecent(),
    fetcher,
    { refreshInterval: 30_000, keepPreviousData: true },
  );
  const alerts = data?.alerts ?? [];

  return (
    <div className="space-y-3">
      <h2 className="text-sm font-medium text-ink-2">Последние алерты</h2>
      {isLoading && !data ? (
        <TableSkeleton rows={5} />
      ) : alerts.length === 0 ? (
        <Card>
          <div className="py-8 text-center text-sm text-muted">
            Тишина — алертов нет
          </div>
        </Card>
      ) : (
        <div className="space-y-2">
          {alerts.map((a, i) => (
            <AlertCard key={`${a.key}-${a.created_at}-${i}`} alert={a} />
          ))}
        </div>
      )}
    </div>
  );
}

export default function AlertsPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-ink">Алерты</h1>

      <div className="grid gap-3 lg:grid-cols-3">
        <div className="lg:col-span-1">
          <StatusCard />
        </div>
        <div className="lg:col-span-2">
          <RecentAlerts />
        </div>
      </div>
    </div>
  );
}
