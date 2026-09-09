import asyncio, sys

async def main():
    from core.settings import S, INDEX, SCHEMA, parse_value, format_value
    from core.plans import seed_plans, all_plans, get_plan, price_for_days, devices_monthly_price
    from admin.entities import ENTITIES
    from admin.panel import Adm, router
    from admin.stats import build_stats_text
    from aiogram import Dispatcher

    # 1. settings
    assert await S.int('price.device_extra') == 75
    await S.set('price.device_extra', 90, admin_id=1)
    assert await S.int('price.device_extra') == 90
    assert await S.flag('features.extend_enabled') is True
    assert await S.toggle('features.extend_enabled', 1) is False
    assert await S.flag('features.extend_enabled') is False
    await S.reset('features.extend_enabled', 1)
    assert await S.flag('features.extend_enabled') is True
    assert abs(await S.rate('bonus.ref_rate') - 0.30) < 1e-9

    # percent parse/format round-trip
    ok, val, err = parse_value(INDEX['bonus.topup_rate'], '25')
    assert ok and abs(val - 0.25) < 1e-9, (ok, val, err)
    assert format_value(INDEX['bonus.topup_rate'], 0.25) == '25%'
    ok, _, err = parse_value(INDEX['bonus.topup_rate'], 'abc')
    assert not ok and err
    ok, _, err = parse_value(INDEX['pay.min_topup'], '0')
    assert not ok, 'min bound must reject 0'
    ok, val, _ = parse_value(INDEX['price.device_extra'], '120 ₽')
    assert ok and val == 120

    # 2. plans
    await seed_plans()
    plans = await all_plans()
    assert [p['code'] for p in plans] == ['1day', '1month', '3month', '3year'], plans
    assert (await get_plan('3year'))['price'] == 3000
    assert await price_for_days(90) == 375
    assert await devices_monthly_price({'vpn': {'hwidDeviceLimit': 5}}) == 3 * 90

    # 3. entity admin (plans)
    plan_admin = ENTITIES['plan']
    await plan_admin.set_field('1month', 'price', 199)
    from core.plans import invalidate_plans
    assert (await get_plan('1month'))['price'] == 199
    assert await plan_admin.toggle('1day') is False
    assert await plan_admin.move('3year', -1) is True
    assert [p['code'] for p in await all_plans(only_enabled=False)][2] == '3year'
    await plan_admin.create('1week')
    created = await plan_admin.get('1week')
    assert created['price'] == 100 and created['enabled'] is True, created
    await plan_admin.delete('1week')
    assert await plan_admin.get('1week') is None

    qr = ENTITIES['qr']
    await qr.create('how_to_connect')
    assert (await qr.get('how_to_connect'))['active'] is True
    assert await qr.toggle('how_to_connect') is False

    # 4. callback_data length limits
    longest = max(INDEX, key=len)
    packed = Adm(act='fld', a=longest).pack()
    assert len(packed.encode()) <= 64, (packed, len(packed))
    packed2 = Adm(act='eedit', a='plan|gift_count', b='3month').pack()
    assert len(packed2.encode()) <= 64, packed2
    assert Adm.unpack(packed2).b == '3month'

    # 5. router registers cleanly (aiogram validates handler signatures)
    dp = Dispatcher()
    dp.include_router(router)

    # 6. stats builds against empty users collection
    from loader import users
    await users.insert_one({'user_data': {'user_id': 5}, 'info': {'balance': 100,
        'transactions': [[100, None, 'Пополнение (wata)']]},
        'vpn': {'shortUuid': 'abc', 'expireAt': '2099-01-01T00:00:00Z'}, 'growth': {'segment': 'active_paid'}})
    text = await build_stats_text()
    assert 'Активных подписок: <code>1</code>' in text, text
    assert 'Активных подписок с пополнением: <code>1</code>' in text
    assert '🟢 Активные платящие: <code>1</code>' in text

    print('ALL CHECKS PASSED')
    print(f'  настроек в схеме: {len(INDEX)} в {len(SCHEMA)} разделах')
    print(f'  сущностей админки: {list(ENTITIES)}')
    print(f'  самый длинный callback_data: {len(packed.encode())} байт из 64')

asyncio.run(main())
