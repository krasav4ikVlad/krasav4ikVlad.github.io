"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import useSWR from "swr";
import { LogOut, Moon, Search, Sun } from "lucide-react";
import { api, fetcher } from "@/lib/api";
import { fmtMoney } from "@/lib/format";
import type { UserSearchRow } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PeriodPicker } from "./period-picker";

function useDebounced<V>(value: V, ms: number): V {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

function UserSearch() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [focus, setFocus] = useState(false);
  const debounced = useDebounced(q.trim(), 250);
  const boxRef = useRef<HTMLDivElement>(null);
  const { data } = useSWR<{ results: UserSearchRow[] }>(
    debounced.length >= 2 ? api.urls.userSearch(debounced) : null,
    fetcher,
    { keepPreviousData: true },
  );

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setFocus(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  const results = data?.results ?? [];
  return (
    <div ref={boxRef} className="relative w-full max-w-xs">
      <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" />
      <Input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onFocus={() => setFocus(true)}
        placeholder="Telegram ID или @username"
        className="pl-8"
      />
      {focus && debounced.length >= 2 ? (
        <div className="absolute top-full z-50 mt-1 w-full overflow-hidden rounded-md border border-hairline bg-surface shadow-lg">
          {results.length === 0 ? (
            <div className="px-3 py-2 text-xs text-muted">Никого не нашли</div>
          ) : (
            results.map((u) => (
              <button
                key={u.user_id}
                className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-xs hover:bg-surface-2"
                onClick={() => {
                  setFocus(false);
                  setQ("");
                  router.push(`/users/${u.user_id}`);
                }}
              >
                <span className="min-w-0">
                  <span className="block truncate font-medium text-ink">
                    {u.username ? `@${u.username}` : `#${u.user_id}`}
                  </span>
                  <span className="text-muted">
                    {u.segment ?? "—"} · {fmtMoney(u.topup_total)}
                  </span>
                </span>
                <span className="shrink-0 tabular text-muted">#{u.user_id}</span>
              </button>
            ))
          )}
        </div>
      ) : null}
    </div>
  );
}

function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  if (!mounted) return <Button variant="ghost" size="icon" aria-hidden />;
  const dark = resolvedTheme === "dark";
  return (
    <Button
      variant="ghost"
      size="icon"
      title={dark ? "Светлая тема" : "Тёмная тема"}
      onClick={() => setTheme(dark ? "light" : "dark")}
    >
      {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
    </Button>
  );
}

export function Topbar() {
  const router = useRouter();
  return (
    <header className="sticky top-0 z-40 border-b border-hairline bg-page/90 backdrop-blur">
      <div className="flex flex-wrap items-center gap-2 px-3 py-2 md:px-5">
        <UserSearch />
        <div className="ml-auto flex items-center gap-2">
          <PeriodPicker />
          <ThemeToggle />
          <Button
            variant="ghost"
            size="icon"
            title="Выйти"
            onClick={() =>
              api.logout().then(() => router.push("/login"))
            }
          >
            <LogOut className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </header>
  );
}
