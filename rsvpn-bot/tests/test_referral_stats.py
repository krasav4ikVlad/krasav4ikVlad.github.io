"""Сводка реферальной программы."""

from app.domain.referrals import referral_stats

FULL = {
    'referrals': list(range(37)),
    'paying_referrals': [1],
    'payments_count': 9,
    'turnover_total': 1125,
    'earned_total': 510,
    'withdrawable': 575,
}


def test_stats_match_the_numbers_from_the_old_bot():
    stats = referral_stats(FULL)

    assert stats.invited == 37
    assert stats.active == 1
    assert stats.active_percent == 3            # 1 из 37
    assert stats.payments == 9
    assert stats.average_payment == 125         # 1125 / 9
    assert stats.per_active_friend == 510       # 510 / 1
    assert stats.earned == 510
    assert stats.withdrawable == 575


def test_empty_stats_do_not_divide_by_zero():
    """Новичок открывает раздел с нулями — экран не должен падать."""
    stats = referral_stats({})

    assert stats.invited == 0
    assert stats.active_percent == 0
    assert stats.average_payment == 0
    assert stats.per_active_friend == 0


def test_missing_document_is_the_same_as_empty():
    assert referral_stats(None).invited == 0


def test_broken_counters_do_not_break_the_screen():
    """В боевой базе поля бывают строками и None — это не повод падать."""
    stats = referral_stats({'referrals': None, 'payments_count': 'семь',
                            'turnover_total': None, 'earned_total': '300'})

    assert stats.invited == 0
    assert stats.payments == 0
    assert stats.earned == 300
