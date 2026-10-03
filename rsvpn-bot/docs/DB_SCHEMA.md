# Схема базы RS VPN — для расчёта статистики

Документ самодостаточный: по нему можно писать агрегации, не заглядывая в код бота.
MongoDB. Все деньги — целые рубли (кроме крипто-минимума в настройках, он в USD).

---

## 1. `users` — главная коллекция

Один документ на человека. Ключ поиска — `user_data.user_id` (Telegram ID, число).

```jsonc
{
  "_id": ObjectId,

  "user_data": {
    "user_id":     2027845615,          // Telegram ID. Уникален. Основной ключ
    "first_name":  "Коич",
    "last_name":   null,
    "username":    null,                // без @, часто null
    "date_joined": "11.04.2026 21:47:36", // ⚠️ СТРОКА в старых доках, Date в новых
    "referrer":    7996131040,          // кто привёл: user_id, либо "" если сам
    "utm":         "ref_RepublickCheck" // метка источника, "" если нет
  },

  "info": {
    "balance": 0,                       // ОБЫЧНЫЙ баланс, ₽. Им платят за подписку
    "email":   "Не привязана",
    "bonus_multiplier": 0.0,            // персональная надбавка к пополнению (доля), гасится после применения

    "transactions": [ /* см. раздел 2 — ТРИ разных формата */ ],

    "ref_stats": {
      "withdrawable":     540,          // РЕФЕРАЛЬНЫЙ баланс, ₽ (отдельный от info.balance)
      "earned_total":     1200,         // всего заработано с рефералов за всё время
      "turnover_total":   4000,         // сумма пополнений приведённых друзей
      "payments_count":   8,            // сколько раз друзья пополняли
      "referrals":        [111, 222],   // user_id приведённых
      "paying_referrals": [111],        // из них те, кто хоть раз платил
      "method":           [ /* см. 1.1 */ ],  // сохранённые реквизиты
      "method_draft":     null,         // черновик добавляемого способа
      "payout_selected":  "bot_balance", // "bot_balance" | id способа из method[]
      "pending_payout_active": false,   // висит необработанная заявка
      "last_payout_request_at": Date,   // для кулдауна 24 ч
      "payout_history":   [ /* см. 1.2 */ ]
    },

    "gifts":   { "1day": 0, "1month": 2 },   // непотраченные подарочные подписки по коду тарифа
    "support": { "status": "closed", "thread_id": null },
    "bypass_stats": { "purchases": [ /* даты покупок ByPass */ ] },

    "logs_balance": [ /* ⚠️ ЛЕГАСИ старого бота, новый не пишет. См. logs */ ]
  },

  "vpn": {
    "period":    1,                     // ⚠️ НЕ длина подписки, а ТАРИФ продления в днях: 1|30|90|1095
    "uuid":      "33ed9ecd-…",          // id подписки в панели Remnawave
    "shortUuid": "uwrh5dof4m7ke4oh",    // "" = подписки никогда не было
    "createdAt": Date,
    "expireAt":  Date,                  // когда закончится. active := shortUuid != "" AND expireAt > now
    "hwidDeviceLimit": 2,               // сколько устройств разрешено (2 бесплатно, дальше платно)
    "trafficLimitBytes": 0,             // 0 = без лимита

    "extraDevices": [ /* см. 1.3 — платные пакеты устройств */ ],

    "bypass_uuid": "", "bypass_shortUuid": "", "bypass_expireAt": Date,
    "bypass_trafficLimitBytes": "",     // ByPass — вторая подписка через другой сквад

    "activeInternalSquads": ["727b…", "c4ba…"],  // сервера в панели
    "activeInternalSquads_prev": [ … ],          // что было до перевода
    "squad_number": 4,                  // номер в ротации при выдаче

    "notified":    { "1d": true, "expired": true },   // какие напоминания уже слали
    "notified_at": { "1d": Date },                    // когда
    "in_lifeline": false,               // переведён на запасной сервер после истечения
    "lifeline_at": Date,
    "orig_squads": [ … ],               // куда вернуть при продлении

    "fingerprintUpdatedAt": Date, "fingerprintVersion": 1
  },

  "growth": { /* см. раздел 3 — ПРОИЗВОДНЫЕ поля, пересчитываются раз в час */ },
  "campaigns": { /* см. раздел 4 — флаги «это письмо уже слали» */ },
  "logs": [ /* см. раздел 5 — журнал действий, обрезан до 350 записей */ ],
  "growth_history": [ { "from": "new_trial_d0", "to": "new_trial_d1", "changed_at": Date } ]
}
```

