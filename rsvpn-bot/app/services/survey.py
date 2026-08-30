"""Опрос с бонусом за прохождение.

Перенос из handlers/survey_bonus.py. Начисление бонуса там уже было атомарным,
здесь добавлено: сумма и вопросы — настройки, начисление идёт через
users.credit (единый формат транзакций), а не своим $push в info.logs_balance.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.core.time import now

log = logging.getLogger(__name__)

ANSWER_LABELS = {
    'good': 'Всё стабильно',
    'ok': 'В целом норм, бывают сбои',
    'bad': 'Часто нестабильно',
    'awful': 'Совсем не устраивает',
    'price_ok': 'Приемлемо',
    'price_high': 'Дороговато, но останусь',
    'price_leave': 'Слишком дорого, уйду',
}
NEEDS_COMMENT = ('bad', 'awful')


@dataclass
class SurveyStep:
    saved: bool
    next_question: str = ''     # price | comment | ''
    bonus: int = 0


class SurveyService:
    def __init__(self, users, answers, settings):
        self.users = users
        self.answers = answers
        self.settings = settings

    async def ensure_indexes(self) -> None:
        from app.repositories.base import Repository

        await Repository(self.answers).ensure_index('user_id', unique=True)

    async def start(self, user_id: int, questions: list[str]) -> None:
        await self.answers.update_one(
            {'user_id': user_id},
            {'$set': {'pending': questions, 'started_at': now()},
             '$setOnInsert': {'bonus_given': False}},
            upsert=True)

    async def answer(self, user_id: int, question: str, value: str) -> SurveyStep:
        if not await self.settings.flag('survey.enabled'):
            return SurveyStep(False)

        await self.answers.update_one(
            {'user_id': user_id},
            {'$set': {f'answers.{question}': value, f'answers.{question}_at': now()}},
            upsert=True)

        if question == 'stability' and value in NEEDS_COMMENT:
            await self.answers.update_one({'user_id': user_id},
                                          {'$set': {'awaiting_comment': True}})
            return SurveyStep(True, next_question='comment')

        return await self._advance(user_id, question)

    async def comment(self, user_id: int, text: str) -> SurveyStep:
        await self.answers.update_one(
            {'user_id': user_id},
            {'$set': {'answers.comment': text[:2000], 'awaiting_comment': False}})
        return await self._advance(user_id, 'stability')

    async def is_awaiting_comment(self, user_id: int) -> bool:
        return bool(await self.answers.find_one({'user_id': user_id, 'awaiting_comment': True}))

    async def _advance(self, user_id: int, done: str) -> SurveyStep:
        doc = await self.answers.find_one({'user_id': user_id}) or {}
        pending = [q for q in (doc.get('pending') or []) if q != done]
        await self.answers.update_one({'user_id': user_id}, {'$set': {'pending': pending}})

        if pending:
            return SurveyStep(True, next_question=pending[0])
        return SurveyStep(True, bonus=await self.give_bonus(user_id))

    async def give_bonus(self, user_id: int) -> int:
        """Бонус ровно один раз: флаг ставится тем же запросом, что и проверяется."""
        amount = await self.settings.int('survey.bonus')
        if amount <= 0:
            return 0

        claimed = await self.answers.update_one(
            {'user_id': user_id, 'bonus_given': {'$ne': True}},
            {'$set': {'bonus_given': True, 'bonus_at': now(), 'bonus_amount': amount}})
        if claimed.modified_count != 1:
            return 0

        if not await self.users.credit(user_id, amount, 'Бонус за участие в опросе',
                                   kind='survey'):
            # пользователя нет — снимаем флаг, чтобы бонус не потерялся
            await self.answers.update_one({'user_id': user_id},
                                          {'$set': {'bonus_given': False}})
            return 0

        log.info('бонус за опрос %s₽ начислен %s', amount, user_id)
        return amount

    async def stats(self) -> dict:
        """Сводка ответов для админки."""
        result: dict[str, dict[str, int]] = {}
        async for doc in self.answers.find({}):
            for key, value in (doc.get('answers') or {}).items():
                if key.endswith('_at') or key == 'comment':
                    continue
                result.setdefault(key, {})
                result[key][value] = result[key].get(value, 0) + 1
        return result
