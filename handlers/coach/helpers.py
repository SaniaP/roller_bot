"""Общие хелперы для хендлеров тренера."""

import re
import random
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from models import User, Workout, WorkoutRegistration, Trick
from keyboards.coach_kb import get_coach_main_menu_sync


async def get_active_workout(session: AsyncSession) -> Workout | None:
    """Возвращает активную тренировку (status='active'), если она есть"""
    return (
        await session.execute(select(Workout).where(Workout.status == "active"))
    ).scalar_one_or_none()


async def get_coach_menu_for(session: AsyncSession):
    """Возвращает главное меню тренера с учётом наличия активной тренировки"""
    active_w = await get_active_workout(session)
    return get_coach_main_menu_sync(has_active_workout=active_w is not None)


async def get_workout_students(session: AsyncSession, workout_id: int) -> list[User]:
    """Список спортсменов, зарегистрированных на тренировку"""
    regs = (
        (
            await session.execute(
                select(WorkoutRegistration).where(
                    WorkoutRegistration.workout_id == workout_id
                )
            )
        )
        .scalars()
        .all()
    )
    students = []
    for r in regs:
        st = await session.get(User, r.student_id)
        if st:
            students.append(st)
    return students


async def get_trick_categories(session: AsyncSession) -> list[str]:
    """Все категории трюков из справочника"""
    return list(
        (await session.execute(select(Trick.category).distinct())).scalars().all()
    )


def generate_workout_pin() -> str:
    """Генерация уникального 4-значного PIN-кода"""
    return str(random.randint(1000, 9999))


def percentage_to_multiplier(pct: int) -> float:
    """Переводит процент поправки в множитель качества: -10 -> 0.9, +20 -> 1.2"""
    return max(0.0, 1.0 + pct / 100.0)


def draw_progress_bar(
    percent: float, length: int = 10, filled: str = "█", empty: str = "░"
) -> str:
    """Рисует текстовый прогресс-бар: draw_progress_bar(45) -> '████▒░░░░░ 45%'.
    percent от 0 до 100, length — количество сегментов бара"""
    percent = max(0.0, min(100.0, percent))
    filled_count = round(length * percent / 100)
    empty_count = length - filled_count
    bar = filled * filled_count + empty * empty_count
    return f"{bar} {percent:.0f}%"


def parse_quantity_input(text: str):
    """Парсит ввод количества конусов вида '8' / '8,-10' / '11,+20'.
    Возвращает (quantity, percentage_mod | None) или None при неверном формате."""
    match = re.match(r"^(\d+)(?:,([+-]?\d+))?$", text)
    if not match:
        return None
    return int(match.group(1)), match.group(2)


def format_accuracy_text(percentage_mod: str | None) -> str:
    """Формирует человекочитаемую подпись поправки: '-10' -> '-10%', '20' -> '+20%'"""
    if not percentage_mod:
        return "без изменений"
    if not percentage_mod.startswith(("-", "+")):
        return f"+{percentage_mod}%"
    return f"{percentage_mod}%"


def new_workout_now() -> Workout:
    """Создает объект новой активной тренировки с PIN-кодом"""
    return Workout(
        date=datetime.now(),
        status="active",
        pin_code=generate_workout_pin(),
    )
