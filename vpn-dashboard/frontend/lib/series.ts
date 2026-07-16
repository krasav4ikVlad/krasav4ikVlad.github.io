/** Zero-fill helpers for time-bucketed series.
 *
 * Backend buckets come from $dateTrunc and skip empty periods entirely —
 * on a categorical X axis a missing day silently vanishes, visually gluing
 * neighbours together and misleading the reader. */

export type Granularity = "day" | "week" | "month";

function nextBucket(d: Date, granularity: Granularity): Date {
  const n = new Date(d);
  if (granularity === "month") n.setUTCMonth(n.getUTCMonth() + 1);
  else n.setUTCDate(n.getUTCDate() + (granularity === "week" ? 7 : 1));
  return n;
}

/** Fill gaps between the first and last bucket with `make(bucketIso)` rows.
 *  Rows must carry an ISO datetime under `bucketKey`; output stays sorted. */
export function fillTimeBuckets<Row extends Record<string, unknown>>(
  rows: Row[],
  granularity: Granularity,
  make: (bucketIso: string) => Row,
  bucketKey = "bucket",
): Row[] {
  if (rows.length < 2) return rows;
  const byBucket = new Map<number, Row>();
  for (const row of rows) {
    const t = new Date(String(row[bucketKey])).getTime();
    if (!Number.isNaN(t)) byBucket.set(t, row);
  }
  if (byBucket.size < 2) return rows;

  const times = [...byBucket.keys()].sort((a, b) => a - b);
  const out: Row[] = [];
  let cursor = new Date(times[0]);
  const end = times[times.length - 1];
  // hard cap so a corrupt future date can't spin the loop forever
  for (let i = 0; i < 1000 && cursor.getTime() <= end; i++) {
    const t = cursor.getTime();
    out.push(byBucket.get(t) ?? make(cursor.toISOString()));
    cursor = nextBucket(cursor, granularity);
  }
  return out;
}
