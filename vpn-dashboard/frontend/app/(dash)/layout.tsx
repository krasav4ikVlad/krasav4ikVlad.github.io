"use client";

import { Suspense, type ReactNode } from "react";
import useSWR from "swr";
import { api, fetcher } from "@/lib/api";
import { PeriodProvider } from "@/lib/period";
import { MobileNav, Sidebar } from "@/components/layout/sidebar";
import { Topbar } from "@/components/layout/topbar";

function AuthGate({ children }: { children: ReactNode }) {
  // 401 inside fetcher redirects to /login; this just avoids a flash
  const { data, error } = useSWR(api.urls ? "/api/auth/me" : null, fetcher, {
    shouldRetryOnError: false,
    revalidateOnFocus: false,
  });
  if (!data && !error) {
    return (
      <div className="flex h-screen items-center justify-center text-sm text-muted">
        Загрузка…
      </div>
    );
  }
  return <>{children}</>;
}

export default function DashLayout({ children }: { children: ReactNode }) {
  return (
    <Suspense>
      <PeriodProvider>
        <AuthGate>
          <div className="flex min-h-screen">
            <Sidebar />
            <div className="min-w-0 flex-1">
              <Topbar />
              <main className="px-3 py-4 pb-20 md:px-5 md:pb-6">{children}</main>
            </div>
          </div>
          <MobileNav />
        </AuthGate>
      </PeriodProvider>
    </Suspense>
  );
}
