import { PLANS, DEVICE_FEE, BASE_DEVICES, DAY, HOUR } from './config';

export const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

export function money(n) {
  const v = Math.round(Number(n) || 0);
  return `${v.toLocaleString('ru-RU')} ₽`;
}

export function planBase(days) {
  const plan = PLANS.find((p) => p.days === Number(days));
  return plan ? plan.price : 0;
}

export function periodLabel(days) {
  switch (Number(days)) {
    case 1:    return 'день';
    case 30:   return 'месяц';
    case 90:   return '3 месяца';
    case 1095: return '3 года';
    default:   return `${days} дн.`;
  }
}

export function devicesFee(limit) {
  return Math.max(0, (Number(limit) || BASE_DEVICES) - BASE_DEVICES) * DEVICE_FEE;
}

/** Сервер отдаёт naive-ISO, как лежит в Mongo — читаем как локальное время. */
export function parseDate(value) {
  if (!value) return null;
  if (value instanceof Date) return value;
  const d = new Date(typeof value === 'number' ? value : String(value).replace(' ', 'T'));
  return Number.isNaN(+d) ? null : d;
}

export function fmtDateTime(d) {
  if (!d) return '—';
  const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getDate())}.${p(d.getMonth() + 1)}.${d.getFullYear()} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function plural(n, one, few, many) {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
  return many;
}

/** «11 дней» / «38 часов» / «12 минут» — как в напоминаниях бота. */
export function timeLeftText(ms) {
  if (ms <= 0) return 'истекла';
  if (ms >= 2 * DAY) {
    const n = Math.floor(ms / DAY);
    return `${n} ${plural(n, 'день', 'дня', 'дней')}`;
  }
  if (ms >= HOUR) {
    const n = Math.floor(ms / HOUR);
    return `${n} ${plural(n, 'час', 'часа', 'часов')}`;
  }
  const n = Math.max(1, Math.round(ms / 60e3));
  return `${n} ${plural(n, 'минута', 'минуты', 'минут')}`;
}

export function gb(bytesOrGb) {
  const n = Number(bytesOrGb) || 0;
  return `${Number.isInteger(n) ? n : n.toFixed(1)} Гб`;
}

/**
 * Сводка по подписке: одно место, где решается «активна / скоро / истекла».
 * Пороги те же, что у process_subscriptions() в боте.
 */
export function subSummary(sub = {}) {
  const shortUuid = sub.short_uuid || '';
  const has = Boolean(shortUuid);
  const expire = parseDate(sub.expire_at);
  const msLeft = expire ? expire - Date.now() : 0;
  const period = Number(sub.period_days) || 30;
  const limit = Number(sub.device_limit) || BASE_DEVICES;
  const used = Number.isFinite(Number(sub.devices_used)) ? Number(sub.devices_used) : null;
  const renew = Number.isFinite(Number(sub.renew_price)) ? Number(sub.renew_price) : planBase(period);

  return {
    has,
    shortUuid,
    expire,
    msLeft,
    period,
    limit,
    used,
    renew,
    fee: devicesFee(limit),
    expired: has && msLeft <= 0,
    soon: has && msLeft > 0 && msLeft < 3 * DAY,
    active: has && msLeft > 0,
    /** 'none' | 'ok' | 'soon' | 'expired' — для бейджа и цвета текста */
    status: !has ? 'none' : msLeft <= 0 ? 'expired' : msLeft < 3 * DAY ? 'soon' : 'ok',
  };
}
