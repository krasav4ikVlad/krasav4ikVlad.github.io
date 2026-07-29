import { Cell, Section } from '@telegram-apps/telegram-ui';
import IconCalendar from '~icons/solar/calendar-linear';
import IconBill from '~icons/solar/bill-list-linear';
import IconDevices from '~icons/solar/smartphone-2-linear';
import IconGlobal from '~icons/solar/global-linear';
import IconChevron from '~icons/solar/alt-arrow-right-linear';
import IconCopy from '~icons/solar/copy-linear';

import { BASE_DEVICES, DEVICE_FEE } from '../config';
import { fmtDateTime, gb, money, periodLabel } from '../format';
import { ClickCell } from './ClickCell';

const Chevron = () => <IconChevron className="ico ico--sm ico--muted" />;

export function SubscriptionSection({ sub, bypass, connectUrl, onNavigate, onCopyLink }) {
  const dateCls = sub.expired ? 'val-bad' : sub.soon ? 'val-warn' : 'cell-value--strong';

  return (
    <Section
      header="Подписка"
      footer={sub.fee > 0
        ? `Каждое устройство свыше ${BASE_DEVICES} стоит ${DEVICE_FEE} ₽ в месяц и списывается отдельно от подписки.`
        : `Первые ${BASE_DEVICES} устройства входят в подписку.`}
    >
      <Cell
        before={<IconCalendar className="ico ico--muted" />}
        after={<span className={`cell-value ${dateCls}`}>{fmtDateTime(sub.expire)}</span>}
      >
        Истекает
      </Cell>

      <ClickCell
        before={<IconBill className="ico ico--muted" />}
        after={<span className="cell-value cell-value--strong">{money(sub.renew)}</span>}
        subtitle={sub.fee > 0 ? `+ ${money(sub.fee)}/мес за устройства` : null}
        onClick={() => onNavigate('plan')}
      >
        Тариф — {periodLabel(sub.period)}
      </ClickCell>

      <ClickCell
        before={<IconDevices className="ico ico--muted" />}
        after={<Chevron />}
        subtitle={sub.used === null ? `лимит ${sub.limit}` : `${sub.used} из ${sub.limit} занято`}
        onClick={() => onNavigate('devices')}
      >
        Устройства
      </ClickCell>

      <ClickCell
        before={<IconGlobal className="ico ico--muted" />}
        after={<Chevron />}
        subtitle={bypass?.enabled ? `${gb(bypass.traffic_limit_gb)} в пакете` : 'обход белых списков'}
        onClick={() => onNavigate('bypass')}
      >
        ByPass
      </ClickCell>

      <ClickCell
        before={<IconCopy className="ico ico--muted" />}
        subtitle={<span className="reflink">{connectUrl.replace(/^https?:\/\//, '')}</span>}
        onClick={onCopyLink}
        multiline
      >
        Ссылка подписки
      </ClickCell>
    </Section>
  );
}
