"""Общие хелперы для хендлеров тренера."""

import secrets
import re
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from aiogram.types import CallbackQuery

from models import User, Workout, WorkoutRegistration, Trick, TrickLog, STATUS_ACTIVE
from keyboards.coach_kb import (
    get_coach_main_menu_sync,
    get_skater_workout_card_kb,
    get_active_workout_kb,
    WorkoutAction,
)
from calculator import calculate_current_workout_totals


async def get_active_workout(session: AsyncSession) -> Workout | None:
    """Возвращает активную тренировку (status='active'), если она есть"""
    return (
        await session.execute(select(Workout).where(Workout.status == STATUS_ACTIVE))
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
    """Генерация 4-значного PIN-кода (криптостойкий ГСЧ)"""
    return f"{secrets.randbelow(9000) + 1000}"


def percentage_to_multiplier(pct: int) -> float:
    """Переводит процент поправки в множитель качества: -10 -> 0.9, +20 -> 1.2"""
    return max(0.0, 1.0 + pct / 100.0)


def level_progress_percent(current_xp: int, prev_required_xp: int, next_required_xp: int) -> float:
    """Процент прогресса до следующего уровня.

    required_xp в level_experience — СУММАРНЫЙ опыт, нужный для достижения уровня.
    Прогресс внутри уровня считается от порога текущего уровня:
      (xp - required_xp[текущий]) / (required_xp[следующий] - required_xp[текущий]) * 100
    """
    span = next_required_xp - prev_required_xp
    if span <= 0:
        return 100.0
    gained = current_xp - prev_required_xp
    return max(0.0, min(100.0, gained / span * 100))


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
        status=STATUS_ACTIVE,
        pin_code=generate_workout_pin(),
    )


async def show_skater_card_or_main_list(
    callback: CallbackQuery, callback_data: WorkoutAction, session: AsyncSession
):
    """Ярус карточки спортсмена: показывает текущий лвл, опыт и предварительный балл.

    Живет в helpers (а не в workout.py), чтобы другие модули (trick_flow,
    combo_builder) могли импортировать ее напрямую без циклических импортов.
    Если исходное сообщение удалено (поток «delete + answer» при вводе конусов),
    edit_text падает — тогда карточка печатается новым сообщением внизу чата.
    """
    workout_id = callback_data.workout_id
    student_id = callback_data.student_id

    # Если student_id пустой — возвращаемся к главному списку тренировки
    if not student_id:
        current_students = await get_workout_students(session, workout_id)
        await callback.message.edit_text(
            "🏋️‍♂️ **ИДЕТ ТРЕНИРОВКА**\n\nСписок спортсменов на занятии:",
            reply_markup=get_active_workout_kb(workout_id, current_students),
        )
        return

    # Если передан student_id — проверяем/создаем регистрацию на тренировку
    reg = (
        await session.execute(
            select(WorkoutRegistration).where(
                WorkoutRegistration.workout_id == workout_id,
                WorkoutRegistration.student_id == student_id,
            )
        )
    ).scalar_one_or_none()

    if not reg:
        reg = WorkoutRegistration(workout_id=workout_id, student_id=student_id)
        session.add(reg)
        await session.commit()

    student = await session.get(User, student_id)

    # Тянем все логи трюков этого ученика за текущую сессию
    logs = (
        (
            await session.execute(
                select(TrickLog).where(
                    TrickLog.workout_id == workout_id, TrickLog.student_id == student_id
                )
            )
        )
        .scalars()
        .all()
    )

    # Калькуляция результатов «на лету»
    current_sport_score, current_xp_gain = await calculate_current_workout_totals(
        list(logs), student, session
    )

    card_text = (
        f"👤 **Спортсмен:** {student.full_name}\n"
        f"🎮 **Ранг:** {student.title} (Уровень: {student.level})\n"
        f"✨ **Текущий опыт:** {student.xp} EXP\n\n"
        f"📊 **Предварительные итоги сессии:**\n"
        f" — Средний спортивный балл: <b>{current_sport_score}</b>\n"
        f" — Набрано опыта за сегодня: <b>+{current_xp_gain} EXP</b>"
    )

    try:
        await callback.message.edit_text(
            text=card_text,
            reply_markup=get_skater_workout_card_kb(workout_id, student_id, list(logs)),
        )
    except Exception:
        # Сообщение могло быть удалено (например, после «delete + answer»
        # в потоке ввода конусов) или не подлежать редактированию.
        # Печатаем карточку новым сообщением внизу чата.
        await callback.message.answer(
            text=card_text,
            reply_markup=get_skater_workout_card_kb(workout_id, student_id, list(logs)),
        )
