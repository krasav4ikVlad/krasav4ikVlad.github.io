# Интеграция бота техподдержки с панелью

Два патча в бота: кнопка «Профиль на сайте» в карточке пользователя и запись
сообщений тикетов в общую коллекцию `support_messages` (чтобы переписка была
видна на сайте). Все правки — в файлах `config.py`, `loader.py`, `utils/utils.py`,
`handlers/start.py`, `handlers/admin.py`.

## 1. Кнопка «Профиль на сайте»

Ссылка ведёт на карточку пользователя в панели (`/#/user/{uid}`). Безопасность:
панель сама требует вход — неавторизованный, перейдя по ссылке, увидит только
форму логина, а после входа попадёт ровно на карточку этого пользователя
(deep-link сохраняется через логин).

**config.py** — добавить:

```python
PANEL_URL = 'https://ops.вашдомен.com'   # адрес панели операторов, без / на конце
```

**utils/utils.py** — импорт и две правки клавиатур:

```python
from config import SUPPORT_CHAT_ID, REMNAWAVE_TOKEN, PANEL_URL   # добавить PANEL_URL
```

В `_user_info_kb(uid)` добавить кнопку (например, после «Обновить»):

```python
def _user_info_kb(uid: int) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text='🔄 Обновить', callback_data=f'admin:{uid}:info_refresh')
    kb.button(text='🖥 Профиль на сайте', url=f'{PANEL_URL}/#/user/{uid}')   # <--- НОВОЕ
    kb.button(text='📲 Очистить подключения', callback_data=f'admin:{uid}:devices_delete_all')
    kb.button(text='🧩 Быстрые ответы', callback_data=f'admin:{uid}:qr_menu')
    kb.button(text='🔒 Закрыть тикет', callback_data='admin:close_ticket')
    kb.adjust(1, 2)
    return kb
```

В `_send_and_pin_user_info` — та же кнопка в обе клавиатуры (основную и fallback):

```python
kb.button(text='🖥 Профиль на сайте', url=f'{PANEL_URL}/#/user/{uid}')
```

## 2. Общая история сообщений (`support_messages`)

Панель пишет свои ответы в коллекцию `RS_2.support_messages`; чтобы на сайте была
видна и переписка из Telegram, бот должен писать туда же.

Схема документа:

```js
{
  user_id: 802421217,
  direction: "user" | "operator" | "system",
  text: "...",                  // текст/подпись или "<photo>", "<voice>" и т.п.
  operator_login: "имя_оператора" | null,
  source: "tg" | "site",
  timestamp: ISODate
}
```

**loader.py** — добавить коллекцию:

```python
support_messages = db['support_messages']
```

**utils/utils.py** — добавить хелпер:

```python
from loader import users, bot, support_quick_replies, support_messages  # добавить support_messages


def _message_text_for_log(message: types.Message) -> str:
    if message.text:
        return message.text
    if message.caption:
        ct = getattr(message, 'content_type', 'media')
        return f'<{ct}> {message.caption}'
    return f'<{getattr(message, "content_type", "media")}>'


async def log_support_message(uid: int, direction: str, text: str,
                              operator_login: str | None = None):
    try:
        await support_messages.insert_one({
            'user_id': uid,
            'direction': direction,
            'text': (text or '')[:3500],
            'operator_login': operator_login,
            'source': 'tg',
            'timestamp': datetime.datetime.now(datetime.timezone.utc),
        })
    except Exception:
        pass  # лог не должен ломать доставку
```

**handlers/start.py** — логируем входящие сообщения пользователя. В
`handle_user_message` после КАЖДОГО успешного `_copy_user_message_to_thread_with_retry`
(их три вызова: два в ветке создания треда и один в основной) добавить:

```python
from utils.utils import log_support_message, _message_text_for_log   # к существующим импортам

# сразу после успешной пересылки (ok == True), перед return:
await log_support_message(uid, 'user', _message_text_for_log(message))
```

**handlers/admin.py** — логируем ответы операторов из треда. В
`relay_operator_message_to_user`, в ветке успеха после `_set_reaction(message, '👍')`:

```python
from utils.utils import log_support_message, _message_text_for_log   # к существующим импортам

op_login = (message.from_user.username or message.from_user.full_name or 'operator')
await log_support_message(uid, 'operator', _message_text_for_log(message), operator_login=op_login)
```

Опционально — в обработчиках закрытия тикета (`on_admin_close_ticket_btn`,
`on_support_close`, `auto_close_pending_tickets`) добавить системную запись:

```python
await log_support_message(uid, 'system', 'Тикет закрыт (TG)')
```

## 3. Настройка панели

В `.env` панели добавить и перезапустить (`pm2 restart operator-panel`):

```ini
TG_BOT_TOKEN=<токен САППОРТ-бота>
SUPPORT_CHAT_ID=-100xxxxxxxxxx
```

Панель использует те же соглашения, что и бот: статусы `pending/open/closed` в
`info.support.status`, заголовки тредов `🟡/🟢/🔴 Тикет #uid`, кнопки оценки
`rate:1..5` (их обрабатывает бот). Ответ с сайта уходит пользователю в ЛС и
дублируется в тред с пометкой «💻 Ответ с сайта — {имя}».
