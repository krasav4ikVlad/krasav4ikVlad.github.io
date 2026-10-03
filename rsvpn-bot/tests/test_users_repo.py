from app.repositories.users import UsersRepository


async def test_charge_rejects_when_not_enough(db, user_factory):
    users = UsersRepository(db['users'])
    await user_factory(**{'info.balance': 100})

    assert await users.charge(1, 150, 'покупка') is False
    assert (await users.get(1))['info']['balance'] == 100


async def test_double_click_cannot_go_negative(db, user_factory):
    """Ключевой сценарий: два одновременных нажатия «Купить»."""
    users = UsersRepository(db['users'])
    await user_factory(**{'info.balance': 150})

    first = await users.charge(1, 150, 'покупка')
    second = await users.charge(1, 150, 'покупка')

    assert first is True and second is False
    assert (await users.get(1))['info']['balance'] == 0


async def test_campaign_slot_claimed_once(db, user_factory):
    users = UsersRepository(db['users'])
    doc = await user_factory()

    assert await users.claim_campaign_slot(doc['_id'], 'trial_d0') is True
    assert await users.claim_campaign_slot(doc['_id'], 'trial_d0') is False
