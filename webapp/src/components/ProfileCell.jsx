import { Avatar, Badge, Section } from '@telegram-apps/telegram-ui';
import { ClickCell } from './ClickCell';

const STATUS = {
  ok:      { text: 'Подписка активна', mode: 'primary',   cls: 'val-ok' },
  soon:    { text: 'Скоро истекает',   mode: 'primary',   cls: 'val-warn' },
  expired: { text: 'Подписка истекла', mode: 'critical',  cls: 'val-bad' },
  none:    { text: 'Подписки нет',     mode: 'secondary', cls: 'hint' },
};

export function ProfileCell({ user, status, photo, onCopyId }) {
  const name = [user.first_name, user.last_name].filter(Boolean).join(' ')
    || (user.username ? `@${user.username}` : 'Аккаунт RS VPN');
  const acronym = (name.replace('@', '')[0] || 'R').toUpperCase();
  const s = STATUS[status] || STATUS.none;

  return (
    <Section>
      <ClickCell
        onClick={onCopyId}
        before={<Avatar size={48} src={photo} acronym={acronym} />}
        titleBadge={<Badge type="dot" mode={s.mode} />}
        subtitle={<span className={s.cls}>{s.text}</span>}
        description={<span className="hint">ID {user.id}</span>}
      >
        {name}
      </ClickCell>
    </Section>
  );
}
