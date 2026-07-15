"use client";

/** Card wrapper for every chart: title, optional actions, PNG/CSV export,
 *  loading skeleton and empty state. */

import { useRef, type ReactNode } from "react";
import { Download, Image as ImageIcon } from "lucide-react";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ChartSkeleton } from "@/components/ui/skeleton";
import { exportCsv, exportPng } from "./export";

export function ChartCard({
  title,
  subtitle,
  loading,
  empty,
  csvRows,
  filename = "chart",
  actions,
  height = 280,
  children,
}: {
  title: string;
  subtitle?: string;
  loading?: boolean;
  empty?: boolean;
  csvRows?: Record<string, unknown>[];
  filename?: string;
  actions?: ReactNode;
  height?: number;
  children: ReactNode;
}) {
  const bodyRef = useRef<HTMLDivElement>(null);
  return (
    <Card>
      <CardHeader>
        <div className="min-w-0">
          <CardTitle>{title}</CardTitle>
          {subtitle ? (
            <p className="mt-0.5 text-xs text-muted">{subtitle}</p>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {actions}
          {csvRows && csvRows.length > 0 ? (
            <Button
              variant="ghost"
              size="icon"
              title="Экспорт CSV"
              onClick={() => exportCsv(csvRows, filename)}
            >
              <Download className="h-4 w-4" />
            </Button>
          ) : null}
          <Button
            variant="ghost"
            size="icon"
            title="Экспорт PNG"
            onClick={() => bodyRef.current && exportPng(bodyRef.current, filename)}
          >
            <ImageIcon className="h-4 w-4" />
          </Button>
        </div>
      </CardHeader>
      <div ref={bodyRef}>
        {loading ? (
          <ChartSkeleton height={height} />
        ) : empty ? (
          <div
            className="flex items-center justify-center text-sm text-muted"
            style={{ height }}
          >
            Нет данных за выбранный период
          </div>
        ) : (
          children
        )}
      </div>
    </Card>
  );
}
