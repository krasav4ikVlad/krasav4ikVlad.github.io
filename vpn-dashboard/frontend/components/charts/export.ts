"use client";

/** PNG (SVG → canvas) and CSV export for charts. */

export function exportCsv(
  rows: Record<string, unknown>[],
  filename: string,
): void {
  if (rows.length === 0) return;
  const headers = Array.from(
    rows.reduce<Set<string>>((acc, r) => {
      Object.keys(r).forEach((k) => acc.add(k));
      return acc;
    }, new Set()),
  );
  const escape = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[";\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const csv = [
    headers.join(";"),
    ...rows.map((r) => headers.map((h) => escape(r[h])).join(";")),
  ].join("\n");
  // BOM so Excel opens cyrillic correctly
  download(new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" }), `${filename}.csv`);
}

export async function exportPng(
  container: HTMLElement,
  filename: string,
): Promise<void> {
  const svg = container.querySelector("svg");
  if (!svg) return;
  const clone = svg.cloneNode(true) as SVGSVGElement;
  const rect = svg.getBoundingClientRect();
  clone.setAttribute("width", String(rect.width));
  clone.setAttribute("height", String(rect.height));
  // inline currentColor / CSS vars: serialize with computed styles on text
  inlineComputedColor(svg, clone);
  const xml = new XMLSerializer().serializeToString(clone);
  const blob = new Blob([xml], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  try {
    const img = new Image();
    await new Promise<void>((resolve, reject) => {
      img.onload = () => resolve();
      img.onerror = () => reject(new Error("svg render failed"));
      img.src = url;
    });
    const scale = 2;
    const canvas = document.createElement("canvas");
    canvas.width = rect.width * scale;
    canvas.height = rect.height * scale;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const surface = getComputedStyle(document.body).getPropertyValue("--surface").trim();
    ctx.fillStyle = surface || "#1a1a19";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.scale(scale, scale);
    ctx.drawImage(img, 0, 0, rect.width, rect.height);
    canvas.toBlob((png) => {
      if (png) download(png, `${filename}.png`);
    });
  } finally {
    URL.revokeObjectURL(url);
  }
}

function inlineComputedColor(source: SVGSVGElement, clone: SVGSVGElement): void {
  const srcEls = source.querySelectorAll<SVGElement>("*");
  const dstEls = clone.querySelectorAll<SVGElement>("*");
  srcEls.forEach((el, i) => {
    const dst = dstEls[i];
    if (!dst) return;
    const cs = getComputedStyle(el);
    for (const prop of ["fill", "stroke"] as const) {
      const value = el.getAttribute(prop);
      if (value && (value.startsWith("var(") || value === "currentColor")) {
        dst.setAttribute(prop, cs.getPropertyValue(prop));
      }
    }
    if (el.tagName === "text" || el.tagName === "tspan") {
      dst.setAttribute("fill", cs.fill);
      dst.setAttribute("font-family", cs.fontFamily);
      dst.setAttribute("font-size", cs.fontSize);
    }
  });
}

function download(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}
