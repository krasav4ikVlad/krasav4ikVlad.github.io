"use client";

/** Global period filter — preset chips + custom date range. */

import { useState } from "react";
import { Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  PRESET_LABELS,
  usePeriod,
  type PeriodPreset,
} from "@/lib/period";
import { cn } from "@/lib/utils";

const ORDER: PeriodPreset[] = ["today", "7d", "30d", "90d", "all"];

export function PeriodPicker() {
  const { preset, setPreset, setCustom, customFrom, customTo } = usePeriod();
  const [open, setOpen] = useState(false);
  const [from, setFrom] = useState(customFrom ?? "");
  const [to, setTo] = useState(customTo ?? "");

  return (
    <div className="relative">
      <div className="flex items-center gap-1 overflow-x-auto rounded-md bg-surface-2 p-1">
        {ORDER.map((p) => (
          <button
            key={p}
            onClick={() => {
              setOpen(false);
              setPreset(p);
            }}
            className={cn(
              "flex items-center gap-1 whitespace-nowrap rounded px-2 py-1 text-xs font-medium transition-colors",
              preset === p
                ? "bg-surface text-ink shadow-sm"
                : "text-muted hover:text-ink",
            )}
          >
            {preset === p ? <Check className="h-3 w-3" strokeWidth={3} /> : null}
            {PRESET_LABELS[p]}
          </button>
        ))}
        <button
          onClick={() => setOpen((v) => !v)}
          className={cn(
            "whitespace-nowrap rounded px-2 py-1 text-xs font-medium",
            preset === "custom"
              ? "bg-surface text-ink shadow-sm"
              : "text-muted hover:text-ink",
          )}
        >
          {preset === "custom" && customFrom
            ? `${customFrom} — ${customTo ?? "…"}`
            : PRESET_LABELS.custom}
        </button>
      </div>
      {open ? (
        <div className="absolute right-0 top-full z-50 mt-1 flex flex-col gap-2 rounded-md border border-hairline bg-surface p-3 shadow-lg">
          <label className="text-xs text-muted">
            С
            <input
              type="date"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
              className="mt-0.5 block h-8 rounded-md border border-hairline bg-surface px-2 text-xs text-ink"
            />
          </label>
          <label className="text-xs text-muted">
            По
            <input
              type="date"
              value={to}
              onChange={(e) => setTo(e.target.value)}
              className="mt-0.5 block h-8 rounded-md border border-hairline bg-surface px-2 text-xs text-ink"
            />
          </label>
          <Button
            size="sm"
            disabled={!from || !to}
            onClick={() => {
              setCustom(from, to);
              setOpen(false);
            }}
          >
            Применить
          </Button>
        </div>
      ) : null}
    </div>
  );
}
