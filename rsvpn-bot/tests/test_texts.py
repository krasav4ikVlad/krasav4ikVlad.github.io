from app.content import texts


def test_name_placeholder_without_name():
    assert texts.render('campaign.trial.d0', name=None).startswith('как вам RS VPN')
    assert texts.render('campaign.trial.d0', name='Иван').startswith('Иван, как вам')


def test_multi_variant_used_for_repeat_payers():
    single = texts.render('campaign.expired.d1', name='Иван', multi=False)
    repeat = texts.render('campaign.expired.d1', name='Иван', multi=True)
    assert single != repeat
    assert 'не первый раз' in repeat


def test_missing_placeholder_does_not_crash():
    assert texts.render('screen.balance.not_enough') is not None


def test_overrides_from_admin_win():
    texts.set_overrides({'error.generic': 'Мой текст'})
    assert texts.render('error.generic') == 'Мой текст'
    texts.set_overrides({})
