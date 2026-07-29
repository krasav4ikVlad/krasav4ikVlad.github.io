import { Cell, Section } from '@telegram-apps/telegram-ui';
import IconWallet from '~icons/solar/wallet-money-linear';
import IconTopup from '~icons/solar/card-send-linear';
import IconTicket from '~icons/solar/ticket-linear';
import IconChevron from '~icons/solar/alt-arrow-right-linear';

import { PLANS } from '../config';
import { money } from '../format';
import { ClickCell } from './ClickCell';

const Chevron = () => <IconChevron className="ico ico--sm ico--muted" />;

export function BalanceSection({ balance, sub, onNavigate }) {
  const need = sub.has ? Math.max(0, sub.renew + sub.fee - balance) : 0;

  const subtitle = (() => {
    if (!sub.has) return balance >= PLANS[0].price ? 'хватит на первый день' : 'пополните, чтобы начать';
    if (need > 0) return <span className="val-bad">не хватает {money(need)}</span>;
    return 'хватит на продление';
  })();

  return (
    <Section header="Баланс">
      <Cell
        before={<IconWallet className="ico ico--muted" />}
        after={<span className="cell-value cell-value--strong">{money(balance)}</span>}
        subtitle={subtitle}
      >
        На счету
      </Cell>

      <ClickCell
        before={<IconTopup className="ico ico--muted" />}
        after={<Chevron />}
        subtitle="СБП, карта, криптовалюта"
        onClick={() => onNavigate('topup')}
      >
        Пополнить
      </ClickCell>

      <ClickCell
        before={<IconTicket className="ico ico--muted" />}
        after={<Chevron />}
        subtitle="баланс или гигабайты ByPass"
        onClick={() => onNavigate('promo')}
      >
        Промокод
      </ClickCell>
    </Section>
  );
}
