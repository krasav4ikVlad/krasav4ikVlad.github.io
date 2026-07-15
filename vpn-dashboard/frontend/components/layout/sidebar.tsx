"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import {
  Activity,
  Bell,
  FlaskConical,
  Gift,
  LayoutDashboard,
  Package,
  Server,
  Ticket,
  Users,
  Wallet,
} from "lucide-react";
import { cn } from "@/lib/utils";

export const NAV = [
  { href: "/", label: "Live-обзор", icon: LayoutDashboard },
  { href: "/revenue", label: "Выручка", icon: Wallet },
  { href: "/users", label: "Пользователи", icon: Users },
  { href: "/referrals", label: "Рефералка", icon: Gift },
  { href: "/promos", label: "Промокоды", icon: Ticket },
  { href: "/product", label: "Продукт", icon: Package },
  { href: "/experiments", label: "Эксперименты", icon: FlaskConical },
  { href: "/infra", label: "Инфраструктура", icon: Server },
  { href: "/alerts", label: "Алерты", icon: Bell },
] as const;

export function Sidebar() {
  const pathname = usePathname();
  const params = useSearchParams();
  const qs = params.toString();
  const suffix = qs ? `?${qs}` : "";
  return (
    <aside className="sticky top-0 hidden h-screen w-52 shrink-0 flex-col border-r border-hairline bg-surface md:flex">
      <div className="flex items-center gap-2 px-4 py-4">
        <Activity className="h-5 w-5 text-accent" />
        <span className="text-sm font-semibold text-ink">VPN Analytics</span>
      </div>
      <nav className="flex-1 space-y-0.5 px-2">
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = pathname === href;
          return (
            <Link
              key={href}
              href={`${href}${suffix}`}
              className={cn(
                "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors",
                active
                  ? "bg-surface-2 font-medium text-ink"
                  : "text-ink-2 hover:bg-surface-2 hover:text-ink",
              )}
            >
              <Icon className="h-4 w-4" />
              {label}
            </Link>
          );
        })}
      </nav>
      <div className="px-4 py-3 text-[10px] text-muted">внутренний инструмент</div>
    </aside>
  );
}

/** Bottom tab bar for phones. */
export function MobileNav() {
  const pathname = usePathname();
  const params = useSearchParams();
  const qs = params.toString();
  const suffix = qs ? `?${qs}` : "";
  return (
    <nav className="fixed inset-x-0 bottom-0 z-40 flex justify-around border-t border-hairline bg-surface/95 py-1.5 backdrop-blur md:hidden">
      {NAV.map(({ href, label, icon: Icon }) => {
        const active = pathname === href;
        return (
          <Link
            key={href}
            href={`${href}${suffix}`}
            aria-label={label}
            className={cn(
              "rounded-md p-2",
              active ? "text-accent" : "text-muted hover:text-ink",
            )}
          >
            <Icon className="h-5 w-5" />
          </Link>
        );
      })}
    </nav>
  );
}
