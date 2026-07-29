import { Placeholder, Section } from '@telegram-apps/telegram-ui';
import IconShield from '~icons/solar/shield-minimalistic-bold-duotone';
import IconGift from '~icons/solar/gift-linear';

import { PLANS } from '../config';
import { money } from '../format';
import { ClickCell } from './ClickCell';

/** Состояние «подписки нет»: тарифы обычным списком настроек. */
export function NoSubscription({ onPick }) {
  return (
    <>
      <Section>
        <Placeholder
          header="Подписка не оформлена"
          description="Первый день стоит 4 ₽. Дальше подписка продлевается автоматически с баланса — отключить можно в любой момент."
        >
          <IconShield style={{ width: 88, height: 88, color: 'var(--tgui--link_color)' }} />
        </Placeholder>
      </Section>

      <Section
        header="Тарифы"
        footer="С подпиской от месяца даём подарочную — её можно отправить другу через inline-режим бота."
      >
        {PLANS.map((plan) => (
          <ClickCell
            key={plan.days}
            onClick={() => onPick(plan)}
            after={<span className="cell-value cell-value--strong">{money(plan.price)}</span>}
            before={plan.gift
              ? <IconGift className="ico ico--muted" />
              : <span style={{ display: 'block', width: 28 }} />}
          >
            {plan.name}
          </ClickCell>
        ))}
      </Section>
    </>
  );
}
