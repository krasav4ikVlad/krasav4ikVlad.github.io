"""Скидки по аудиториям.

Главное, что здесь проверяется, — цена на экране и цена в списании это одно
и то же число. Разойтись они могут в четырёх местах сразу (кнопка тарифа,
строка «Плата за подписку», покупка, автопродление), и человек замечает
расхождение раньше, чем мы.
"""

import pytest

from app.domain.pricing import discounted
from app.domain.segments import audiences_of
from app.services.discounts import DiscountService


@pytest.fixture
def discounts(container):
    return DiscountService(container.settings)


def user_in(segment: str) -> dict:
    return {'user_data': {'user_id': 5}, 'growth': {'segment': segment}}


# ── арифметика ──────────────────────────────────────────────────────────────
def test_discount_rounds_in_the_users_favour():
    """149₽ со скидкой 10% — это 134₽, а не 135₽: округляем вниз."""
    assert discounted(149, 0.10) == 134
    assert discounted(100, 0.0) == 100
    assert discounted(100, 1.0) == 0


def test_discount_cannot_go_below_zero_or_above_full_price():
    assert discounted(100, 5.0) == 0
    assert discounted(100, -1.0) == 100


# ── выбор скидки ────────────────────────────────────────────────────────────
def test_segment_maps_to_its_audiences():
    assert audiences_of('expired_3d') == ('all', 'expired')
    assert audiences_of('active_paid') == ('all', 'active')
    assert audiences_of('что-то незнакомое') == ('all',)


async def test_no_discounts_by_default(discounts):
    assert await discounts.rate(user_in('expired_3d')) == 0.0


async def test_audience_discount_applies(container, discounts):
    await container.settings.set('discount.expired', 0.3)

    assert await discounts.rate(user_in('expired_3d')) == 0.3
    assert await discounts.rate(user_in('active_paid')) == 0.0


async def test_the_larger_of_two_matching_discounts_wins(container, discounts):
    """«Всем 10%» и «истёкшим 30%» — человек получает 30%, а не 40% и не 10%.

    Складывать нельзя: две акции по 60% дали бы отрицательную цену.
    """
    await container.settings.set('discount.all', 0.1)
    await container.settings.set('discount.expired', 0.3)

    assert await discounts.rate(user_in('expired_3d')) == 0.3
    assert await discounts.rate(user_in('active_paid')) == 0.1


async def test_discount_follows_the_segment(container, discounts):
    """Скидка живёт на аудитории: ушёл из неё — перестала действовать сама."""
    await container.settings.set('discount.churned', 0.5)

    assert await discounts.rate(user_in('churned_60d')) == 0.5
    assert await discounts.rate(user_in('active_paid')) == 0.0


async def test_notice_shows_the_percent(container, discounts):
    await container.settings.set('discount.all', 0.25)

    assert '25%' in await discounts.notice(user_in('active_paid'))
    await container.settings.set('discount.all', 0.0)
    assert await discounts.notice(user_in('active_paid')) == ''


# ── деньги ──────────────────────────────────────────────────────────────────
async def test_purchase_charges_the_discounted_price(container):
    """Списывается ровно то, что человек видел на кнопке."""
    from app.services.billing import BillingService

    class Vpn:
        async def create_subscription(self, user_id, days):
            from datetime import timedelta

            from app.core.time import now
            return {'uuid': 'u', 'shortUuid': 's',
                    'expireAt': now() + timedelta(days=days), 'createdAt': now()}

    await container.startup()
    await container.settings.set('discount.expired', 0.5)
    await container.users.create({'user_data': {'user_id': 5},
                                  'info': {'balance': 500},
                                  'growth': {'segment': 'expired_3d'}})

    billing = BillingService(container.users, container.plans, container.settings,
                             Vpn(), container.topup, discounts=container.discounts)
    plan = await container.plans.get('1month')
    result = await billing.buy(5, '1month')

    assert result['price'] == int(plan['price'] * 0.5)
    assert (await container.users.get(5))['info']['balance'] == 500 - result['price']


