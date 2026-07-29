import { CFG, DAY } from './config';
import { tg } from './telegram';

/** Демо-состояние для просмотра вне Telegram (без бэкенда). */
export const DEMO = {
  user: { id: 152341887, first_name: 'Влад', username: 'krasav4ik', email: 'Не привязана' },
  balance: 340,
  subscription: {
    active: true,
    period_days: 30,
    expire_at: new Date(Date.now() + 11.4 * DAY).toISOString().slice(0, 19),
    short_uuid: 'k3f9qzt2mx',
    device_limit: 3,
    devices_used: 2,
    renew_price: 100,
  },
  bypass: { enabled: true, traffic_limit_gb: 15, traffic_used_gb: 4.2 },
  referrals: { invited: 14, active: 6, withdrawable: 360, earned_total: 1980 },
  gifts: { '1day': 1, '1month': 2 },
};

const iso = (ms) => new Date(Date.now() + ms).toISOString().slice(0, 19);

/**
 * Состояния для просмотра вёрстки: ?state=soon | expired | none
 * Работает только в демо-режиме, вне Telegram.
 */
const DEMO_VARIANTS = {
  active: (d) => d,
  soon: (d) => ({
    ...d,
    balance: 20,
    subscription: { ...d.subscription, expire_at: iso(1.6 * DAY) },
  }),
  expired: (d) => ({
    ...d,
    balance: 0,
    subscription: { ...d.subscription, expire_at: iso(-3 * DAY) },
  }),
  none: (d) => ({
    ...d,
    balance: 15,
    user: { ...d.user, email: 'vlad@gmail.com' },
    subscription: { active: false, short_uuid: '' },
    bypass: { enabled: false },
    referrals: { invited: 0, active: 0, withdrawable: 0, earned_total: 0 },
    gifts: {},
  }),
};

function demoState() {
  const name = new URLSearchParams(window.location.search).get('state') || 'active';
  const mutate = DEMO_VARIANTS[name] || DEMO_VARIANTS.active;
  return mutate(structuredClone(DEMO));
}

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

/**
 * Одним запросом получаем всё состояние кабинета.
 * Подпись Telegram уходит как есть — проверяет её бэкенд (см. README).
 */
export async function fetchState() {
  const initData = tg?.initData;
  if (!initData) return { data: demoState(), demo: true };

  const res = await fetch(`${CFG.apiBase}${CFG.endpoint}`, {
    headers: { Accept: 'application/json', Authorization: `tma ${initData}` },
    cache: 'no-store',
  });

  if (!res.ok) throw new ApiError(`HTTP ${res.status}`, res.status);
  return { data: await res.json(), demo: false };
}
