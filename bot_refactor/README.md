# Рефакторинг RS VPN бота: единая админка + конец копипасты

Готовый каркас, который кладётся рядом с текущим кодом и внедряется по шагам —
переписывать всё сразу не нужно, бот продолжает работать на каждом шаге.

```
core/settings.py   — все «крутилки» бота: цены, проценты, флаги, ссылки, тексты
core/plans.py      — тарифы как записи в БД вместо if-цепочек
core/ui.py         — повторяющиеся блоки текста и отрисовка экранов
core/guards.py     — фильтр Feature() и режим техработ
admin/entities.py  — описание списочных сущностей (тарифы, ответы, промокоды)
admin/panel.py     — сама админка: универсальные хендлеры на всё вышеперечисленное
admin/stats.py     — статистика (сейчас посчитана в двух местах по-разному)
```

## В чём была проблема

1. **Цены зашиты в коде и продублированы.** `6 / 150 / 375 / 3000` встречаются в
   `start.py` в строках 59–72, 366–368, 432, 1249, 2775–2777 и в inline-подарках.
   Поменять цену = 8 правок + деплой.
2. **Один хендлер на всё.** `menu_callback` — примерно 2200 строк из 40 блоков
   `if call.data.endswith(...)`, причём каждый блок начинается с одинакового
   текста профиля и заканчивается одинаковым `try: edit_message_media / except`.
3. **Админка дублирует сама себя.** Статистика написана дважды (`admin_panel` и
   ветка `admin:main`), и во второй копии сегменты уже потерялись. Быстрые
   ответы и промокоды редактируются двумя почти одинаковыми наборами хендлеров.
4. **Ничего нельзя выключить без деплоя** — ни продление, ни ByPass, ни бонус
   за пополнение.

## Идея решения

Всё, что меняется руками, становится **данными**, а не кодом:

| Что | Где хранится | Где меняется |
|---|---|---|
| Цены тарифов, дни, подарки | коллекция `plans` | `/admin → 💰 Тарифы` |
| Проценты, лимиты, бонусы, ссылки, тексты | `bot_settings` (один документ) | `/admin → ⚙️ Настройки` |
| Включение/выключение функций | там же, тип `bool` | одна кнопка-тумблер |
| Быстрые ответы, промокоды | свои коллекции | `/admin`, те же универсальные хендлеры |

Админка **генерируется** из описаний. Новая настройка — одна строка в `SCHEMA`,
никакого нового хендлера. Новая сущность со своим списком — один `EntityAdmin`.

## Установка

1. Скопируйте `core/` и `admin/` в корень проекта бота (рядом с `loader.py`).
2. В `loader.py` ничего менять не нужно — модули берут `db`, `users`,
   `admins_ids` оттуда. Понадобится только коллекция `plans`, она создаётся сама.
3. В `app.py`:

```python
from core.plans import seed_plans
from core.guards import MaintenanceMiddleware
from admin import panel as admin_panel

async def on_startup(dispatcher: Dispatcher):
    await seed_plans()                     # один раз перенесёт текущие цены в БД
    dispatcher.include_router(admin_panel.router)   # ДО старого admin-роутера
    dp.message.middleware(MaintenanceMiddleware())
    dp.callback_query.middleware(MaintenanceMiddleware())
    ...
```

4. Старый `admin.py` пока оставьте: рассылка, опрос оттока и промо-FSM работают
   как работали. Новый роутер перехватывает `/admin` и свои `adm:*` коллбэки,
   старые `admin:*` не трогает.

После этого `/admin` уже показывает статистику, тарифы, настройки и тумблеры —
но бот ещё читает старые константы. Дальше переключаем потребителей.

## Шаг за шагом: как убрать дублирование

### 1. Тарифы

Было (`start.py:366-368`, и ещё в трёх местах):