### 1.1 `info.ref_stats.method[]` — реквизиты вывода

```jsonc
{
  "id":    "a1b2c3d4",                  // локальный id, на него ссылается payout_selected
  "type":  "sbp",                       // "sbp" | "mir" | "crypto"
  "data":  { "fio": "…", "phone": "+7…", "bank": "Сбербанк" },   // для sbp
           // mir:    { "fio": "…", "card": "2200…" }
           // crypto: { "wallet": "T…" }
  "created_at": Date
}
```

### 1.2 `info.ref_stats.payout_history[]` — что решили по заявке

```jsonc
{ "action": "balance" | "paid" | "reject",  // на баланс бота | выведено наружу | отказ
  "amount": 540, "admin_id": 1, "reason": "data", "at": Date }
```

### 1.3 `vpn.extraDevices[]` — платные пакеты устройств

```jsonc
{ "id": "uuid4", "devices": 1, "price": 75,
  "nextChargeAt": Date,   // списание раз в 30 дней
  "active": true }
```

---

## 2. `info.transactions[]` — ⚠️ ГЛАВНАЯ ЛОВУШКА

В одном массиве лежат **три формата** — база живёт с 2024 года и переезжала.
Любая агрегация по деньгам обязана понимать все три.

**A. Старый (список), самый массовый в исторических данных:**
```jsonc
[ 100, ISODate("2026-05-01T10:00:00Z"), "Пополнение (cardlink), бонус 20₽" ]
//  ^сумма  ^дата                        ^описание
```

**B. Новый (словарь) — так пишет текущий бот:**
```jsonc
{ "amount": -3, "dt": Date, "description": "Продление подписки «Ежедневная»",
  "meta": { … } }   // meta есть не всегда
```

**C. Промо/легаси-словарь — ДРУГИЕ ИМЕНА ПОЛЕЙ:**
```jsonc
{ "type": "promo_balance", "amount": 12, "code": "WELCOME",
  "created_at": Date, "comment": "Активация промокода WELCOME" }
//  ^дата в created_at, а не dt;  текст в comment, а не description
```

Иногда встречается обёртка `[ {…} ]` — список из одного словаря.

### Правила разбора

```
сумма    = tx[0]              если список
           tx.amount          если словарь
дата     = tx[1] | tx.dt | tx.created_at
описание = tx[2] | tx.description | tx.type | tx.comment
```

**Что считать пополнением (деньги от клиента):**
```
amount > 0 И описание (в нижнем регистре) содержит "пополнение" ИЛИ "topup"
```
Это же правило использует сам бот для `growth.has_topup`.

**Важно:** промокоды, реферальные начисления и бонусы кампаний — это тоже
положительные транзакции, но они **НЕ выручка**, а расход маркетинга. Их описания:

| Описание содержит | Смысл | Деньги |
|---|---|---|
| `Пополнение` / `topup` | клиент занёс деньги | **выручка** |
| `Активация промокода` / `type: promo_balance` | промокод | расход |
| `Реферальное начисление` | процент пригласившему | расход |
| `Бонус за возвращение` | кампания win-back | расход |
| `Бонус за ответ на опрос` | опрос оттока | расход |
| `Покупка подписки` (отриц.) | списание за тариф | потребление |
| `Продление подписки` (отриц.) | автопродление | потребление |
| `Плата за устройства` (отриц.) | доп. устройства | потребление |
| `Возврат:` | откат неудачной операции | компенсация |

Сумма пополнения в описании **не равна** зачисленному: `"Пополнение (tribute), бонус 30₽"`
означает, что клиент заплатил `amount − 30`. Для точной выручки берите коллекцию
`payments` (раздел 6), а `transactions` — для баланса и потребления.

---

## 3. `growth` — производный снимок, НЕ первоисточник

Пересчитывается фоновой задачей раз в час по `user_data`, `info`, `vpn`.
Для точной статистики считайте сами из сырых полей — `growth` может отставать
(смотрите `segment_updated_at`).

