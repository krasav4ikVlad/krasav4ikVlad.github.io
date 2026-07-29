import { Banner, Button } from '@telegram-apps/telegram-ui';
import IconShieldOk from '~icons/solar/shield-check-bold-duotone';
import IconShieldWarn from '~icons/solar/shield-warning-bold-duotone';
import IconShieldBad from '~icons/solar/shield-cross-bold-duotone';

import { money, timeLeftText, fmtDateTime, periodLabel } from '../format';

const VIEW = {
  ok: {
    Icon: IconShieldOk,
    cls: 'ico--ok',
    header: 'RS VPN подключён',
  },
  soon: {
    Icon: IconShieldWarn,
    cls: 'ico--warn',
    header: 'Подписка скоро истекает',
  },
  expired: {
    Icon: IconShieldBad,
    cls: 'ico--bad',
    header: 'Подписка истекла',
  },
};

/**
 * Верхний баннер: состояние доступа и главное действие.
 * Текст подсказки повторяет то, что бот пишет в blockquote.
 */
export function StatusBanner({ sub, balance, onConnect, onExtend }) {
  const view = VIEW[sub.status] || VIEW.ok;
  const { Icon } = view;

  const until = sub.expired
    ? `Истекла ${fmtDateTime(sub.expire)}.`
    : `До ${fmtDateTime(sub.expire)}.`;

  const description = (() => {
    if (sub.expired) {
      return `${until} Продление — ${money(sub.renew)}, доступ вернётся сразу, ссылка на подключение останется той же.`;
    }
    if (sub.msLeft < 24 * 3600e3) {
      return balance >= sub.renew
        ? `${until} Автопродление сработает само: средства спишутся в течение суток до окончания.`
        : `${until} На балансе не хватает средств для автопродления — пополните, иначе доступ отключится.`;
    }
    if (sub.soon) {
      return `${until} Продление спишется автоматически, если на балансе будет ${money(sub.renew + sub.fee)}.`;
    }
    return `${until} Автопродление включено, тариф — ${money(sub.renew)} за ${periodLabel(sub.period)}.`;
  })();

  return (
    <Banner
      type="section"
      before={<div className="badge-round"><Icon className={`ico ${view.cls}`} /></div>}
      header={view.header}
      subheader={sub.expired ? 'Доступ отключён' : `Осталось ${timeLeftText(sub.msLeft)}`}
      description={description}
    >
      <div className="banner-actions">
        {sub.expired ? (
          <>
            <Button size="s" mode="filled" onClick={onExtend}>Продлить</Button>
            <Button size="s" mode="bezeled" onClick={onConnect}>Открыть ссылку</Button>
          </>
        ) : (
          <>
            <Button size="s" mode="filled" onClick={onConnect}>Настроить VPN</Button>
            <Button size="s" mode="bezeled" onClick={onExtend}>Продлить</Button>
          </>
        )}
      </div>
    </Banner>
  );
}
