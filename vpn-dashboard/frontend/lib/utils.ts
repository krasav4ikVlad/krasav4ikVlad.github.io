import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Fixed categorical palette (CSS variables) — assign in order, never cycle. */
export const SERIES = [
  "var(--series-1)",
  "var(--series-2)",
  "var(--series-3)",
  "var(--series-4)",
  "var(--series-5)",
  "var(--series-6)",
  "var(--series-7)",
  "var(--series-8)",
] as const;

/** Fold categories beyond the palette into "other" instead of cycling hues. */
export function capSeries<Row extends { [k: string]: unknown }>(
  keys: string[],
  max = 8,
): { keys: string[]; folded: string[] } {
  if (keys.length <= max) return { keys, folded: [] };
  return { keys: [...keys.slice(0, max - 1), "other"], folded: keys.slice(max - 1) };
}
