import { InlineButtons, Section } from '@telegram-apps/telegram-ui';
import IconRenew from '~icons/solar/refresh-circle-bold-duotone';
import IconWallet from '~icons/solar/wallet-money-bold-duotone';
import IconInvite from '~icons/solar/users-group-rounded-bold-duotone';

export function QuickActions({ onNavigate }) {
  return (
    <Section>
      <InlineButtons mode="bezeled">
        <InlineButtons.Item text="Продлить" onClick={() => onNavigate('extend')}>
          <IconRenew className="ico" />
        </InlineButtons.Item>
        <InlineButtons.Item text="Пополнить" onClick={() => onNavigate('topup')}>
          <IconWallet className="ico" />
        </InlineButtons.Item>
        <InlineButtons.Item text="Позвать" onClick={() => onNavigate('referrals')}>
          <IconInvite className="ico" />
        </InlineButtons.Item>
      </InlineButtons>
    </Section>
  );
}
