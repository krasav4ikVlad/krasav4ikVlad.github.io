import { Cell, Progress, Section } from '@telegram-apps/telegram-ui';
import IconInvite from '~icons/solar/users-group-rounded-linear';
import IconPayout from '~icons/solar/hand-money-linear';
import IconShare from '~icons/solar/share-linear';
import IconGraph from '~icons/solar/graph-up-linear';
import IconChevron from '~icons/solar/alt-arrow-right-linear';

import { PAYOUT_MIN, REF_SHARE } from '../config';
import { clamp, money, plural } from '../format';
import { ClickCell } from './ClickCell';

export function ReferralSection({ referrals, link, onShare, onNavigate }) {
  const invited = Number(referrals.invited) || 0;
  const active = Number(referrals.active) || 0;
  const withdrawable = Number(referrals.withdrawable) || 0;
  const ready = withdrawable >= PAYOUT_MIN;

  return (
    <Section
      header="Приглашения"
      footer={`Вы получаете ${REF_SHARE}% с каждого пополнения приглашённого. Реферальные можно тратить в боте или выводить от ${money(PAYOUT_MIN)}.`}
    >
      <Cell
        before={<IconInvite className="ico ico--muted" />}
        after={<span className="cell-value cell-value--strong">{invited}</span>}
        subtitle={`${active} ${plural(active, 'активный', 'активных', 'активных')}`}
      >
        Приглашено друзей
      </Cell>

      <Cell
        before={<IconGraph className="ico ico--muted" />}
        after={<span className="cell-value">{money(referrals.earned_total)}</span>}
      >
        Заработано всего
      </Cell>

      <ClickCell
        before={<IconPayout className="ico ico--muted" />}
        after={<span className={`cell-value ${ready ? 'val-ok' : ''}`}>{money(withdrawable)}</span>}
        subtitle={ready
          ? 'можно заказать вывод'
          : (
            <>
              <span>до вывода ещё {money(PAYOUT_MIN - withdrawable)}</span>
              <div className="payout-bar">
                <Progress value={clamp((withdrawable / PAYOUT_MIN) * 100, 0, 100)} />
              </div>
            </>
          )}
        onClick={() => onNavigate('payout')}
        multiline
      >
        Доступно к выводу
      </ClickCell>

      <ClickCell
        before={<IconShare className="ico ico--muted" />}
        after={<IconChevron className="ico ico--sm ico--muted" />}
        subtitle={<span className="reflink">{link.replace(/^https?:\/\//, '')}</span>}
        onClick={onShare}
        multiline
      >
        Поделиться ссылкой
      </ClickCell>
    </Section>
  );
}
