from app.settings.schema import INDEX, format_value, parse_value
from app.settings.service import SettingsService


async def test_defaults_and_override(db):
    settings = SettingsService(db['bot_settings'], db['bot_settings_audit'])

    assert await settings.int('price.device_extra') == 75
    await settings.set('price.device_extra', 90, admin_id=1)
    assert await settings.int('price.device_extra') == 90

    # ключи с точками не должны превращаться во вложенные документы
    doc = await db['bot_settings'].find_one({'_id': 'price.device_extra'})
    assert doc['value'] == 90


async def test_toggle_and_reset(db):
    settings = SettingsService(db['bot_settings'])
    assert await settings.flag('features.extend_enabled') is True
    assert await settings.toggle('features.extend_enabled') is False
    await settings.reset('features.extend_enabled')
    assert await settings.flag('features.extend_enabled') is True


async def test_audit_records_change(db):
    settings = SettingsService(db['bot_settings'], db['bot_settings_audit'])
    await settings.set('pay.min_topup', 120, admin_id=42)
    record = await db['bot_settings_audit'].find_one({'key': 'pay.min_topup'})
    assert record['before'] == 75 and record['after'] == 120 and record['admin_id'] == 42


def test_percent_input_and_output():
    setting = INDEX['bonus.topup_rate']
    ok, value, _ = parse_value(setting, '25')
    assert ok and abs(value - 0.25) < 1e-9
    assert format_value(setting, 0.25) == '25%'


def test_bounds_are_enforced():
    ok, _, error = parse_value(INDEX['pay.min_topup'], '0')
    assert not ok and error
