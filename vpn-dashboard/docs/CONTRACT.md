# API Contract — VPN Analytics Dashboard

Single source of truth for backend routers and the frontend API client.
All endpoints live under `/api`, require the admin JWT cookie (except
`/api/auth/*` and `/api/health`), and return JSON.

## Conventions

- **Period filter**: every aggregation endpoint accepts `?from=<ISO>&to=<ISO>`
  (both optional; omitted `from` = all time, omitted `to` = now). Parsed by
  `app.routers.deps.get_period` → `Period` dataclass with `.match()` for
  `$match` stages and `.previous()` for comparisons.
- **Granularity**: endpoints with time series accept
  `?granularity=day|week|month` (default `day`). Use `app.agg.bucket_expr`.
- **Money**: RUB floats rounded to 2 decimals (`app.agg.r2`).
  *Net revenue* of a top-up = `amount - bonus` (`app.agg.NET_AMOUNT`) — the
  bonus part is money we gave away, not received.
- **Dates in responses**: ISO-8601 strings (UTC). FastAPI serializes
  `datetime` automatically; for `$dateTrunc` buckets return the datetime as-is.
- **Aggregations run in MongoDB** against `transactions_flat` / `users_flat`
  (constants `TX_FLAT`, `USERS_FLAT` in `app.db`). Final light assembly
  (medians over a few thousand values, sankey folding) may be Python-side.
  Never aggregate over the raw `users` collection except the explicitly
  noted user-card endpoint.
- **Caching**: heavy report endpoints use `@cached(ttl=120, prefix="...")`
  from `app.cache` — the decorator caches by kwargs, so cached helper
  functions must take only JSON-serializable kwargs (pass `from`/`to` as ISO
  strings, NOT the db handle; get the db inside the helper via
  `app.db.get_db()`). Live counters (overview summary) are NOT cached, or
  cached ≤ 15s.
- **PII**: payout requisites (card / phone / email / name) must pass through
  `app.masking.mask_payout_entry` before leaving the API.
- Routers declare `router = APIRouter(prefix="/<name>", tags=["<name>"])`;
  auth is enforced centrally in `main.py` — do not add per-route guards.

## Collection schemas

### `transactions_flat` (constant `TX_FLAT`)

```
_id: str            # deterministic sha1
user_id: int        # telegram id
username: str|null
direction: "credit"|"debit"
dt: datetime|null   # BEWARE: can be null for broken legacy rows — always
                    # $match on dt when bucketing by time
amount: float       # debits are stored positive
kind: str           # credit: topup|ref_income|promo|bonus|purchase|gift|unknown
                    # debit:  renewal|device|bypass|gift|other
source: str|null    # topups only: cardlink|severpay|wata|heleket|cards_ru|card|stars|crypto|...
bonus: float        # gifted part of a top-up amount
promo_code: str|null
ref_meta: dict|null
payment_id: str|null
desc: str|null
product: str|null   # debits only (e.g. "Pro")
etl_at: datetime
```

### `users_flat` (constant `USERS_FLAT`)

```
_id: int                    # telegram id
username, username_lower: str|null
joined_at: datetime|null
segment: str|null           # active_paid, expiring_3d, new_trial_d2, expired, ...
segment_history: [{segment: str, dt: datetime}]   # sorted asc
days_to_expire: float|null
sub_until: datetime|null
balance: float
referrer_id: int|str|null
ref_stats: {turnover_total: float, earned_total: float, referrals: int,
            paying_referrals: int, payout_pending: float,
            payout_history: [raw dicts — MASK before returning]}
campaigns: dict|list|null   # raw campaign info incl. converted_from/converted_amount
extra_devices: [{active: bool, dt: datetime|null, name: str|null}]
extra_devices_active: int
preferred_client: str|null  # happ | INCY | ...
first_topup_at: datetime|null
topup_total: float, topup_count: int, bonus_total: float
first_sub_at: datetime|null       # first renewal debit
second_sub_at: datetime|null      # second renewal debit
last_renewal_at: datetime|null, renewals_count: int
spend_total: float
bypass_count: int, bypass_total: float
device_spend_total: float
promo_activations: [{code: str, dt: datetime|null, amount: float}]
last_tx_at: datetime|null
etl_at: datetime
```

