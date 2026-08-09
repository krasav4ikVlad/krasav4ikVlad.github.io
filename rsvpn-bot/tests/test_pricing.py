"""Денежная арифметика — чистые функции, тест мгновенный."""

from app.domain.pricing import (PricingRules, devices_price, missing_amount,
                                net_from_gross, referral_reward,
                                subscription_price, topup_credit)

RULES = PricingRules(device_price=75, free_devices=2, topup_bonus_rate=0.20,
                     referral_rate=0.30, gateway_fee_rate=0.05)


def test_devices_price_counts_only_extra():
    assert devices_price(2, RULES) == 0
    assert devices_price(5, RULES) == 225


def test_subscription_price_with_discount():
    assert subscription_price(150, 2, RULES) == 150
    assert subscription_price(150, 4, RULES) == 300
    assert subscription_price(150, 2, RULES, discount=0.4) == 90


def test_topup_bonus():
    assert topup_credit(100, RULES) == (120, 20)
    assert topup_credit(100, RULES, extra_rate=0.05) == (125, 25)


def test_topup_without_bonus_when_disabled():
    off = PricingRules(topup_bonus_rate=0.0)
    assert topup_credit(100, off) == (100, 0)


def test_referral_and_fee():
    assert referral_reward(500, RULES) == 150
    assert net_from_gross(105, RULES) == 100


def test_missing_amount_never_negative():
    assert missing_amount(150, 200) == 0
    assert missing_amount(150, 50) == 100

