"use client";

/** Retention cohort matrix: rows = cohorts, columns = month offsets,
 *  sequential-blue cell backgrounds by retention %. */

const RAMP: [number, string][] = [
  [0, "var(--surface-2)"],
  [5, "var(--seq-100)"],
  [15, "var(--seq-250)"],
  [30, "var(--seq-400)"],
  [50, "var(--seq-550)"],
  [70, "var(--seq-700)"],
];

function cellBg(pct: number): string {
  let bg = RAMP[0][1];
  for (const [threshold, color] of RAMP) {
    if (pct >= threshold) bg = color;
  }
  return bg;
}

function cellInk(pct: number): string {
  return pct >= 30 ? "var(--seq-cell-ink-strong)" : "var(--ink-2)";
}

export function CohortGrid({
  cohorts,
}: {
  cohorts: { cohort: string; size: number; retention: number[] }[];
}) {
  const maxLen = Math.max(0, ...cohorts.map((c) => c.retention.length));
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[560px] border-separate border-spacing-[2px] text-xs">
        <thead>
          <tr className="text-muted">
            <th className="px-1 text-left font-medium">Когорта</th>
            <th className="px-1 text-right font-medium">Юзеров</th>
            {Array.from({ length: maxLen }).map((_, i) => (
              <th key={i} className="px-1 text-center font-medium">
                M{i}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {cohorts.map((c) => (
            <tr key={c.cohort}>
              <td className="whitespace-nowrap px-1 text-ink-2">{c.cohort}</td>
              <td className="px-1 text-right tabular text-ink-2">
                {c.size.toLocaleString("ru-RU")}
              </td>
              {Array.from({ length: maxLen }).map((_, i) => {
                const pct = c.retention[i];
                if (pct === undefined)
                  return <td key={i} className="min-w-[38px]" />;
                return (
                  <td
                    key={i}
                    title={`${c.cohort}, месяц ${i}: ${pct}%`}
                    className="min-w-[38px] rounded-[3px] px-1 py-1.5 text-center tabular"
                    style={{ background: cellBg(pct), color: cellInk(pct) }}
                  >
                    {Math.round(pct)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
