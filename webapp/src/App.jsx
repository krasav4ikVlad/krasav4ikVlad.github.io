import { useCallback, useEffect, useMemo, useState } from 'react';
import { Button, Caption, Placeholder, Snackbar, Spinner } from '@telegram-apps/telegram-ui';
import IconWarn from '~icons/solar/danger-circle-bold-duotone';
import IconDone from '~icons/solar/shield-check-bold-duotone';

import { CFG, DAY } from './config';
import { fetchState } from './api';
import { subSummary } from './format';
import { copyText, haptic, notify, openExternal, share, tg } from './telegram';

import { ProfileCell } from './components/ProfileCell';
import { StatusBanner } from './components/StatusBanner';
import { NoSubscription } from './components/NoSubscription';
import { QuickActions } from './components/QuickActions';
import { SubscriptionSection } from './components/SubscriptionSection';
import { BalanceSection } from './components/BalanceSection';
import { ReferralSection } from './components/ReferralSection';
import { MoreSection } from './components/MoreSection';

/** Экраны, которых ещё нет — заглушка с понятным названием. */
const ROUTES = {
  extend: 'Продление подписки',
  topup: 'Пополнение баланса',
  devices: 'Менеджер устройств',
  bypass: 'ByPass и белые списки',
  referrals: 'Реферальная программа',
  payout: 'Заказ выплаты',
  plan: 'Смена длительности',
};

/** Разделы, которые в боте делаются сообщением в чат — уводим в бота. */
const IN_BOT = new Set(['email', 'promo', 'gifts']);

export function App() {
  const [state, setState] = useState(null);
  const [demo, setDemo] = useState(false);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [snack, setSnack] = useState(null);
  const [, setTick] = useState(0);

  const toast = useCallback((text, ok = true) => {
    setSnack({ text, ok });
  }, []);

  const load = useCallback(async ({ silent = false } = {}) => {
    if (busy) return;
    setBusy(true);
    try {
      const { data, demo: isDemo } = await fetchState();
      setState(data);
      setDemo(isDemo);
      setError(null);
      if (isDemo && !silent) toast('Демо-данные: откройте кабинет из бота');
    } catch (e) {
      notify('error');
      if (e.status === 401 || e.status === 403) {
        setError({
          header: 'Сессия не подтверждена',
          description: 'Откройте кабинет заново из бота — Telegram выдаст свежую подпись.',
        });
      } else if (!silent) {
        setError({
          header: 'Нет связи с сервером',
          description: 'Проверьте интернет и попробуйте ещё раз.',
        });
      }
    } finally {
      setBusy(false);
    }
  }, [busy, toast]);

  useEffect(() => { load(); }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  // вернулись в мини-апп — подтягиваем свежий баланс
  useEffect(() => {
    let hidAt = 0;
    const onVisibility = () => {
      if (document.hidden) { hidAt = Date.now(); return; }
      if (Date.now() - hidAt > 30e3) load({ silent: true });
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => document.removeEventListener('visibilitychange', onVisibility);
  }, [load]);

  const sub = useMemo(() => subSummary(state?.subscription), [state]);

  // пересчитываем остаток, пока он измеряется часами
  useEffect(() => {
    if (!sub.has || sub.msLeft <= 0 || sub.msLeft > 2 * DAY) return undefined;
    const id = setInterval(() => setTick((t) => t + 1), 30e3);
    return () => clearInterval(id);
  }, [sub.has, sub.msLeft]);

  const navigate = useCallback((route) => {
    haptic('light');
    if (IN_BOT.has(route)) { openExternal(CFG.bot); return; }
    window.location.hash = `#/${route}`;
    toast(`${ROUTES[route] || 'Раздел'} — экран в работе`);
  }, [toast]);

  if (!state && error) {
    return (
      <div className="center-screen">
        <Placeholder header={error.header} description={error.description}
          action={<Button size="m" onClick={() => load()} loading={busy}>Повторить</Button>}
        >
          <IconWarn style={{ width: 96, height: 96, color: 'var(--tgui--destructive_text_color)' }} />
        </Placeholder>
      </div>
    );
  }

  if (!state) {
    return <div className="center-screen"><Spinner size="l" /></div>;
  }

  const user = state.user || {};
  const photo = tg?.initDataUnsafe?.user?.photo_url || user.photo_url;
  const uid = user.id || tg?.initDataUnsafe?.user?.id || '';
  const balance = Number(state.balance) || 0;
  const refLink = state.referrals?.link || `${CFG.bot}?start=ref_${uid}`;
  const connectUrl = `${CFG.connectBase}/${sub.shortUuid}`;

  const copyAnd = async (text, okText) => {
    const ok = await copyText(text);
    if (ok) notify('success');
    toast(ok ? okText : 'Не удалось скопировать', ok);
  };

  return (
    <div className="app">
      <ProfileCell
        user={{ ...user, id: uid }}
        status={sub.status}
        photo={photo}
        onCopyId={() => copyAnd(String(uid), 'Идентификатор скопирован')}
      />

      {sub.has ? (
        <>
          <StatusBanner
            sub={sub}
            balance={balance}
            onConnect={() => { haptic('medium'); openExternal(connectUrl); }}
            onExtend={() => navigate('extend')}
          />
          <QuickActions onNavigate={navigate} />
          <SubscriptionSection
            sub={sub}
            bypass={state.bypass}
            connectUrl={connectUrl}
            onNavigate={navigate}
            onCopyLink={() => copyAnd(connectUrl, 'Ссылка подписки скопирована')}
          />
        </>
      ) : (
        <NoSubscription onPick={() => navigate('extend')} />
      )}

      <BalanceSection balance={balance} sub={sub} onNavigate={navigate} />

      <ReferralSection
        referrals={state.referrals || {}}
        link={refLink}
        onShare={() => { haptic('light'); share(refLink, 'RS VPN — быстрый VPN в Telegram. Первый день за 4 ₽.'); }}
        onNavigate={navigate}
      />

      <MoreSection gifts={state.gifts} email={user.email} onNavigate={navigate} />

      <div className="footnote">
        <Caption level="2">
          RS VPN · личный кабинет{demo ? ' · демо-данные' : ''}
          {' · '}
          <span role="button" tabIndex={0} style={{ textDecoration: 'underline' }}
            onClick={() => load()}
          >
            обновить
          </span>
        </Caption>
      </div>

      {snack && (
        <Snackbar
          duration={2400}
          onClose={() => setSnack(null)}
          before={snack.ok
            ? <IconDone className="ico ico--ok" />
            : <IconWarn className="ico ico--bad" />}
        >
          {snack.text}
        </Snackbar>
      )}
    </div>
  );
}
