"""Доменные исключения.

Хендлеры ловят их и показывают пользователю понятный текст, а не «Произошла
какая-то ошибка» из общего except Exception.
"""

from __future__ import annotations


class AppError(Exception):
    """База для всех ожидаемых ошибок приложения."""
    user_message = 'Что-то пошло не так. Попробуйте ещё раз или напишите в поддержку.'

    def __init__(self, message: str = '', *, user_message: str | None = None):
        super().__init__(message or self.__class__.__name__)
        if user_message:
            self.user_message = user_message


class NotEnoughBalance(AppError):
    user_message = 'На балансе недостаточно средств.'

    def __init__(self, need: int, have: int):
        super().__init__(f'need={need} have={have}')
        self.need = need
        self.have = have
        self.user_message = f'Не хватает {need - have}₽. Пополните баланс и вернитесь к покупке.'


class PlanUnavailable(AppError):
    user_message = 'Этот тариф сейчас недоступен.'


class FeatureDisabled(AppError):
    user_message = 'Функция временно отключена.'


class VpnPanelError(AppError):
    user_message = 'Сервис подписок не отвечает. Мы уже знаем, попробуйте через минуту.'


class PaymentError(AppError):
    user_message = 'Платёжная система недоступна. Попробуйте другой способ оплаты.'


class PromoError(AppError):
    """Текст задаётся при создании: «промокод истёк», «уже использован» и т.д."""
