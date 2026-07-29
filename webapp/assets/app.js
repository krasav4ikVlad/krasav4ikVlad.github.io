/* ═══════════════════════════════════════════════════════════════
   RS VPN — Telegram Mini App, главный экран.

   Данные приходят одним запросом: GET {apiBase}{endpoint}
   с заголовком `Authorization: tma <initData>`.
   Контракт ответа описан в webapp/README.md.

   Без initData (открыли в обычном браузере) страница показывает
   демо-данные, чтобы экран можно было смотреть и верстать.
   ═══════════════════════════════════════════════════════════════ */
'use strict';

const CFG = window.RSVPN_CONFIG || {};
const tg  = window.Telegram && window.Telegram.WebApp;

const HOUR = 3600e3;
const DAY  = 24 * HOUR;

/* ── тарифы бота: держим в одном месте, чтобы совпадало с plan_base_price() ── */
const PLANS = [
  { days: 1,    price: 4,    name: '1 день',    gift: null },
  { days: 30,   price: 100,  name: '1 месяц',   gift: '+ подарок' },
  { days: 90,   price: 250,  name: '3 месяца',  gift: '+ подарок' },
  { days: 1095, price: 2000, name: '3 года',    gift: '+ подарок' }
];
const DEVICE_FEE   = 75;   // за каждое устройство свыше двух, ₽/мес
const PAYOUT_MIN   = 500;  // минимум для вывода реферальных, ₽

const $ = (id) => document.getElementById(id);

/* ═════════════ утилиты ═════════════ */

const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

function money(n) {
  const v = Math.round(Number(n) || 0);
  return v.toLocaleString('ru-RU') + ' ₽';
}

function planBase(days) {
  const p = PLANS.find((x) => x.days === Number(days));
  return p ? p.price : 0;
}

function periodLabel(days) {
  switch (Number(days)) {
    case 1:    return 'день';
    case 30:   return 'месяц';
    case 90:   return '3 месяца';
    case 1095: return '3 года';
    default:   return `${days} дн.`;
  }
}

function parseDate(v) {
  if (!v) return null;
  if (v instanceof Date) return v;
  // сервер отдаёт naive-ISO ('2026-08-15T12:30:00') — трактуем как локальное время
  const d = new Date(typeof v === 'number' ? v : String(v).replace(' ', 'T'));
  return isNaN(d) ? null : d;
}

function fmtDateTime(d) {
  if (!d) return '—';
  const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getDate())}.${p(d.getMonth() + 1)}.${d.getFullYear()} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** Остаток времени в виде {n, unit} — дни / часы / минуты. */
function timeLeft(ms) {
  if (ms <= 0) return { n: 0, unit: 'истекла' };
  if (ms >= 2 * DAY)  return { n: Math.floor(ms / DAY),   unit: 'дней' };
  if (ms >= HOUR)     return { n: Math.floor(ms / HOUR),  unit: 'часов' };
  return { n: Math.max(1, Math.round(ms / 60e3)), unit: 'минут' };
}

function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
  return many;
}

/* ═════════════ Telegram-мост ═════════════ */

function haptic(style = 'light') {
  try { tg.HapticFeedback.impactOccurred(style); } catch (_) {}
}
function notify(type = 'success') {
  try { tg.HapticFeedback.notificationOccurred(type); } catch (_) {}
}

function openExternal(url) {
  if (!url) return;
  if (tg && /(^https?:\/\/)?(t\.me|telegram\.me)\//.test(url)) {
    try { tg.openTelegramLink(url); return; } catch (_) {}
  }
  if (tg && tg.openLink) {
    try { tg.openLink(url); return; } catch (_) {}
  }
  window.open(url, '_blank', 'noopener');
}

let toastTimer = null;
function toast(text) {
  const el = $('toast');
  el.textContent = text;
  el.classList.add('is-on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('is-on'), 2200);
}

async function copy(text, label = 'Скопировано') {
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
  } catch (_) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.cssText = 'position:fixed;top:-1000px;opacity:0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch (__) {}
    ta.remove();
  }
  notify('success');
  toast(label);
}

