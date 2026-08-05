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


# ── режимы запуска ──────────────────────────────────────────────────────────
def test_scheduler_can_be_switched_off_by_env(monkeypatch):
    """Пока работает старый бот, планировщик нового должен молчать."""
    from app.core.config import Config

    for value, expected in (('0', False), ('false', False), ('no', False),
                            ('1', True), ('', True)):
        monkeypatch.setenv('API_TOKEN', '1:TEST')
        monkeypatch.setenv('SCHEDULER_ENABLED', value)
        assert Config.from_env().scheduler_enabled is expected, value


def test_dangerous_combination_is_visible(monkeypatch):
    monkeypatch.setenv('API_TOKEN', '1:TEST')
    monkeypatch.setenv('PANEL_DRY_RUN', '0')
    monkeypatch.setenv('SCHEDULER_ENABLED', '1')

    from app.core.config import Config
    config = Config.from_env()

    # именно это сочетание меняет боевые данные
    assert config.vpn.dry_run is False and config.scheduler_enabled is True


# ── картинки экранов ────────────────────────────────────────────────────────
def test_media_accepts_any_common_extension(container, tmp_path):
    import dataclasses

    container.config = dataclasses.replace(container.config, media_dir=str(tmp_path))

    assert container.media('profile') is None

    (tmp_path / 'profile.jpg').write_bytes(b'x')
    assert container.media('profile').endswith('profile.jpg')

    (tmp_path / 'profile.png').write_bytes(b'x')
    assert container.media('profile').endswith('profile.png')   # png приоритетнее
