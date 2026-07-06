# Интеграция бота техподдержки с панелью

Два патча в бота: кнопка «Профиль на сайте» в карточке пользователя и запись
сообщений тикетов в общую коллекцию `support_messages` (чтобы переписка была
видна на сайте). Все правки — в файлах `config.py`, `loader.py`, `utils/utils.py`,
`handlers/start.py`, `handlers/admin.py`.

## 1. Кнопка «Профиль на сайте»

Ссылка ведёт на карточку пользователя в панели (`/user/{uid}` — путь без `#`:
некоторые Telegram-клиенты портят fragment в URL кнопок, поэтому сервер панели
сам редиректит этот путь на SPA-роут). Безопасность:
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
    kb.button(text='🖥 Профиль на сайте', url=f'{PANEL_URL}/user/{uid}')   # <--- НОВОЕ
    kb.button(text='📲 Очистить подключения', callback_data=f'admin:{uid}:devices_delete_all')
    kb.button(text='🧩 Быстрые ответы', callback_data=f'admin:{uid}:qr_menu')
    kb.button(text='🔒 Закрыть тикет', callback_data='admin:close_ticket')
    kb.adjust(1, 2)
    return kb
```

В `_send_and_pin_user_info` — та же кнопка в обе клавиатуры (основную и fallback):

```python
kb.button(text='🖥 Профиль на сайте', url=f'{PANEL_URL}/user/{uid}')
```

## 2. Общая история сообщений (`support_messages`)

Панель пишет свои ответы в коллекцию `RS_2.support_messages`; чтобы на сайте была
видна и переписка из Telegram, бот должен писать туда же.

Схема документа:

```js
{
  user_id: 802421217,
  direction: "user" | "operator" | "system",
  text: "...",                            // текст или подпись к вложению ('' если её нет)
  attachment: {                           // null для чисто текстовых сообщений
    type: "photo" | "video" | "document" | "voice" | "video_note"
        | "animation" | "audio" | "sticker",
    file_id: "...",                       // Telegram file_id — сайт скачает через getFile
    name: "имя_файла.pdf"                 // для документов/аудио (опционально)
  },
  operator_login: "имя_оператора" | null,
  source: "tg" | "site",
  timestamp: ISODate
}
```

Панель показывает вложения прямо в чате тикета: фото и стикеры — картинкой,
голосовые/аудио — плеером, видео/кружки/гифки — видеоплеером, документы —
кнопкой «открыть». Файлы она скачивает сама через `getFile` тем же ботом
(лимит Bot API — 20 МБ на файл), поэтому боту достаточно сохранить `file_id`.

**loader.py** — добавить коллекцию:

```python
support_messages = db['support_messages']
```

**utils/utils.py** — добавить хелперы:

```python
from loader import users, bot, support_quick_replies, support_messages  # добавить support_messages


def _message_text_for_log(message: types.Message) -> str:
    """Текст сообщения или подпись к вложению; спец-типы — плейсхолдером."""
    if message.text:
        return message.text
    if message.caption:
        return message.caption
    if message.location:
        return '<локация>'
    if message.contact:
        return '<контакт>'
    if message.poll:
        return '<опрос>'
    if message.dice:
        return '<кубик>'
    return ''


def _attachment_for_log(message: types.Message) -> dict | None:
    """file_id вложения — сайт скачает его через getFile."""
    if message.photo:
        return {'type': 'photo', 'file_id': message.photo[-1].file_id}
    if message.video:
        return {'type': 'video', 'file_id': message.video.file_id}
    if message.document:
        return {'type': 'document', 'file_id': message.document.file_id,
                'name': message.document.file_name or 'документ'}
    if message.voice:
        return {'type': 'voice', 'file_id': message.voice.file_id}
    if message.video_note:
        return {'type': 'video_note', 'file_id': message.video_note.file_id}
    if message.animation:
        return {'type': 'animation', 'file_id': message.animation.file_id}
    if message.audio:
        return {'type': 'audio', 'file_id': message.audio.file_id,
                'name': message.audio.file_name or 'аудио'}
    if message.sticker:
        return {'type': 'sticker', 'file_id': message.sticker.file_id}
    return None


async def log_support_message(uid: int, direction: str, text: str,
                              operator_login: str | None = None,
                              attachment: dict | None = None):
    try:
        await support_messages.insert_one({
            'user_id': uid,
            'direction': direction,
            'text': (text or '')[:3500],
            'attachment': attachment,
            'operator_login': operator_login,
            'source': 'tg',
            'timestamp': datetime.datetime.now(datetime.timezone.utc),
        })
    except Exception:
        pass  # лог не должен ломать доставку
```

**handlers/start.py** — логируем входящие сообщения пользователя ОДНИМ вызовом
в самом начале `handle_user_message` (в хендлере несколько веток с ранними
`return`, поэтому одна точка в начале надёжнее, чем раскладывать по веткам):

```python
from utils.utils import log_support_message, _message_text_for_log   # к существующим импортам


@router.message(F.chat.type == 'private', ~CommandStart())
async def handle_user_message(message: types.Message):
    if not await ensure_registered(message):
        return

    uid = message.from_user.id
    await log_support_message(uid, 'user', _message_text_for_log(message),
                              attachment=_attachment_for_log(message))   # <-- единственное место

    user_doc = await users.find_one({'user_data.user_id': uid}) or {}
    # ... дальше без изменений
```

Больше нигде в этом хендлере логировать не нужно — иначе будут дубли.
(Импортируйте `_attachment_for_log` вместе с остальными хелперами.)

**handlers/admin.py** — логируем ответы операторов из треда. В
`relay_operator_message_to_user`, в ветке успеха после `_set_reaction(message, '👍')`:

```python
from utils.utils import log_support_message, _message_text_for_log   # к существующим импортам

op_login = (message.from_user.username or message.from_user.full_name or 'operator')
await log_support_message(uid, 'operator', _message_text_for_log(message),
                          operator_login=op_login,
                          attachment=_attachment_for_log(message))
```

Опционально — в обработчиках закрытия тикета (`on_admin_close_ticket_btn`,
`on_support_close`, `auto_close_pending_tickets`) добавить системную запись:

```python
await log_support_message(uid, 'system', 'Тикет закрыт (TG)')
```

**handlers/admin.py — быстрые ответы.** Когда оператор жмёт кнопку быстрого
ответа (`qr_menu` → выбор пункта), бот шлёт пользователю текст из
`support_quick_replies` и пишет в тред «Пользователю отправлено: …» — но в
`support_messages` это не попадает, поэтому на сайте такого ответа не видно.
В обработчике колбэка быстрого ответа, СРАЗУ после успешной отправки текста
пользователю (`bot.send_message(uid, body...)`), добавить:

```python
op_login = (call.from_user.username or call.from_user.full_name or 'operator')
await log_support_message(uid, 'operator', body, operator_login=op_login)
```

где `body` — тот же текст инструкции, который ушёл пользователю. То же самое
в обработчике FAQ-меню пользователя (если бот сам отвечает на кнопки вроде
«Как продлить подписку?» в ЛС) — там `operator_login='бот (FAQ)'`.

Коллекция `support_quick_replies` теперь общая с панелью: операторы могут
добавлять/менять/выключать быстрые ответы на сайте (кнопка «Быстрые ответы»
в тикете), панель создаёт записи в той же схеме `{key, title, text, order,
active}` — они сразу появляются в меню бота, ничего дублировать не нужно.

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