/* ═════════════ загрузка состояния ═════════════ */

const DEMO = {
  user: { id: 152341887, first_name: 'Влад', username: 'krasav4ik', email: 'Не привязана' },
  balance: 340,
  subscription: {
    active: true,
    period_days: 30,
    expire_at: new Date(Date.now() + 11.4 * DAY).toISOString().slice(0, 19),
    short_uuid: 'k3f9qzt2mx',
    device_limit: 3,
    devices_used: 2
  },
  bypass: { enabled: true, traffic_limit_gb: 15, traffic_used_gb: 4.2 },
  referrals: { invited: 14, active: 6, withdrawable: 360, earned_total: 1980 },
  gifts: { '1day': 1, '1month': 2 }
};

async function fetchState() {
  const initData = tg && tg.initData;
  if (!initData) return { data: DEMO, demo: true };

  const url = (CFG.apiBase || '') + (CFG.endpoint || '/api/webapp/me');
  const res = await fetch(url, {
    method: 'GET',
    headers: {
      'Accept': 'application/json',
      'Authorization': 'tma ' + initData
    },
    cache: 'no-store'
  });

  if (!res.ok) {
    const err = new Error('HTTP ' + res.status);
    err.status = res.status;
    throw err;
  }
  return { data: await res.json(), demo: false };
}

/* ═════════════ рендер ═════════════ */

let STATE = null;
let tickTimer = null;