async def test_autorenewal_uses_the_same_discount(container):
    """Автопродление — тот же прайс. Иначе цена на экране обманывает."""
    from datetime import timedelta

    from app.core.time import now
    from app.services.renewal import RenewalService

    class Vpn:
        async def update_subscription(self, uuid, **kw):
            return {}

    await container.startup()
    await container.settings.set('discount.all', 0.2)
    plan = await container.plans.get('1month')
    await container.users.create({
        'user_data': {'user_id': 7}, 'info': {'balance': 1000},
        'growth': {'segment': 'active_paid'},
        'vpn': {'uuid': 'u', 'shortUuid': 's', 'period': plan['days'],
                'expireAt': now() + timedelta(hours=2)}})

    renewal = RenewalService(container.users, container.plans, container.settings,
                             Vpn(), container.topup, discounts=container.discounts)
    report = await renewal.run()

    assert report.renewed == 1
    assert (await container.users.get(7))['info']['balance'] == 1000 - int(plan['price'] * 0.8)


async def test_renewal_reads_the_segment_it_needs(container):
    """Проекция запроса — тоже часть скидки: без growth.segment автопродление
    списывало бы полную цену там, где на экране висит акция."""
    from datetime import timedelta

    from app.core.time import now
    from app.services.renewal import RenewalService

    seen = {}

    class Vpn:
        async def update_subscription(self, uuid, **kw):
            return {}

    class SpyDiscounts:
        async def price(self, user, plan):
            seen['segment'] = ((user or {}).get('growth') or {}).get('segment')
            return int(plan['price'])

    await container.startup()
    plan = await container.plans.get('1month')
    await container.users.create({
        'user_data': {'user_id': 8}, 'info': {'balance': 1000},
        'growth': {'segment': 'expired_3d'},
        'vpn': {'uuid': 'u', 'shortUuid': 's', 'period': plan['days'],
                'expireAt': now() + timedelta(hours=2)}})

    await RenewalService(container.users, container.plans, container.settings,
                         Vpn(), container.topup, discounts=SpyDiscounts()).run()

    assert seen['segment'] == 'expired_3d'


async def test_devices_are_not_discounted(container, discounts):
    """Скидка — только на тариф: у доп. устройств свой тридцатидневный цикл,
    и уценка там разъехалась бы с тем, что уже насчитано в пакетах."""
    from app.bot.screens.profile import price_line

    await container.settings.set('discount.all', 0.5)
    line = price_line(await discounts.price(user_in('active_paid'), {'price': 200}),
                      30, devices_price=150)

    assert '100₽ за месяц' in line
    assert '150₽ в месяц' in line


# ── экран ───────────────────────────────────────────────────────────────────
def test_price_tag_strikes_the_old_price():
    """В тексте зачёркивание — тег, в подписи кнопки — символы: разметки
    там нет вовсе, и <s> показался бы буквами."""
    from app.bot.screens.pricing import price_tag, strike

    assert price_tag(150, 100) == '100₽ вместо <s>150₽</s> −33%'
    assert price_tag(150, 100, html=False) == f'100₽ вместо {strike("150₽")} −33%'
    assert '<' not in price_tag(150, 100, html=False)


def test_price_tag_without_a_discount_is_just_the_price():
    from app.bot.screens.pricing import price_tag

    assert price_tag(150, 150) == '150₽'
    assert price_tag(0, 0) == '0₽'


def test_strike_draws_over_every_character():
    from app.bot.screens.pricing import STRIKE, strike

    assert strike('150₽') == f'1{STRIKE}5{STRIKE}0{STRIKE}₽{STRIKE}'


def test_price_line_shows_the_old_price_too():
    from app.bot.screens.profile import price_line

    line = price_line(100, 30, devices_price=0, full_price=150)

    assert '100₽ за месяц' in line
    assert '<s>150₽</s>' in line and '−33%' in line
    # зачёркивание вне <code>: внутри него разметка не разбирается
    assert '<code>' not in line.split('вместо')[1]


async def test_plan_button_shows_both_prices(container):
    from app.bot.keyboards.subscription import plans_keyboard
    from app.bot.screens.pricing import strike

    await container.startup()
    plan = await container.plans.get('1month')
    kb = await plans_keyboard(container.plans, 0, discount=0.5)
    labels = [button.text for row in kb.export() for button in row]

    assert any(f'{int(plan["price"] * 0.5)}₽ вместо {strike(str(plan["price"]) + "₽")} −50%'
               in label for label in labels), labels


async def test_plan_button_stays_plain_without_a_discount(container):
    from app.bot.keyboards.subscription import plans_keyboard

    await container.startup()
    plan = await container.plans.get('1month')
    labels = [button.text for row in (await plans_keyboard(container.plans, 0)).export()
              for button in row]

    assert any(f'{plan["price"]}₽' in label and 'вместо' not in label
               for label in labels), labels
