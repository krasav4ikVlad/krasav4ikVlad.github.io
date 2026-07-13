"""Owner-only: manage operator accounts (create, edit, deactivate — never hard-delete,
history in the audit log must stay attributable)."""
from __future__ import annotations

import re

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, HTTPException, Request, status
from pymongo.errors import DuplicateKeyError

from .. import audit
from ..audit import write_audit
from ..config import get_settings
from ..database import get_db
from ..schemas import OperatorCreate, OperatorPublic, OperatorUpdate
from ..security import (OwnerOperator, client_ip, generate_temp_password,
                        hash_password, invalidate_operator_cache)
from ..utils import utcnow
from .auth import operator_public

router = APIRouter(prefix="/api/operators", tags=["operators"])

# ---------------------------------------------------------------- недельный график

DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_RU = {"mon": "Пн", "tue": "Вт", "wed": "Ср", "thu": "Чт",
          "fri": "Пт", "sat": "Сб", "sun": "Вс"}
INTERVAL_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)-([01]?\d|2[0-3]):([0-5]\d)$")
# «плавающий» день: рабочий интервал + льготное окно первого ответа,
# напр. "09:00-16:00~1" = день 9-16, первый ответ в первый час (9-10);
# рабочее время отсчитывается с первого ответа, но не позже 10:00 —
# до этого момента ожидание не портит рейтинг оператора
FLOAT_RE = re.compile(
    r"^([01]?\d|2[0-3]):([0-5]\d)-([01]?\d|2[0-3]):([0-5]\d)~(\d{1,2}(?:\.\d)?)$")

# потолок недельных часов (12 ч × 7 дней): завышенный график — яд для нормы
# активности, он раздувает часы команды и занижает норму остальных операторов
MAX_WEEK_HOURS = 84


def validate_schedule(schedule: dict) -> tuple[dict, float]:
    """Проверяет недельный график и возвращает (нормализованный график, часов/нед).
    Формат дня: "09:00-18:00" (фиксированный) или "09:00-16:00~1" (плавающий:
    рабочий интервал + льготное окно первого ответа в часах). Пусто = выходной;
    у фиксированных конец 00:00 = до конца суток, конец меньше начала = смена
    через полночь (18:00-02:00 = 8 часов)."""
    clean: dict[str, str] = {}
    total_min = 0.0
    for day in DAY_KEYS:
        v = (schedule.get(day) or "").strip()
        if not v:
            clean[day] = ""
            continue
        fm = FLOAT_RE.match(v)
        if fm:
            start = int(fm.group(1)) * 60 + int(fm.group(2))
            end = int(fm.group(3)) * 60 + int(fm.group(4))
            grace = float(fm.group(5)) * 60  # окно первого ответа, минут
            if end == 0:
                end = 1440  # конец 00:00 = до конца суток
            if end <= start:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                    f"{DAY_RU[day]}: у дня «по 1-му ответу» конец должен быть позже начала")
            if not 0 < grace < (end - start):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"{DAY_RU[day]}: окно первого ответа должно быть больше нуля и меньше самого дня")
            # в недельные часы идёт гарантированный минимум: конец − (начало + окно)
            total_min += end - start - grace
            clean[day] = v
            continue
        m = INTERVAL_RE.match(v)
        if not m:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"{DAY_RU[day]}: интервал в формате ЧЧ:ММ-ЧЧ:ММ (например 09:00-18:00) "
                f"или ЧЧ:ММ-ЧЧ:ММ~часы для плавающего дня")
        start = int(m.group(1)) * 60 + int(m.group(2))
        end = int(m.group(3)) * 60 + int(m.group(4))
        dur = (end - start) if end > start else (1440 - start + end)  # 00:00/через полночь
        total_min += dur
        clean[day] = v
    unknown = set(schedule) - set(DAY_KEYS)
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Неизвестные дни в графике: {', '.join(sorted(unknown))}")
    if total_min > MAX_WEEK_HOURS * 60:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"В графике {round(total_min / 60, 1)} ч/нед — больше {MAX_WEEK_HOURS} ч. "
            "Такой график раздувает часы команды и занижает норму остальных "
            "операторов. Укажите реальные рабочие часы.")
    return clean, round(total_min / 60, 1)


def _col():
    return get_db()[get_settings().operators_collection]


def _oid(operator_id: str) -> ObjectId:
    try:
        return ObjectId(operator_id)
    except InvalidId:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Оператор не найден")


