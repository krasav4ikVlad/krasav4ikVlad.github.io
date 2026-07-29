/** Внешние адреса и эндпоинт кабинета. Правится в одном месте. */
export const CFG = {
  apiBase: '',                        // '' = тот же origin, что и мини-апп
  endpoint: '/api/webapp/me',

  bot: 'https://t.me/rsconnect_bot',
  support: 'https://t.me/RSConnectHelp_bot',
  channel: 'https://t.me/rsconnect_vpn',
  connectBase: 'https://connect.rsvps.tech',

  terms: 'https://telegra.ph/Polzovatelskoe-soglashenie-Publichnaya-oferta-RS-VPN-07-02',
  privacy: 'https://telegra.ph/Politika-konfidencialnosti-RS-VPN-07-02',
};

/* ── Значения, которые обязаны совпадать с ботом ────────────────────────────
   plan_base_price(), devices_monthly_price(), лимит вывода, пакеты ByPass.  */

export const PLANS = [
  { days: 1,    price: 4,    name: '1 день',   gift: false },
  { days: 30,   price: 100,  name: '1 месяц',  gift: true  },
  { days: 90,   price: 250,  name: '3 месяца', gift: true  },
  { days: 1095, price: 2000, name: '3 года',   gift: true  },
];

export const DEVICE_FEE = 75;   // ₽/мес за каждое устройство свыше двух
export const BASE_DEVICES = 2;  // базовый лимит устройств
export const PAYOUT_MIN = 500;  // минимум для вывода реферальных, ₽
export const REF_SHARE = 30;    // % с пополнений приглашённого

export const HOUR = 3600e3;
export const DAY = 24 * HOUR;
