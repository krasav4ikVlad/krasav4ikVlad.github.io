"use client";

/** Conversion funnel: horizontal bars on an ordinal blue ramp with
 *  step-to-step conversion and median time between steps. */

import { fmtHoursApprox, fmtNum, fmtPct } from "@/lib/format";
import type { FunnelStep } from "@/lib/types";

const STEP_LABELS: Record<string, string> = {
  registration: "Регистрация",
  first_topup: "Первое пополнение",
  first_sub: "Первая подписка",
  renewal: "Продление",
};

// ordinal ramp: no step lighter than seq-250 (contrast floor)
const RAMP = ["var(--seq-250)", "var(--seq-400)", "var(--seq-550)", "var(--seq-700)"];

export function Funnel({ steps }: { steps: FunnelStep[] }) {
  const max = Math.max(1, ...steps.map((s) => s.users));
  return (
    <div className="space-y-3">
      {steps.map((s, i) => (
        <div key={s.step}>
          <div className="mb-1 flex flex-wrap items-baseline justify-between gap-x-3 text-xs">
            <span className="font-medium text-ink">
              {STEP_LABELS[s.step] ?? s.step}
            </span>
            <span className="text-muted">
              {fmtNum(s.users)}
              {i > 0 ? (
                <>
                  {" · конверсия "}
                  <span className="text-ink-2">{fmtPct(s.conversion_pct)}</span>
                  {s.median_hours_from_prev !== null ? (
                    <>
                      {" · медиана "}
                      <span className="text-ink-2">
                        {fmtHoursApprox(s.median_hours_from_prev)}
                      </span>
                    </>
                  ) : null}
                </>
              ) : null}
            </span>
          </div>
          <div className="h-6 w-full rounded bg-surface-2">
            <div
              className="h-6 rounded transition-[width] duration-300"
              style={{
                width: `${Math.max(1.5, (s.users / max) * 100)}%`,
                background: RAMP[Math.min(i, RAMP.length - 1)],
              }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}