function render(data) {
  STATE = data;

  const u    = data.user || {};
  const sub  = data.subscription || {};
  const ref  = data.referrals || {};
  const byp  = data.bypass || {};
  const gift = data.gifts || {};

  document.body.querySelectorAll('.is-skeleton').forEach((el) => el.classList.remove('is-skeleton'));

  /* — личная строка — */
  const name = [u.first_name, u.last_name].filter(Boolean).join(' ')
            || (u.username ? '@' + u.username : 'Аккаунт RS VPN');
  $('user-name').textContent = name;
  $('avatar-letter').textContent = (name.replace('@', '')[0] || '·').toUpperCase();

  const photo = (tg && tg.initDataUnsafe && tg.initDataUnsafe.user && tg.initDataUnsafe.user.photo_url) || u.photo_url;
  if (photo) {
    const av = $('avatar');
    av.style.backgroundImage = `url("${photo}")`;
    av.classList.add('has-photo');
  }

  const uid = u.id || (tg && tg.initDataUnsafe && tg.initDataUnsafe.user && tg.initDataUnsafe.user.id) || '';
  const idBtn = $('user-id');
  idBtn.textContent = uid || '—';
  idBtn.dataset.copy = String(uid || '');

  /* — подписка — */
  const hasSub  = Boolean(sub.short_uuid);
  const expire  = parseDate(sub.expire_at);
  const msLeft  = expire ? expire - Date.now() : 0;
  const active  = hasSub && msLeft > 0;
  const period  = Number(sub.period_days) || 30;
  const limit   = Number(sub.device_limit) || 2;
  const used    = Number.isFinite(Number(sub.devices_used)) ? Number(sub.devices_used) : null;

  const hero = $('hero');
  hero.dataset.mode = hasSub ? 'live' : 'empty';
  $('hero-live').hidden  = !hasSub;
  $('hero-empty').hidden = hasSub;

  // сигнальный цвет: те же пороги, что у напоминаний бота (3 дня / истекла)
  const sig = !hasSub ? 'idle' : msLeft <= 0 ? 'dead' : msLeft < 3 * DAY ? 'warn' : 'ok';
  document.body.dataset.sig = sig === 'ok' || sig === 'idle' ? 'ok' : sig;

  const pill = $('state-pill');
  pill.dataset.state = !hasSub ? 'idle' : msLeft <= 0 ? 'off' : msLeft < 3 * DAY ? 'warn' : 'on';
  $('state-text').textContent = !hasSub ? 'нет подписки' : msLeft <= 0 ? 'истекла' : msLeft < 3 * DAY ? 'скоро конец' : 'активна';

  if (hasSub) {
    $('hero-eyebrow').textContent = 'подписка · ' + periodLabel(period);
    paintGauge(msLeft, period);

    const expEl = $('spec-expire');
    expEl.textContent = fmtDateTime(expire);
    expEl.classList.toggle('is-warn', msLeft < 3 * DAY);

    $('spec-plan').textContent = `${money(planBase(period))} / ${periodLabel(period)}`;

    const fee = Math.max(0, limit - 2) * DEVICE_FEE;
    const renew = Number.isFinite(Number(sub.renew_price)) ? Number(sub.renew_price) : planBase(period);
    $('spec-price').textContent = fee > 0
      ? `${money(renew)} + ${money(fee)}/мес`
      : money(renew);

    const link = `${CFG.connectBase}/${sub.short_uuid}`;
    const connect = $('connect');
    const copyBtn = $('copy-link');
    copyBtn.hidden = false;
    copyBtn.dataset.link = link;   // ссылку копируем всегда, даже если подписка истекла

    if (msLeft <= 0) {
      // доступа сейчас нет — главное действие это продление, как и в боте
      connect.removeAttribute('href');
      connect.dataset.link = '';
      $('connect-label').textContent = 'Продлить подписку';
    } else {
      connect.href = link;
      connect.dataset.link = link;
      $('connect-label').textContent = 'Настроить VPN';
    }

    $('act-extend-s').textContent = money(renew);
    if (renew < planBase(period)) $('act-extend-s').textContent = money(renew) + ' · скидка';
  } else {
    $('hero-eyebrow').textContent = 'подписка';
    $('copy-link').hidden = true;
    $('copy-link').dataset.link = '';
    const connect = $('connect');
    connect.removeAttribute('href');
    connect.dataset.link = '';
    $('connect-label').textContent = 'Подключить RS VPN';
    $('act-extend-s').textContent = 'нет подписки';
    renderPlans();
  }

  // пояснение под кнопкой — ровно то, что бот пишет в blockquote
  const note = $('hero-note');
  if (!hasSub) {
    note.textContent = '';
  } else if (msLeft <= 0) {
    note.textContent = 'Подписка истекла. Продлите её — доступ вернётся сразу после списания.';
  } else if (msLeft < DAY) {
    note.textContent = Number(data.balance) >= planBase(period)
      ? 'Автопродление сработает автоматически: средства спишутся в течение суток до окончания.'
      : 'На балансе не хватает средств для автопродления — пополните, иначе доступ отключится.';
  } else {
    note.textContent = '';
  }

  /* — метрики — */
  const bal = Number(data.balance) || 0;
  $('m-balance').textContent = money(bal);
  if (!hasSub) {
    $('m-balance-sub').textContent = bal >= PLANS[0].price ? 'хватит на первый день' : 'пополните для старта';
  } else {
    const needed = Math.max(0, planBase(period) + Math.max(0, limit - 2) * DEVICE_FEE - bal);
    $('m-balance-sub').textContent = needed > 0 ? `не хватает ${money(needed)}` : 'хватит на продление';
  }

  $('m-devices').innerHTML = used === null
    ? `${limit}`
    : `${used}<small> / ${limit}</small>`;
  $('m-devices-sub').textContent = used === null
    ? `лимит ${limit}`
    : `свободно ${Math.max(0, limit - used)}`;

  const invited = Number(ref.invited) || 0;
  const activeRef = Number(ref.active) || 0;
  $('m-friends').textContent = invited;
  $('m-friends-sub').textContent = `${activeRef} ${plural(activeRef, 'активный', 'активных', 'активных')}`;

  /* — действия — */
  $('act-bypass-s').textContent = byp.enabled
    ? `${(Number(byp.traffic_limit_gb) || 0).toFixed(0)} Гб в пакете`
    : 'подключить';

  const giftTotal = (Number(gift['1day']) || 0) + (Number(gift['1month']) || 0);
  $('act-gifts-s').textContent = giftTotal > 0
    ? `${giftTotal} ${plural(giftTotal, 'подарок', 'подарка', 'подарков')}`
    : 'через inline';

  /* — реферальный блок — */
  const wd = Number(ref.withdrawable) || 0;
  $('ref-withdrawable').textContent = money(wd);
  $('ref-invited').textContent = invited;
  $('ref-earned').textContent = money(ref.earned_total);

  const pct = clamp((wd / PAYOUT_MIN) * 100, 0, 100);
  $('ref-fill').style.width = pct + '%';
  $('ref-bar').setAttribute('aria-valuenow', String(Math.min(wd, PAYOUT_MIN)));
  $('ref-hint').textContent = wd >= PAYOUT_MIN
    ? 'можно заказать вывод'
    : `до вывода ещё ${money(PAYOUT_MIN - wd)}`;

  const refLink = ref.link || `${CFG.bot}?start=ref_${uid}`;
  $('ref-link').textContent = refLink.replace(/^https?:\/\//, '');
  $('ref-link').dataset.link = refLink;

  /* — почта — */
  const email = (u.email || '').trim();
  $('email-note').hidden = Boolean(email) && email !== 'Не привязана';

  /* — тикающий остаток — */
  clearInterval(tickTimer);
  if (hasSub && msLeft > 0 && msLeft < 2 * DAY) {
    tickTimer = setInterval(() => {
      const left = parseDate(sub.expire_at) - Date.now();
      if (left <= 0) { clearInterval(tickTimer); render(STATE); return; }
      paintGauge(left, period);
    }, 30e3);
  }
}

function paintGauge(msLeft, periodDays) {
  const total = Math.max(1, periodDays * DAY);
  const frac  = clamp(msLeft / total, 0, 1);
  const C = 396; // 2π·63 — совпадает со stroke-dasharray в CSS
  const arc = $('gauge-arc');
  arc.style.strokeDashoffset = String(C * (1 - frac));
  arc.style.opacity = frac <= 0 ? '0' : '1';   // иначе round-cap рисует точку на нуле

  const { n, unit } = timeLeft(msLeft);
  if (msLeft <= 0) {
    $('gauge-num').textContent = '0';
    $('gauge-unit').textContent = 'истекла';
    return;
  }
  const short = unit === 'дней' ? 'дн' : unit === 'часов' ? 'ч' : 'мин';
  $('gauge-num').innerHTML = `${n}<small>${short}</small>`;
  $('gauge-unit').textContent = 'осталось';
}

function renderPlans() {
  const ul = $('plans');
  if (ul.childElementCount) return;
  ul.innerHTML = PLANS.map((p) => `
    <li data-plan="${p.days}" ${p.days === 30 ? 'data-hot="1"' : ''}>
      <span class="plan__n">${p.name}${p.gift ? `<span class="plan__gift">${p.gift}</span>` : ''}</span>
      <span class="plan__p">${money(p.price)}</span>
    </li>`).join('');
}

/* ═════════════ загрузка / ошибки ═════════════ */

function skeleton() {
  ['user-name', 'm-balance', 'm-devices', 'm-friends',
   'spec-expire', 'spec-plan', 'spec-price',
   'ref-withdrawable', 'ref-invited', 'ref-earned', 'ref-link'
  ].forEach((id) => $(id) && $(id).classList.add('is-skeleton'));
}

let loading = false;
async function load({ silent = false } = {}) {
  if (loading) return;
  loading = true;

  const chip = $('refresh');
  chip.classList.add('is-busy');
  if (!silent && !STATE) skeleton();

  try {
    const { data, demo } = await fetchState();
    $('fail').hidden = true;
    render(data);
    $('build').textContent = demo ? 'демо' : 'v1';
    if (demo && !silent) toast('Демо-режим: откройте кабинет из бота');
  } catch (e) {
    if (e.status === 401 || e.status === 403) {
      showFail('Сессия не подтверждена', 'Откройте кабинет заново из бота — Telegram выдаст свежую подпись.');
    } else if (!STATE) {
      showFail('Нет связи с сервером', 'Проверьте интернет и попробуйте ещё раз.');
    } else {
      toast('Не удалось обновить данные');
    }
  } finally {
    chip.classList.remove('is-busy');
    loading = false;
  }
}

function showFail(title, sub) {
  $('fail').hidden = false;
  $('fail').querySelector('.fail__t').textContent = title;
  $('fail-s').textContent = sub;
  notify('error');
}

/* ═════════════ навигация (следующие экраны) ═════════════ */

const ROUTES = {
  extend:    'Продление',
  topup:     'Пополнение баланса',
  devices:   'Менеджер устройств',
  bypass:    'ByPass / белые списки',
  referrals: 'Реферальная программа',
  gifts:     'Подарки'
};

function navigate(route) {
  haptic('light');
  if (route === 'email') { openExternal(CFG.bot); return; }
  const title = ROUTES[route];
  location.hash = '#/' + route;
  toast(title ? `${title} — экран в работе` : 'Раздел в работе');
}

/* ═════════════ события ═════════════ */

function wire() {
  $('refresh').addEventListener('click', () => { haptic('light'); load(); });
  $('fail-retry').addEventListener('click', () => { $('fail').hidden = true; load(); });

  $('user-id').addEventListener('click', (e) => copy(e.currentTarget.dataset.copy, 'ID скопирован'));

  $('connect').addEventListener('click', (e) => {
    const link = e.currentTarget.dataset.link;
    e.preventDefault();
    haptic('medium');
    if (link) openExternal(link);
    else navigate('extend');
  });

  $('copy-link').addEventListener('click', (e) => {
    const link = e.currentTarget.dataset.link;
    if (link) copy(link, 'Ссылка подписки скопирована');
  });

  $('ref-link').addEventListener('click', (e) => copy(e.currentTarget.dataset.link, 'Ссылка скопирована'));

  $('ref-share').addEventListener('click', () => {
    const link = $('ref-link').dataset.link;
    if (!link) return;
    haptic('light');
    openExternal('https://t.me/share/url?url=' + encodeURIComponent(link) +
                 '&text=' + encodeURIComponent('RS VPN — быстрый VPN в Telegram. Первый день за 4 ₽.'));
  });

  // тайлы, кнопки действий, тарифы, ссылка «Привязать»
  document.addEventListener('click', (e) => {
    const nav = e.target.closest('[data-nav]');
    if (nav) { navigate(nav.dataset.nav); return; }

    const ext = e.target.closest('[data-ext]');
    if (ext) { haptic('light'); openExternal(CFG[ext.dataset.ext]); return; }

    const plan = e.target.closest('[data-plan]');
    if (plan) { navigate('extend'); }
  });

  // тайлы доступны с клавиатуры
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const t = e.target.closest('[data-nav][role="button"]');
    if (t) { e.preventDefault(); navigate(t.dataset.nav); }
  });

  // вернулись в приложение — подтягиваем свежий баланс
  let hidAt = 0;
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { hidAt = Date.now(); return; }
    if (Date.now() - hidAt > 30e3) load({ silent: true });
  });
}

/* ═════════════ старт ═════════════ */

function bootTelegram() {
  if (!tg) return;
  try { tg.ready(); } catch (_) {}
  try { tg.expand(); } catch (_) {}
  try { tg.setHeaderColor('#06080a'); } catch (_) {}
  try { tg.setBackgroundColor('#06080a'); } catch (_) {}
  try { tg.disableVerticalSwipes(); } catch (_) {}  // чтобы скролл не закрывал апп
  try { tg.BackButton.hide(); } catch (_) {}
}

bootTelegram();
wire();
load();