```python
keyboard.row(types.InlineKeyboardButton(text='1 месяц + 🎁 - 150₽', callback_data='menu:1month:buy_sub', style='success' if user['info']['balance'] >= 150 else 'danger'))
keyboard.row(types.InlineKeyboardButton(text='3 месяца + 🎁 - 375₽', ...))
keyboard.row(types.InlineKeyboardButton(text='3 года + 🎁 - 3000₽', ...))
```

Стало:

```python
from core.plans import all_plans, plan_button_title

for plan in await all_plans():
    keyboard.row(types.InlineKeyboardButton(
        text=await plan_button_title(plan),
        callback_data=f'menu:{plan["code"]}:buy_sub',
        style='success' if user['info']['balance'] >= plan['price'] else 'danger',
    ))
```

Было (`start.py:432`):

```python
price, duration = (6, 1) if duration == '1day' else (150, 30) if duration == '1month' else (375, 90) if duration == '3month' else (3000, 1095)
```

Стало:

```python
plan = await get_plan(call.data.split(':')[1])
if not plan:
    await call.answer('Тариф недоступен', show_alert=True)
    return
price, duration = plan['price'], plan['days']
```

Подарки после покупки (`start.py:528-533`) тоже уходят в данные тарифа:

```python
if plan.get('gift_count'):
    await users.update_one(
        {'user_data.user_id': call.from_user.id},
        {'$inc': {f'info.gifts.{plan["gift_type"]}': plan['gift_count']}},
    )
```

`plan_base_price()` и `devices_monthly_price()` из `utility/utils.py` заменяются
на `price_for_days()` и `devices_monthly_price()` из `core/plans.py` — они берут
цену устройства и бесплатный лимит из настроек.

### 2. Повторяющиеся тексты и экраны

Было — в каждом из 40 блоков:

```python
text = (f'<b>...👤 Профиль</b>\n\n'
        f'<b>...🆔 Идентификатор:</b> <code>{call.from_user.id}</code>\n'
        f'<b>...💰 Баланс:</b> <code>{user['info']['balance']}₽</code>\n'
        f'<b>...👥 Друзей:</b> <code>{len(user['info']['ref_stats']['referrals'])}</code>\n'
        f'<b>...✉️ Почта:</b> <code>{user['info']['email']}</code>\n\n')
...
try:
    new_media = types.InputMediaPhoto(media=types.FSInputFile('img/new_profile.png'), caption=text)
    await bot.edit_message_media(chat_id=..., message_id=..., media=new_media, reply_markup=keyboard.as_markup())
except Exception as e:
    print(f'Ошибка при отправке фото: {e}')
    await call.message.reply(text, reply_markup=keyboard.as_markup())
```

Стало:

```python
from core.ui import profile_header, subscription_lines, add_footer, show_screen

text = profile_header(user) + await subscription_lines(user)
await add_footer(kb, back='menu:sub')
await show_screen(call, text, kb, image='img/new_your_sub.png')
```

`show_screen` сам разбирается, что перед ним — `Message` или `CallbackQuery`,
есть ли в сообщении фото, и делает фолбэк на текст. Это одна функция вместо
примерно 600 строк try/except по всему файлу.

Админ-уведомления в лог-чат (шесть одинаковых блоков) → `notify_admins_chat(bot, text, thread_id=2)`.

### 3. Разрезать гигантский хендлер

Один `menu_callback` с 40 `if` заменяется на роутеры по разделам. aiogram сам
матчит коллбэки — руками парсить строки не нужно:

```python
# handlers/subscription.py
router = Router(name='subscription')

@router.callback_query(F.data == 'menu:sub')
async def show_subscription(call, state): ...

@router.callback_query(F.data.endswith(':buy_sub'), Feature('features.buy_enabled'))
async def buy_subscription(call, state): ...

@router.callback_query(F.data == 'menu:extend', Feature('features.extend_enabled'))
async def extend_subscription(call, state): ...
```

