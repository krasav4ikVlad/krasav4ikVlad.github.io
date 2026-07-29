import { Section } from '@telegram-apps/telegram-ui';
import IconGift from '~icons/solar/gift-linear';
import IconMail from '~icons/solar/letter-linear';
import IconSupport from '~icons/solar/chat-round-dots-linear';
import IconChannel from '~icons/solar/volume-loud-linear';
import IconDoc from '~icons/solar/document-text-linear';
import IconChevron from '~icons/solar/alt-arrow-right-linear';

import { CFG } from '../config';
import { openExternal } from '../telegram';
import { plural } from '../format';
import { ClickCell } from './ClickCell';

const Chevron = () => <IconChevron className="ico ico--sm ico--muted" />;

export function MoreSection({ gifts, email, onNavigate }) {
  const giftTotal = (Number(gifts?.['1day']) || 0) + (Number(gifts?.['1month']) || 0);
  const emailSet = Boolean(email) && email !== 'Не привязана';

  return (
    <>
      <Section header="Ещё">
        <ClickCell
          before={<IconGift className="ico ico--muted" />}
          after={<Chevron />}
          subtitle={giftTotal > 0
            ? `${giftTotal} ${plural(giftTotal, 'подарок', 'подарка', 'подарков')} наготове`
            : 'подарить подписку другу'}
          onClick={() => onNavigate('gifts')}
        >
          Подарки
        </ClickCell>

        <ClickCell
          before={<IconMail className={`ico ${emailSet ? 'ico--muted' : 'ico--warn'}`} />}
          after={<Chevron />}
          subtitle={emailSet
            ? email
            : <span className="val-warn">не привязана — не сможем связаться, если Telegram ограничат</span>}
          onClick={() => onNavigate('email')}
          multiline
        >
          Почта
        </ClickCell>
      </Section>

      <Section header="Помощь">
        <ClickCell
          before={<IconSupport className="ico ico--muted" />}
          after={<Chevron />}
          onClick={() => openExternal(CFG.support)}
        >
          Поддержка
        </ClickCell>

        <ClickCell
          before={<IconChannel className="ico ico--muted" />}
          after={<Chevron />}
          onClick={() => openExternal(CFG.channel)}
        >
          Канал RS VPN
        </ClickCell>

        <ClickCell
          before={<IconDoc className="ico ico--muted" />}
          after={<Chevron />}
          onClick={() => openExternal(CFG.terms)}
        >
          Соглашение и оферта
        </ClickCell>

        <ClickCell
          before={<IconDoc className="ico ico--muted" />}
          after={<Chevron />}
          onClick={() => openExternal(CFG.privacy)}
        >
          Конфиденциальность
        </ClickCell>
      </Section>
    </>
  );
}
