"use client";

/** Wraps a chart and lets the user drag it left/right (mouse or finger)
 *  to slide a fixed-size window through a longer daily series — like
 *  panning a map. Vertical page scrolling keeps working (touch-action:
 *  pan-y); horizontal drags move the window. */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent,
  type ReactNode,
} from "react";

export function PannableChart({
  data,
  window: win,
  render,
  offset: controlledOffset,
  onOffsetChange,
}: {
  data: Record<string, unknown>[];
  /** сколько точек видно одновременно */
  window: number;
  render: (visible: Record<string, unknown>[]) => ReactNode;
  /** controlled-режим: несколько графиков листаются одним окном */
  offset?: number;
  onOffsetChange?: (offset: number) => void;
}) {
  // offset — на сколько точек окно сдвинуто в прошлое (0 = сегодня справа)
  const [ownOffset, setOwnOffset] = useState(0);
  const offset = controlledOffset ?? ownOffset;
  const setOffset = useCallback(
    (compute: (o: number) => number) => {
      if (onOffsetChange !== undefined) {
        onOffsetChange(compute(controlledOffset ?? 0));
      } else {
        setOwnOffset(compute);
      }
    },
    [onOffsetChange, controlledOffset],
  );
  const ref = useRef<HTMLDivElement | null>(null);
  const drag = useRef<{ lastX: number; moved: boolean } | null>(null);

  const maxOffset = Math.max(0, data.length - win);
  const off = Math.min(offset, maxOffset);
  useEffect(() => {
    // данные перезагрузились и стали короче — не выпадать за край
    if (offset > maxOffset) setOffset(() => maxOffset);
  }, [offset, maxOffset, setOffset]);

  const end = data.length - off;
  const visible = data.slice(Math.max(0, end - win), end);
  const canPan = maxOffset > 0;

  const onPointerDown = useCallback(
    (e: PointerEvent<HTMLDivElement>) => {
      if (!canPan) return;
      drag.current = { lastX: e.clientX, moved: false };
      e.currentTarget.setPointerCapture(e.pointerId);
    },
    [canPan],
  );

  const onPointerMove = useCallback(
    (e: PointerEvent<HTMLDivElement>) => {
      const st = drag.current;
      if (!st) return;
      const width = ref.current?.clientWidth || 1;
      const pxPerPoint = Math.max(3, width / win);
      const dx = e.clientX - st.lastX;
      const steps = Math.trunc(dx / pxPerPoint);
      if (steps !== 0) {
        st.lastX += steps * pxPerPoint;
        st.moved = true;
        // тянем вправо → уезжаем в прошлое (как перетаскивание карты)
        setOffset((o) => Math.min(maxOffset, Math.max(0, o + steps)));
      }
    },
    [win, maxOffset],
  );

  const endDrag = useCallback(() => {
    drag.current = null;
  }, []);

  return (
    <div
      ref={ref}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      className="relative select-none"
      style={{
        touchAction: "pan-y",
        cursor: canPan ? (drag.current ? "grabbing" : "grab") : undefined,
      }}
    >
      {render(visible)}
      {off > 0 ? (
        <button
          onClick={() => setOffset(() => 0)}
          className="absolute right-1 top-0 z-10 rounded-full border border-hairline bg-surface px-2 py-0.5 text-[11px] font-medium text-ink-2 shadow-sm hover:text-ink"
        >
          к сегодня →
        </button>
      ) : null}
    </div>
  );
}
