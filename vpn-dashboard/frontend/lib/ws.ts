"use client";

/** Live event stream over WebSocket with auto-reconnect.
 *  Falls back to polling /api/overview/events/recent when WS can't connect
 *  (e.g. a proxy without upgrade support). */

import { useEffect, useRef, useState } from "react";
import { fetcher } from "./api";
import type { LiveEvent } from "./types";

const MAX_EVENTS = 100;

function wsUrl(): string {
  const explicit = process.env.NEXT_PUBLIC_WS_URL;
  if (explicit) return explicit;
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}/api/ws/events`;
}

export function useEventStream(): {
  events: LiveEvent[];
  connected: boolean;
} {
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const seenIds = useRef<Set<string>>(new Set());

  useEffect(() => {
    let ws: WebSocket | null = null;
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let attempts = 0;
    let disposed = false;

    const push = (incoming: LiveEvent[]) => {
      if (disposed || incoming.length === 0) return;
      setEvents((prev) => {
        const fresh = incoming.filter((e) => {
          const key = `${e.id}:${e.ts}`;
          if (seenIds.current.has(key)) return false;
          seenIds.current.add(key);
          return true;
        });
        if (fresh.length === 0) return prev;
        if (seenIds.current.size > 1000) {
          seenIds.current = new Set(
            [...seenIds.current].slice(-MAX_EVENTS * 2),
          );
        }
        return [...fresh.reverse(), ...prev].slice(0, MAX_EVENTS);
      });
    };

    const startPolling = () => {
      if (pollTimer || disposed) return;
      const poll = () =>
        fetcher<{ events: LiveEvent[] }>("/api/overview/events/recent?limit=50")
          .then((r) => push(r.events))
          .catch(() => undefined);
      poll();
      pollTimer = setInterval(poll, 20_000);
    };

    const connect = () => {
      if (disposed) return;
      try {
        ws = new WebSocket(wsUrl());
      } catch {
        startPolling();
        return;
      }
      ws.onopen = () => {
        attempts = 0;
        setConnected(true);
        if (pollTimer) {
          clearInterval(pollTimer);
          pollTimer = null;
        }
      };
      ws.onmessage = (msg) => {
        try {
          const data = JSON.parse(msg.data as string);
          if (data.type === "event") push([data as LiveEvent]);
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onclose = () => {
        setConnected(false);
        if (disposed) return;
        attempts += 1;
        if (attempts >= 3) startPolling();
        reconnectTimer = setTimeout(connect, Math.min(1000 * 2 ** attempts, 30_000));
      };
      ws.onerror = () => ws?.close();
    };

    connect();
    return () => {
      disposed = true;
      ws?.close();
      if (pollTimer) clearInterval(pollTimer);
      if (reconnectTimer) clearTimeout(reconnectTimer);
    };
  }, []);

  return { events, connected };
}