### `payments_flat` (constant `PAYMENTS_FLAT`)

Mirror of the provider webhook log (`payments_webhook`), one doc per event:
```
_id: str (txid), user_id: int|null, dt: datetime|null,
amount: float, commission: float, source: str|null,
status: "paid"|"failed"|"pending"|"other", processed: bool|null,
tx_type: str|null, etl_at: datetime
```
NOT part of revenue (the same top-ups already live in transactions_flat) —
used for provider health, commissions and reconciliation.

### Other collections

- `activity_stats`: `{_id: "heatmap", cells: [{dow: 0-6, hour: 0-23, count}], computed_at}`
- `alerts`: `{key, severity: critical|warning|info, title, details: {}, created_at}`
- `etl_state`: `{_id: "etl", users, tx_rows, unparsed, took_ms, ran_at}`

---

## Endpoints by router

### `overview.py` — prefix `/overview`

`GET /overview/summary` (no cache) →
```json
{
  "online_users": 123 | null,          // Remnawave; null when panel is down
  "remnawave_available": true,
  "active_subs": 456,                  // users_flat: segment starts with "active" OR days_to_expire > 0
  "revenue_today": 1234.5, "revenue_7d": 0.0, "revenue_30d": 0.0,   // NET topups
  "registrations_today": 12,
  "sparklines": {                       // last 30 days, daily, zero-filled
    "revenue":       [{"date": "2024-05-01", "value": 100.0}, ...],
    "registrations": [{"date": "...", "value": 3}, ...],
    "online":        [],                // empty (no history source yet)
    "active_subs":   []
  }
}
```

`GET /overview/providers-status` (cache ≤ 60s) →
```json
{"providers": [{
  "source": "cardlink",
  "last_payment_at": "ISO|null",
  "payments_24h": 10, "payments_7d": 80,
  "median_gap_hours": 1.4,
  "silence_hours": 0.5,
  "threshold_hours": 6.0,
  "status": "ok" | "warning" | "down",  // warning: silence > threshold/2; down: silence > threshold
  "via": "webhook" | "balance",         // data source: payments_flat vs transactions_flat
  "commission_30d": 123.4 | null,       // provider fees, webhook sources only
  "failed_24h": 2 | null
}]}
```
Providers = distinct non-null sources over last 30 days; the webhook stream
(`payments_flat`, status=paid) is preferred per source, balance credits are
the fallback. Threshold = `max(settings.provider_silence_hours, 4 * median_gap_hours)`.

`GET /overview/events/recent?limit=50` → `{"events": [...last WS events from hub._recent...]}` — read `app.ws.hub` ring buffer (add a public `recent()` accessor usage: `list(hub._recent)` is acceptable).

### `revenue.py` — prefix `/revenue`

`GET /revenue/timeseries?granularity=&from=&to=` (cached 120s) →
```json
{"series": [{"bucket": "2024-05-01T00:00:00+00:00", "source": "cardlink", "revenue": 100.0, "count": 3}],
 "sources": ["cardlink", "severpay"]}
```
Stacked-bar data: NET topup revenue grouped by (bucket, source); null source → `"other"`.

`GET /revenue/kpis?from=&to=` (cached 120s) →
```json
{"mrr": 0.0,                    // renewal debits over trailing 30d from period end
 "device_mrr": 0.0,             // device debits over trailing 30d
 "arpu": 0.0,                   // net topup revenue in period / distinct paying users
 "avg_check": 0.0, "median_check": 0.0,   // over NET topup amounts in period
 "paying_users": 0, "payments_count": 0,
 "revenue": 0.0,                // net topups in period
 "prev_revenue": 0.0, "revenue_change_pct": 12.3|null}   // vs Period.previous()
```

`GET /revenue/ltv-cohorts` (cached 300s) →
```json
{"cohorts": [{"cohort": "2024-01", "users": 100, "revenue": 5000.0, "ltv": 50.0}]}
```
Cohort = month of `users_flat.joined_at`; revenue = lifetime NET topups of those users (join via `$lookup`-free approach: aggregate topups by user, map joined-month in Python).

