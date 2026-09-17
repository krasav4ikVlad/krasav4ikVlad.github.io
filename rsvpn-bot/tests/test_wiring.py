"""Сборка контейнера: что должно быть готово к моменту запуска бота.

Появился после того, как main_bot.py забыл вызвать attach_bot. Внешне бот
работал, но devices, renewal, expiry, lifeline и notifier оставались None:
менеджер устройств падал на нажатии, админ-уведомления не уходили, а
планировщик тихо ничего не делал — его задачи проверяют сервис на None.
"""

import pytest

from app.core.container import Container


class FakeBot:
    async def send_message(self, *a, **kw):
        return True


@pytest.fixture
def attached(container):
    container.attach_bot(FakeBot())
    return container


def test_services_are_missing_until_the_bot_is_attached(container):
    assert set(container.missing_services()) == set(Container.REQUIRED_AFTER_ATTACH)


def test_attach_bot_wires_everything_the_handlers_use(attached):
    assert attached.missing_services() == []


def test_device_handlers_have_a_service_to_call(attached):
    """Ровно то, что падало: c.devices был None на нажатии кнопки."""
    for method in ('add', 'remove', 'unbind'):
        assert callable(getattr(attached.devices, method))


def test_notifier_reaches_the_services_that_report_events(attached):
    for service in (attached.topup, attached.billing, attached.gifts, attached.payouts):
        if service is not None:
            assert service.notifier is attached.notifier


def test_attach_bot_can_be_called_twice(attached):
    attached.attach_bot(FakeBot())
    assert attached.missing_services() == []


async def test_scheduler_jobs_survive_an_unwired_container(container, caplog):
    """Задачи не должны падать, но и молчать про пропуск больше не должны."""
    from app.scheduler import jobs

    await jobs.charge_subscriptions(container)

    assert 'renewal' in caplog.text


# ── работа на старой базе без изменений в ней ───────────────────────────────
#
# Режим LEGACY_COLLECTIONS: бот читает и пишет туда же, куда старый бот, —
# в коллекции с пробелом на конце имени. Если хоть одна из них уедет в
# «правильное» имя, данные окажутся не там, куда смотрит работающий бот.
import dataclasses

from app.core import db as names


def legacy_container(container):
    config = dataclasses.replace(container.config, legacy_collections=True)
    return Container(config=config, db=container.db)


def test_renamed_collections_keep_old_names_in_legacy_mode(container):
    c = legacy_container(container)

    assert c.collection(names.PROMO_CODES).name == 'promo_codes '
    assert c.collection(names.PROMO_USAGES).name == 'promo_usages '
    assert c.collection(names.QUICK_REPLIES).name == 'support_quick_replies '
    assert c.collection(names.CHURN_SURVEYS).name == 'churn_surveys '


def test_normal_mode_uses_clean_names(container):
    assert container.collection(names.PROMO_CODES).name == 'promo_codes'


def test_untouched_collections_are_the_same_in_both_modes(container):
    """У users и plans пробела в имени не было — переключать нечего."""
    c = legacy_container(container)

    for name in (names.USERS, names.PLANS, names.GIFTS, names.PAYMENTS):
        assert c.collection(name).name == container.collection(name).name


def test_services_that_touch_renamed_collections_go_through_the_switch(container):
    """Промокоды и опросы должны подхватывать режим, а не жёсткое имя."""
    c = legacy_container(container)

    assert c.promo.codes.name == 'promo_codes '
    assert c.promo.usages.name == 'promo_usages '
    assert c.survey.answers.name == 'churn_surveys '


# ── страховка при переименовании коллекций ──────────────────────────────────
async def test_stranded_data_is_reported(container):
    """Переименовали не всё — бот читает пустую коллекцию и молчит.

    Именно так «пропадают» промокоды: данные остались в имени с пробелом,
    а бот смотрит в чистое. Одна строка в логе при старте дешевле разбора
    по жалобам.
    """
    await container.db['promo_codes '].insert_one({'code': 'СТАРЫЙ'})

    assert await container.warn_about_legacy_leftovers() == ['promo_codes ']


async def test_nothing_is_reported_after_a_proper_rename(container):
    await container.db['promo_codes'].insert_one({'code': 'ПЕРЕЕХАЛ'})

    assert await container.warn_about_legacy_leftovers() == []


async def test_legacy_mode_has_nothing_to_warn_about(container):
    """В режиме совместимости бот и должен читать имена с пробелом."""
    c = legacy_container(container)
    await c.db['promo_codes '].insert_one({'code': 'СТАРЫЙ'})

    assert await c.warn_about_legacy_leftovers() == []


async def test_empty_database_is_silent(container):
    assert await container.warn_about_legacy_leftovers() == []
