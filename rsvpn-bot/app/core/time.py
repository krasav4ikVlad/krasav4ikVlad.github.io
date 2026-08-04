"""Время в одном месте: одна таймзона, один now(), один парсер дат.

Сейчас в боте вперемешку datetime.now() без tz, datetime.now(MSK) и строки
'%d.%m.%Y %H:%M:%S' — из-за этого сравнения дат ведут себя по-разному в
разных модулях. Здесь единственный источник времени.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

MSK = ZoneInfo('Europe/Moscow')


def now() -> datetime:
    """Всегда aware-datetime в МСК."""
    return datetime.now(MSK)


def to_msk(value: datetime) -> datetime:
    return value.replace(tzinfo=MSK) if value.tzinfo is None else value.astimezone(MSK)


def parse_dt(value) -> datetime | None:
    """Понимает datetime, BSON {'$date': ...}, ISO-строку и '%d.%m.%Y %H:%M:%S'."""
    if not value:
        return None
    if isinstance(value, datetime):
        return to_msk(value)
    if isinstance(value, dict) and '$date' in value:
        return parse_dt(value['$date'])
    if isinstance(value, str):
        raw = value.strip()
        try:
            return to_msk(datetime.fromisoformat(
                raw.replace('Z', '+00:00') if raw.endswith('Z') else raw))
        except ValueError:
            pass
        try:
            return to_msk(datetime.strptime(raw, '%d.%m.%Y %H:%M:%S'))
        except ValueError:
            return None
    return None


def hours_since(value: datetime) -> float:
    parsed = parse_dt(value)
    return (now() - parsed).total_seconds() / 3600 if parsed else 0.0


def days_since(value: datetime) -> float:
    return hours_since(value) / 24


def fmt(value: datetime | None, pattern: str = '%d.%m.%Y %H:%M') -> str:
    parsed = parse_dt(value)
    return parsed.strftime(pattern) if parsed else '—'


def plus_days(days: float) -> datetime:
    return now() + timedelta(days=days)