`GET /revenue/by-type?from=&to=` (cached 120s) →
```json
{"types": [{"kind": "renewal", "label": "Подписки", "amount": 0.0, "count": 0},
           {"kind": "device", ...}, {"kind": "bypass", ...}, {"kind": "gift", ...}, {"kind": "other", ...}]}
```
From debit rows (what the money was spent on).

`GET /revenue/bonus-share?granularity=&from=&to=` (cached 120s) →
```json
{"series": [{"bucket": "...", "net": 100.0, "bonus": 20.0, "promo": 30.0}],
 "totals": {"net": 0.0, "bonus": 0.0, "promo": 0.0, "share_pct": 12.5}}
```
`bonus` = sum of bonus in topups; `promo` = sum of kind=promo credits; share = (bonus+promo)/(net+bonus+promo)*100 (0 when denominator 0).

`GET /revenue/compare` (cached 120s) → month-to-date vs previous month same span + week-over-week:
```json
{"month": {"current": 0.0, "previous": 0.0, "change_pct": null},
 "week":  {"current": 0.0, "previous": 0.0, "change_pct": null},
 "wow": [{"week": "2024-W18", "revenue": 0.0}]}    // last 12 ISO weeks
```

### `users.py` — prefix `/users`

`GET /users/segments` (cached 60s) →
```json
{"segments": [{"segment": "active_paid", "count": 100}], "total": 500}
```

`GET /users/segment-flows?from=&to=` (cached 300s) →
```json
{"nodes": ["active_paid", "expiring_3d", "expired"],
 "links": [{"source": "expiring_3d", "target": "active_paid", "value": 12}]}
```
Fold consecutive pairs of `segment_history` where the *target* entry's dt is in
the period. Skip self-transitions. Cap nodes at the 12 most frequent.

`GET /users/retention-cohorts?months=12` (cached 300s) →
```json
{"cohorts": [{"cohort": "2024-01", "size": 100,
              "retention": [100.0, 40.0, 25.0, ...]}]}   // % with a renewal debit in month offset 0,1,2...
```

`GET /users/funnel?from=&to=` (cached 300s) — users with `joined_at` in period:
```json
{"steps": [
  {"step": "registration", "users": 1000, "conversion_pct": 100.0, "median_hours_from_prev": null},
  {"step": "first_topup", "users": 300, "conversion_pct": 30.0, "median_hours_from_prev": 5.2},
  {"step": "first_sub", "users": 250, "conversion_pct": 83.3, "median_hours_from_prev": 0.5},
  {"step": "renewal", "users": 120, "conversion_pct": 48.0, "median_hours_from_prev": 720.0}
]}
```
conversion_pct is step-over-previous-step. Steps use `first_topup_at`,
`first_sub_at`, `second_sub_at`.

`GET /users/churn` (cached 300s) →
```json
{"monthly": [{"month": "2024-01", "churned": 10, "active_start": 100, "churn_rate_pct": 10.0}],
 "at_risk": [{"user_id": 1, "username": "u", "segment": "expiring_3d",
              "days_to_expire": 2.0, "balance": 0.0, "renewals_count": 3}],
 "at_risk_count": 25}
```
Churned in month M = users whose segment_history has a transition INTO a
segment containing "expired" during M. active_start ≈ users with joined_at < M
and not yet churned (documented approximation). at_risk = `days_to_expire < 3`
and `balance < 150` and segment not containing "expired", sorted by
days_to_expire, limit 50.

`GET /users/search?q=` → by exact tg id or username substring (users_flat, limit 20):
```json
{"results": [{"user_id": 1, "username": "u", "segment": "...", "joined_at": "...",
              "topup_total": 0.0, "last_tx_at": "..."}]}
```

