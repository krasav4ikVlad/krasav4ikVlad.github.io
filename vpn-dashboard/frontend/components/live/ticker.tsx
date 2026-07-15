"use client";

/** Live event ticker: new items slide in on top, no flicker on updates
 *  (stable keys, append-only list). */

import {
  AlertTriangle,
  ArrowRightLeft,
  Gift,
  ShoppingCart,
  Ticket,
  UserPlus,
  Wallet,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { fmtDateTime, fmtMoney } from "@/lib/format";
import type { LiveEvent } from "@/lib/types";
import { cn } from "@/lib/utils";

function eventView(e: LiveEvent): {
  icon: React.ReactNode;
  text: string;
  amount?: string;
} {
  const who = e.username ? `@${e.username}` : e.user_id ? `#${e.user_id}` : "аноним";
  switch (e.event) {
    case "topup":
      return {
        icon: <Wallet className="h-3.5 w-3.5 text-[var(--series-2)]" />,
        text: `${who} пополнил${e.source ? ` (${e.source})` : ""}${e.bonus ? ` +бонус ${fmtMoney(e.bonus)}` : ""}`,
        amount: fmtMoney(e.amount ?? 0),
      };
    case "ref_income":
      return {
        icon: <Gift className="h-3.5 w-3.5 text-[var(--series-5)]" />,
        text: `реферальное начисление ${who}`,
        amount: fmtMoney(e.amount ?? 0),
      };
    case "promo":
      return {
        icon: <Ticket className="h-3.5 w-3.5 text-[var(--series-4)]" />,
        text: `${who} активировал промокод${e.promo_code ? ` ${e.promo_code}` : ""}`,
        amount: fmtMoney(e.amount ?? 0),
      };
    case "purchase":
      return {
        icon: <ShoppingCart className="h-3.5 w-3.5 text-[var(--series-1)]" />,
        text: `${who} купил ${e.kind === "device" ? "устройство" : e.kind === "bypass" ? "ByPass-трафик" : e.kind === "gift" ? "подарок" : e.product ?? "подписку"}`,
        amount: fmtMoney(e.amount ?? 0),
      };
    case "registration":
      return {
        icon: <UserPlus className="h-3.5 w-3.5 text-[var(--series-7)]" />,
        text: `новая регистрация ${who}`,
      };
    case "segment":
      return {
        icon: <ArrowRightLeft className="h-3.5 w-3.5 text-muted" />,
        text: `${who} → сегмент ${e.segment}`,
      };
    case "alert":
      return {
        icon: <AlertTriangle className="h-3.5 w-3.5 text-critical" />,
        text: e.title ?? "алерт",
      };
    default:
      return { icon: null, text: JSON.stringify(e) };
  }
}

export function EventTicker({
  events,
  connected,
  maxHeight = 420,
}: {
  events: LiveEvent[];
  connected: boolean;
  maxHeight?: number;
}) {
  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <span
          className={cn(
            "h-2 w-2 rounded-full",
            connected ? "bg-good" : "bg-warning",
          )}
        />
        <span className="text-xs text-muted">
          {connected ? "live: change streams подключены" : "офлайн-режим: опрос раз в 20с"}
        </span>
      </div>
      <div className="space-y-1 overflow-y-auto pr-1" style={{ maxHeight }}>
        {events.length === 0 ? (
          <div className="py-8 text-center text-sm text-muted">
            Ждём события…
          </div>
        ) : (
          events.map((e) => {
            const view = eventView(e);
            return (
              <div
                key={`${e.id}:${e.ts}`}
                className={cn(
                  "flex items-center gap-2 rounded-md border border-hairline bg-surface px-2.5 py-1.5 text-xs",
                  !e.replay && "animate-ticker-in",
                )}
              >
                {view.icon}
                <span className="min-w-0 flex-1 truncate text-ink-2">
                  {view.text}
                </span>
                {view.amount ? (
                  <Badge variant="outline" className="tabular shrink-0">
                    {view.amount}
                  </Badge>
                ) : null}
                <span className="shrink-0 text-[10px] text-muted">
                  {fmtDateTime(e.ts)}
                </span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