```jsonc
{
  "segment": "trial",                  // см. таблицу ниже
  "segment_updated_at": Date,          // ⚠️ если старше суток — задача не работала

  "ab_group":       "bonus_15",        // "control"|"bonus_30"|"bonus_15"|"used"|null
  "trial_ab_group": "trial_control",   // назначается один раз

  "has_sub":   true,                   // shortUuid не пустой
  "is_active": false,                  // подписка работает прямо сейчас
  "joined_at": Date, "expire_at": Date,
  "days_since_join": 118,
  "days_to_expire": null,              // null, если уже истекла
  "hours_to_expire": null,
  "days_since_expired": 58,            // null, если активна

  "has_topup":     false,              // хоть раз платил живыми деньгами
  "topups_count":  0,
  "topups_total":  0,
  "first_topup_at": null, "last_topup_at": null,

  "balance": 3,                        // копия info.balance на момент пересчёта

  "trial_claimed_at": Date,            // брал бесплатный период
  "trial_reset_at":   Date,            // админ разрешил взять заново
  "blocked_bot": true, "blocked_at": Date   // заблокировал бота, рассылки не идут
}
```

### Сегменты (`growth.segment`)

| Код | Смысл | Подписка активна |
|---|---|---|
| `new_trial_d0` … `new_trial_d3` | новичок на бесплатном периоде, день 0–3 | да |
| `new_trial_d2_hot`, `new_trial_d3_hot` | у него осталось ≤6 ч / ≤2 ч | да |
| `trial` | бесплатный период кончился, не заплатил | нет |
| `active_no_topup` | активен, но ни разу не платил | да |
| `first_payment_active` | активен, 1 пополнение | да |
| `active_paid` | активен, 2+ пополнений | да |
| `expiring_3d` | платящий, истекает в ближайшие 3 дня | да |
| `expired_1d` / `_3d` / `_7d` / `_14d` / `_21d` / `_30d` | платил, истекла N дней назад | нет |
| `churned_45d` / `_60d` / `_90d` / `churned_dead` | ушёл 31–45 / 46–60 / 61–90 / 90+ дней назад | нет |
| `inactive_no_sub` | подписки не было вовсе | нет |

Правило отнесения (приоритет сверху вниз) — в порядке этой таблицы, первый
подошедший выигрывает. `expired_*` — только для тех, у кого `has_topup = true`;
неплатившие после триала попадают в `trial`.

---

## 4. `campaigns` — флаги автоматических писем

Плоский словарь `<код>_sent: Date`. Наличие ключа = письмо уже отправлено,
второй раз не уйдёт. Полезно для воронок «получил письмо → заплатил».

```jsonc
{
  "d0_sent": Date, "d1_sent": Date, "d2_sent": Date, "d3_hot_sent": Date,  // ⚠️ старые имена
  "trial_d0_sent": Date, "trial_d1_sent": Date, …,                          // новые имена
  "expired_3d_sent": Date, "expired_7d_sent": Date, "churned_45d_sent": Date,
  "trial_back_7d_sent": Date, "trial_back_30d_s2_sent": Date,

  "converted_from": "bonus_30",       // A/B-группа, в которой человек заплатил
  "converted_at": Date, "converted_amount": 100,
  "expired_converted_from": "expired_7d",   // из какого сегмента вернулся
  "expired_converted_at": Date, "expired_converted_amount": 150
}
```

⚠️ **В базе сосуществуют два набора имён**: `d0_sent` (старый бот) и `trial_d0_sent`
(новый), `expired_14d_s1_sent` (старый) и `expired_14d_sent` (новый),
`trial_7d_s1_sent` (старый) и `trial_back_7d_sent` (новый). Считая охват кампаний,
учитывайте оба.

---

## 5. `logs[]` — журнал действий

⚠️ **Обрезан до последних 350 записей** — для длинной истории непригоден.

```jsonc
{ "action": "Действие пользователя", "details": "menu:bypass",
  "timestamp": "26.05.2026 06:45:27" }   // ⚠️ СТРОКА "%d.%m.%Y %H:%M:%S", не Date
```

Значения `action`: `"Действие пользователя"` (нажатие/команда, `details` = сырой
callback_data), `"Автоматическое действие"` (списания и начисления без участия
человека), `"Списание"`, `"Начисление"`, `"Пользователь зарегестрировался"`.

Текст сообщений пользователя намеренно не пишется — в поддержку присылают
почту и номера карт.

---

## 6. Остальные коллекции

### `payments` — источник правды по выручке
```jsonc
{ "txid": "уникальный id платежа", "user_id": 123, "amount": 100,
  "provider": "cardlink" | "tribute" | "tribute_eu" | "wata" | "heleket" |
              "severpay" | "severpay_web" | "cloudpayments",
  "status": "new" | "paid" | "failed", "payload": {…},
  "created_at": Date, "updated_at": Date }
```
`amount` — сколько клиент реально заплатил, **без** бонусов. Считайте выручку отсюда.