`GET /users/{user_id}/card` → full card (this endpoint MAY read the raw
`users` collection for `logs` tail; everything else from flat):
```json
{"user": {...users_flat doc, payout_history masked...},
 "transactions": [ ...transactions_flat rows for user, sorted dt desc, limit 200,
                   each {dt, direction, kind, amount, source, bonus, promo_code, desc}...],
 "referrals": [{"user_id": 2, "username": "x", "topup_total": 0.0, "joined_at": "..."}],  // users_flat where referrer_id == user_id, limit 50
 "recent_logs": [{"dt": "...", "text": "..."}]}   // last 30 parseable entries of raw doc's logs; free-text run through mask_free_text
```
404 if user not found.

### `referrals.py` — prefix `/referrals`

`GET /referrals/top?by=turnover|earned|paying&limit=20` (cached 120s) →
```json
{"referrers": [{"user_id": 1, "username": "u",
                "turnover_total": 0.0, "earned_total": 0.0,
                "referrals": 10, "paying_referrals": 3, "conversion_pct": 30.0,
                "payout_pending": 0.0}]}
```
From `users_flat.ref_stats`, sorted by chosen field, only rows with referrals > 0.

`GET /referrals/summary` (cached 120s) →
```json
{"total_referrers": 0, "total_referrals": 0, "total_paying": 0,
 "conversion_pct": 0.0, "payout_pending_total": 0.0,
 "earned_total": 0.0, "turnover_total": 0.0}
```

`GET /referrals/payouts` (cached 120s) →
```json
{"pending_total": 0.0,
 "queue": [{"user_id": 1, "username": "u", "payout_pending": 500.0}],    // payout_pending >= 100, desc, limit 50
 "history": [{"user_id": 1, "username": "u", ...masked payout entry...}]} // flattened payout_history entries, masked, latest 50
```

`GET /referrals/timeseries?granularity=&from=&to=` (cached 120s) →
```json
{"series": [{"bucket": "...", "amount": 0.0, "count": 0}]}   // kind=ref_income credits
```

### `promos.py` — prefix `/promos` (also covers campaigns & broadcasts)

`GET /promos/table?from=&to=` (cached 120s) →
```json
{"promos": [{"code": "WELCOME", "activations": 100, "unique_users": 90,
             "amount_total": 5000.0, "repeat_activations": 10,
             "max_by_one_user": 7, "abuse_suspects": 2, "last_used_at": "..."}]}
```
kind=promo rows grouped by promo_code; `repeat_activations` = activations - unique_users; `abuse_suspects` = users with ≥2 activations of that code; sorted by activations desc.

`GET /promos/abuse?from=&to=` (cached 120s) →
```json
{"cases": [{"user_id": 1, "username": "u", "code": "X", "count": 7,
            "amount_total": 700.0, "first_at": "...", "last_at": "..."}]}
```
Same-user-same-code count ≥ 2, sorted by count desc, limit 100.

`GET /promos/campaigns` (cached 300s) →
```json
{"campaigns": [{"campaign": "tg_ads_may", "users": 100, "converted": 30,
                "conversion_pct": 30.0, "converted_amount": 4500.0}]}
```
Fold `users_flat.campaigns` in Python (dict or list; look for
`converted_from` as campaign name and `converted_amount`); group by campaign.
A user "converted" when converted_amount > 0 OR topup_total > 0.

`GET /promos/broadcast-impact?hours=24` (cached 300s) — effectiveness of broadcasts:
if a `broadcasts` collection exists (fields best-effort: dt/created_at, name/title),
for each of the latest 20 broadcasts count topups & net revenue within N hours
after it vs the same window before:
```json
{"available": true,
 "broadcasts": [{"name": "may promo", "at": "...", "topups_after": 10,
                 "revenue_after": 1000.0, "topups_before": 4,
                 "revenue_before": 300.0, "uplift_pct": 233.3}]}
```
`{"available": false, "broadcasts": []}` when the collection is missing.

### `product.py` — prefix `/product`

`GET /product/devices` (cached 120s) →
```json
{"active_slots": 0, "users_with_devices": 0,
 "device_mrr": 0.0,                  // device debits trailing 30d
 "churned_slots": 0,                 // extra_devices with active: false
 "revenue_total": 0.0,               // all-time device debits
 "timeseries": [{"bucket": "...", "amount": 0.0, "count": 0}]}   // monthly device debits, last 12 months
```

