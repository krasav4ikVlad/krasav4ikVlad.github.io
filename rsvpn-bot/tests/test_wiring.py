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