Такие файлы: `profile.py`, `subscription.py`, `devices.py`, `bypass.py`,
`payments.py`, `referrals.py`, `gifts.py`. Каждый — 150–300 строк вместо 3100.
Переносить можно по одному разделу за деплой: пока раздел не перенесён, его
обрабатывает старый хендлер.

Важно: в старом коде `user` загружается в начале хендлера и дальше используется
после `users.update_one(...)` — то есть уже устаревшим (например баланс в тексте
после покупки в `start.py:561`). При переносе берите документ заново или
используйте `find_one_and_update(..., return_document=AFTER)`.

### 4. Флаги функций

`Feature('features.extend_enabled')` в фильтре хендлера + проверка при отрисовке
кнопки:

```python
if await S.flag('features.extend_enabled'):
    kb.row(types.InlineKeyboardButton(text='Продлить подписку', callback_data='menu:extend'))
```

Автосписание в планировщике (`process_subscriptions`) — там же:

```python
if not await S.flag('features.autorenew_enabled'):
    return
```

Кампании в `app.py` — оборачиваются так же, через `campaign.*_enabled`, тогда
рассылку можно выключить, не трогая scheduler.

### 5. Проценты и бонусы в FastApi.py

Было:

```python
BONUS_CUTOFF_UTC = datetime(2026, 8, 1, 3, 0)
bonus_rub = int(amount_rub * 0.20) if now_utc < BONUS_CUTOFF_UTC else 0
if source == 'tribute':
    bonus_rub += int(amount_rub * 0.05)
...
ref_percent = 0.3
```

Стало:

```python
from core.settings import S

bonus_rub = int(amount_rub * await S.rate('bonus.topup_rate')) if await S.flag('bonus.topup_enabled') else 0
if source == 'tribute':
    bonus_rub += int(amount_rub * await S.rate('bonus.tribute_extra_rate'))
...
ref_percent = await S.rate('bonus.ref_rate')
```

Акция «бонус до 1 августа» превращается в тумблер `bonus.topup_enabled`:
включили — идёт, выключили — нет, без правки даты в коде.

## Как это работает под капотом

* Настройки лежат в одном документе `bot_settings/main`, кэш в памяти на 10 сек.
  Поэтому и процесс бота, и процесс FastAPI подхватывают изменение сами —
  перезапуск не нужен, а Mongo дёргается не чаще раза в 10 секунд на процесс.
* Каждое изменение пишется в `bot_settings_audit` (кто, что, было/стало) —
  видно, кто уронил цену в ноль.
* Значение по умолчанию всегда берётся из `SCHEMA`: если ключа нет в БД или
  в него записали мусор, бот работает на дефолте, а не падает.
* `parse_value()` проверяет тип и границы (`min`/`max`), проценты вводятся как
  `20`, а хранятся как `0.2`.

## Что стоит сделать дальше

* Перенести рассылку в тот же реестр: сегменты — это данные (`SEGMENT_LABELS`
  уже словарь), значит кнопки строятся циклом, а `get_audience_user_ids`
  становится одним запросом с фильтром из описания сегмента.
* Рассылка сейчас шлёт сообщения в цикле без пауз и падает на первом же
  флуд-лимите Telegram (`except: failed += 1`). Пауза настраивается ключом
  `campaign.broadcast_delay_ms` — осталось её применить в цикле отправки и
  обрабатывать `TelegramRetryAfter`.
* `loader.py`: в именах коллекций есть пробелы в конце —
  `db['promo_codes ']`, `db['churn_surveys ']`, `db['support_quick_replies ']`.
  Это рабочие, но разные коллекции. Переименование делайте отдельным шагом с
  переносом данных, иначе после чистки имён пропадут промокоды и ответы.
* Транзакции пишутся в двух форматах — списком `[amount, dt, descr]` и словарём.
  Из-за этого `_user_has_topup` разбирает оба. Стоит привести к словарю
  миграцией и упростить.