`GET /product/bypass?from=&to=` (cached 120s) →
```json
{"purchases": 0, "revenue": 0.0, "unique_buyers": 0,
 "repeat_buyers": 0, "repeat_rate_pct": 0.0,       // buyers with ≥2 purchases in period
 "avg_days_between": 12.5|null,                    // mean gap between repeat purchases
 "top_consumers": [{"user_id": 1, "username": "u", "purchases": 5, "amount": 250.0}],  // limit 15
 "timeseries": [{"bucket": "...", "amount": 0.0, "count": 0}]}
```

`GET /product/clients` (cached 300s) →
```json
{"clients": [{"client": "happ", "count": 100}, {"client": "INCY", "count": 50},
             {"client": "unknown", "count": 10}]}
```

### `infra.py` — prefix `/infra`

`GET /infra/nodes` (no cache — client caches 15s internally) →
```json
{"available": true, "nodes": [{...normalized node dict from RemnawaveClient.get_nodes()...}],
 "online_total": 123, "realtime": [{...get_realtime_usage() items...}] | null}
```
`{"available": false, "nodes": [], ...}` when panel unreachable. Use
`app.remnawave.get_remnawave()`.

`GET /infra/heatmap` (cached 300s) → from `activity_stats`:
```json
{"cells": [{"dow": 0, "hour": 0, "count": 5}], "computed_at": "ISO|null"}
```

`GET /infra/peak-hours` (cached 300s) →
```json
{"hours": [{"hour": 0, "count": 12}, ... 24 entries zero-filled ...]}
```
Sum heatmap cells over weekdays.

### `experiments.py` — prefix `/experiments`

`GET /experiments/ab?experiment=ab_group|trial_ab_group&joined_from=&joined_to=` (cached 120s) →
```json
{"experiment": "ab_group",
 "control": "A",                       // largest group = baseline
 "groups": [{
   "group": "A", "users": 500,
   "paying": 150, "conversion_pct": 30.0,
   "arpu": 250.0,                      // lifetime net topups / users
   "avg_ltv_paying": 833.3,            // topups / paying
   "renewal_share_pct": 40.0,          // paying with >= 2 renewals
   "vs_control": {"z": 2.1, "p_value": 0.036, "significant": true,
                  "conversion_diff_pp": 5.2} | null   // null for control itself
 }],
 "untagged_users": 120}
```
Groups from `users_flat.ab_group` / `trial_ab_group`; optional joined_at
window. Two-sided two-proportion z-test on conversion vs control;
significant = p < 0.05.

`GET /experiments/opportunities` (cached 300s) →
```json
{"median_renewal": 199.0, "median_check": 299.0,
 "opportunities": [{
   "key": "expiring_no_balance",
   "title": "Истекают без денег на балансе",
   "description": "...",
   "users": 42,
   "potential_rub": 8358.0,            // users * relevant median * assumed conversion
   "assumption": "конверсия напоминания 40%"
 }]}
```
Keys: expiring_no_balance, winback_expired, trial_no_topup, bypass_upsell,
dormant_balance. Estimates are labeled heuristics, not promises.

### `alerts_router.py` — prefix `/alerts`

`GET /alerts/recent?limit=50` → `{"alerts": [{key, severity, title, details, created_at}]}` sorted desc.
`POST /alerts/test` → fires a test telegram message; `{"sent": true|false}`.
`GET /alerts/status` → `{"telegram_configured": bool, "checks": ["provider_silence", "revenue_anomaly", "promo_abuse", "error_burst"], "cooldown_hours": 6.0}`.

---

## Frontend consumption notes

- Base URL: same origin, `/api/...` (Next.js rewrites proxy to backend).
- Auth: `POST /api/auth/login {username, password}` → httpOnly cookie; 401 from
  any endpoint → redirect to `/login`.
- WS: `new WebSocket(wss://host/api/ws/events)`; messages
  `{"type": "event", "event": "topup|registration|promo|ref_income|purchase|segment|alert", "replay"?: true, ...payload, id, ts}`.
- Period filter is passed as `from`/`to` ISO strings on every aggregation call.