@router.get("", response_model=list[OperatorPublic])
async def list_operators(_owner: OwnerOperator):
    return [operator_public(doc) async for doc in _col().find().sort("created_at", 1)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_operator(body: OperatorCreate, request: Request, owner: OwnerOperator):
    """Создание учётки: пароль генерируется сервером (временный), показывается
    владельцу один раз. Оператор обязан сменить его при первом входе."""
    login = body.login.lower()
    if await _col().find_one({"login": login}):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Логин «{body.login}» уже занят")

    temp_password = generate_temp_password()
    doc = {
        "login": login,
        "password_hash": hash_password(temp_password),
        "name": body.name.strip(),
        "role": body.role,
        "permissions": body.permissions,  # None = все права
        "active": True,
        "must_change_password": True,
        "created_at": utcnow(),
        "created_by": str(owner["_id"]),
        "last_login_at": None,
    }
    try:
        result = await _col().insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Логин «{body.login}» уже занят")
    doc["_id"] = result.inserted_id

    await write_audit(
        operator=owner, action=audit.ACTION_OPERATOR_CREATE, target_user_id=None,
        new_value={"login": doc["login"], "name": doc["name"], "role": doc["role"],
                   "temp_password": True},
        ip=client_ip(request),
    )
    # temp_password отдаётся в ответе ОДИН раз и нигде не сохраняется в открытом виде
    return {"operator": operator_public(doc).model_dump(), "temp_password": temp_password}


@router.post("/{operator_id}/reset-password")
async def reset_operator_password(operator_id: str, request: Request, owner: OwnerOperator):
    """Сброс пароля: генерирует новый временный, оператор обязан сменить его при входе."""
    oid = _oid(operator_id)
    existing = await _col().find_one({"_id": oid})
    if existing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Оператор не найден")
    if oid == owner["_id"]:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Свой пароль меняйте через «Сменить пароль», а не сбросом")

    temp_password = generate_temp_password()
    await _col().update_one(
        {"_id": oid},
        {"$set": {"password_hash": hash_password(temp_password),
                  "must_change_password": True}},
    )
    invalidate_operator_cache(operator_id)
    await write_audit(
        operator=owner, action=audit.ACTION_OPERATOR_PWD_RESET, target_user_id=None,
        new_value={"login": existing["login"]},
        ip=client_ip(request),
    )
    return {"login": existing["login"], "temp_password": temp_password}


@router.patch("/{operator_id}", response_model=OperatorPublic)
async def update_operator(operator_id: str, body: OperatorUpdate,
                          request: Request, owner: OwnerOperator):
    oid = _oid(operator_id)
    existing = await _col().find_one({"_id": oid})
    if existing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Оператор не найден")

    # An owner cannot demote or deactivate themself — prevents locking everyone out
    if oid == owner["_id"] and (body.active is False or (body.role and body.role != "owner")):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Нельзя отключить или понизить собственную учётную запись")

    updates: dict = {}
    changed_public: dict = {}
    if body.name is not None:
        updates["name"] = body.name.strip()
        changed_public["name"] = updates["name"]
    if body.role is not None:
        updates["role"] = body.role
        changed_public["role"] = body.role
    if body.active is not None:
        updates["active"] = body.active
        changed_public["active"] = body.active
    if body.permissions is not None:
        updates["permissions"] = body.permissions
        changed_public["permissions"] = body.permissions
    if body.salary_base is not None:
        updates["salary_base"] = body.salary_base
        changed_public["salary_base"] = body.salary_base
    if body.hours_per_week is not None:
        updates["hours_per_week"] = body.hours_per_week
        changed_public["hours_per_week"] = body.hours_per_week
    if body.tg_username is not None:
        updates["tg_username"] = body.tg_username.strip().lstrip("@").lower()
        changed_public["tg_username"] = updates["tg_username"]
    if body.schedule is not None:
        clean, hours = validate_schedule(body.schedule)
        updates["schedule"] = clean
        updates["hours_per_week"] = hours  # часы всегда следуют из графика
        changed_public["schedule"] = clean
        changed_public["hours_per_week"] = hours
    if not updates:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Нет изменений")

    await _col().update_one({"_id": oid}, {"$set": updates})
    invalidate_operator_cache(operator_id)
    updated = await _col().find_one({"_id": oid})

    await write_audit(
        operator=owner, action=audit.ACTION_OPERATOR_UPDATE, target_user_id=None,
        old_value={"login": existing["login"],
                   **{k: existing.get(k) for k in changed_public if k != "password"}},
        new_value={"login": existing["login"], **changed_public},
        ip=client_ip(request),
    )
    return operator_public(updated)
