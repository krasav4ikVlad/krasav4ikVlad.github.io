/** Тонкая обёртка над Telegram.WebApp — без лишней зависимости от SDK. */

export const tg = typeof window !== 'undefined' ? window.Telegram?.WebApp : undefined;

export const inTelegram = Boolean(tg?.initData);

export function boot() {
  if (!tg) return;
  try { tg.ready(); } catch {}
  try { tg.expand(); } catch {}
  try { tg.disableVerticalSwipes(); } catch {}   // скролл не закрывает апп
  try { tg.BackButton.hide(); } catch {}
}

/** ios-стиль для iOS/macOS, иначе base (Material). */
export function currentPlatform() {
  return ['ios', 'macos'].includes(tg?.platform) ? 'ios' : 'base';
}

/** Тема: из Telegram, а вне него — из системной настройки браузера. */
export function currentAppearance() {
  if (tg?.colorScheme) return tg.colorScheme === 'light' ? 'light' : 'dark';
  return window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

/** Подписка на смену темы: пользователь может переключить её на ходу. */
export function onAppearanceChange(handler) {
  const unsubs = [];

  if (tg?.onEvent) {
    tg.onEvent('themeChanged', handler);
    unsubs.push(() => tg.offEvent('themeChanged', handler));
  }

  const mq = window.matchMedia?.('(prefers-color-scheme: light)');
  if (mq?.addEventListener) {
    mq.addEventListener('change', handler);
    unsubs.push(() => mq.removeEventListener('change', handler));
  }

  return () => unsubs.forEach((fn) => fn());
}

export function haptic(style = 'light') {
  try { tg.HapticFeedback.impactOccurred(style); } catch {}
}

export function notify(type = 'success') {
  try { tg.HapticFeedback.notificationOccurred(type); } catch {}
}

export function openExternal(url) {
  if (!url) return;
  if (tg && /^https?:\/\/(t\.me|telegram\.me)\//.test(url)) {
    try { tg.openTelegramLink(url); return; } catch {}
  }
  if (tg?.openLink) {
    try { tg.openLink(url); return; } catch {}
  }
  window.open(url, '_blank', 'noopener');
}

export async function copyText(text) {
  if (!text) return false;
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.cssText = 'position:fixed;top:-1000px;opacity:0';
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch {}
    ta.remove();
    return ok;
  }
}

export function share(url, text) {
  openExternal(`https://t.me/share/url?url=${encodeURIComponent(url)}&text=${encodeURIComponent(text)}`);
}