### `plans` — тарифы
```jsonc
{ "code": "1month", "title": "1 месяц", "days": 30, "price": 150, "order": 20,
  "gift_type": "1day", "gift_count": 1, "enabled": true }
```
Текущие: `1day`/1 день/6₽, `1month`/30/150₽, `3month`/90/375₽, `3year`/1095/3000₽.
Цены меняются из админки — не зашивайте их в запросы, читайте отсюда.

### `promo_codes` / `promo_usages`
```jsonc
// promo_codes
{ "code": "WELCOME", "reward_type": "balance" | "traffic_gb", "reward_value": 12,
  "max_uses": 100, "used_count": 37, "is_active": true,
  "created_at": Date, "expires_at": Date | null }
// promo_usages — по одной записи на активацию
{ "promo_id": ObjectId, "user_id": 123, "code": "WELCOME",
  "reward_type": "balance", "reward_value": 12, "created_at": Date }
```

### `churn_surveys` — опрос «почему не продлил»
```jsonc
{ "user_id": 123, "campaign": "expired_7d_paid_v1",
  "reason": "too_expensive" | "too_cheap_no_trust" | "not_needed_now" |
            "problems" | "found_other" | "hard_to_use" | "forgot",
  "reward_rub": 8, "created_at": Date, "balance_before": 50 }
```

### `gifts` — подарочные подписки
```jsonc
{ "gift_id": "hex16", "plan_code": "1month", "from_user_id": 123,
  "is_accepted": false, "accepted_by": 456, "created_at": Date }
```

### `campaign_runs` — прогоны автокампаний
```jsonc
{ "step": "expired_3d", "created_at": Date,
  "matched": 400, "sent": 380, "failed": 20, "credited": 4500 }
```

### `broadcasts` — ручные рассылки
```jsonc
{ "_id": "adminid-timestamp", "audience": "expired", "text": "…",
  "recipients": [123, 456], "position": 7400, "sent": 6236, "failed": 1164,
  "status": "running" | "done" | "stopped" | "interrupted" | "failed",
  "started_at": Date, "updated_at": Date, "finished_at": Date }
```

### `job_runs` — когда что из фонового отработало
```jsonc
{ "_id": "renewal" | "campaigns" | "devices" | "panel_webhook" | "expiry_sent",
  "at": Date, "info": { "checked": 120, "renewed": 8, "no_funds": 3 } }
```

### `bot_settings` — все бизнес-параметры
```jsonc
{ "_id": "payout.min_withdraw", "value": 500, "updated_at": Date, "admin_id": 1 }
```
Цены, проценты бонусов, скидки по аудиториям, минимумы вывода, тумблеры кампаний.
Ключи: `bonus.topup_rate`, `bonus.ref_rate`, `discount.expired`, `price.trial_days`,
`payout.min_withdraw`, `campaign.*_enabled` и т. д.

---

## 7. Что важно знать, считая метрики

1. **Даты бывают строками.** `user_data.date_joined` и `logs[].timestamp` — формат
   `"%d.%m.%Y %H:%M:%S"` (московское время) в старых документах и `Date` в новых.
   `vpn.expireAt` бывает naive и aware. Нормализуйте перед сравнением.

2. **Активность подписки не хранится флагом.** Считайте:
   `vpn.shortUuid != "" AND vpn.expireAt > now`. Поле `growth.is_active` — снимок
   часовой давности.

3. **Два баланса.** `info.balance` — обычный, тратится на подписку.
   `info.ref_stats.withdrawable` — реферальный, выводится наружу. Не складывайте.

4. **Выручка ≠ сумма положительных транзакций.** Бонусы, промокоды и реферальные
   начисления — тоже плюс на балансе, но это расход. Выручка — `payments` со
   `status: "paid"`, либо транзакции с «Пополнение» **минус** бонус из описания.

5. **`vpn.period` — тариф продления, а не срок текущей подписки.** У пришедших
   через бесплатный период там встречается `3` — значения без тарифа (историческая
   ошибка, чинится на лету при продлении).

6. **Дубли пользователей невозможны**, `user_data.user_id` уникален. Но записи
   «Пользователь зарегестрировался» в `logs` повторяются — это /start, а не
   регистрация.

7. **Заблокировавшие бота** помечены `growth.blocked_bot: true`. Их стоит исключать
   из знаменателя при расчёте доставляемости, но не из выручки.

8. **ByPass — вторая подписка того же человека** (`vpn.bypass_*`), отдельный
   пользователь в панели Remnawave, но тот же документ в базе.
