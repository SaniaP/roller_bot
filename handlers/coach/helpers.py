"""Общие хелперы для хендлеров тренера."""

import secrets
import re
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from aiogram.types import CallbackQuery

from models import (
    User,
    Workout,
    WorkoutRegistration,
    Trick,
    TrickLog,
    STATUS_ACTIVE,
    CoachStudent,
)
from keyboards.coach_kb import (
    get_coach_main_menu_sync,
    get_skater_workout_card_kb,
    get_active_workout_kb,
    WorkoutAction,
)


async def get_active_workout(
    session: AsyncSession, coach_id: int | None = None
) -> Workout | None:
    """Возвращает активную тренировку (status='active'), если она есть.
    coach_id задан — только активная тренировка этого тренера (изоляция
    параллельных тренировок нескольких тренеров). coach_id is None —
    активная соло-тренировка пользователя (is_solo=True)."""
    stmt = select(Workout).where(Workout.status == STATUS_ACTIVE)
    if coach_id is not None:
        stmt = stmt.where(Workout.coach_id == coach_id)
    else:
        stmt = stmt.where(Workout.is_solo.is_(True))
    return (await session.execute(stmt.limit(1))).scalar_one_or_none()


async def get_coach_menu_for(session: AsyncSession, coach_id: int):
    """Возвращает главное меню тренера с учётом наличия ЕГО активной тренировки"""
    active_w = await get_active_workout(session, coach_id=coach_id)
    return get_coach_main_menu_sync(has_active_workout=active_w is not None)


async def get_coach_menu_for_tg(session: AsyncSession, telegram_id: int):
    """Меню тренера по его telegram_id (удобно в хэндлерах callback'ов)."""
    coach = (
        await session.execute(select(User).where(User.telegram_id == telegram_id))
    ).scalar_one_or_none()
    if not coach:
        return get_coach_main_menu_sync(has_active_workout=False)
    return await get_coach_menu_for(session, coach.id)


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


async def get_coach_students(session: AsyncSession, coach_id: int) -> list[User]:
    """Список учеников конкретного тренера (через CoachStudent)"""
    links = (
        (
            await session.execute(
                select(CoachStudent).where(CoachStudent.coach_id == coach_id)
            )
        )
        .scalars()
        .all()
    )
    students = []
    for link in links:
        st = await session.get(User, link.student_id)
        if st and st.role == "student":
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


def level_progress_percent(
    current_xp: int, prev_required_xp: int, next_required_xp: int
) -> float:
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
    quantity = int(match.group(1))
    if quantity <= 0:
        # Ноль и отрицательные числа запрещены: количество конусов >= 1
        return None
    return quantity, match.group(2)


def format_accuracy_text(percentage_mod: str | None) -> str:
    """Формирует человекочитаемую подпись поправки: '-10' -> '-10%', '20' -> '+20%'"""
    if not percentage_mod:
        return "без изменений"
    if not percentage_mod.startswith(("-", "+")):
        return f"+{percentage_mod}%"
    return f"{percentage_mod}%"


def new_workout_now(coach_id: int | None = None, is_solo: bool = False) -> Workout:
    """Создает объект новой активной тренировки с PIN-кодом.
    Для соло-тренировки — без PIN и без тренера."""
    return Workout(
        date=datetime.now(),
        status=STATUS_ACTIVE,
        pin_code=None if is_solo else generate_workout_pin(),
        coach_id=coach_id,
        is_solo=is_solo,
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

    # Тянем все логи трюков этого ученика за текущую сессию (+ трюки для названий)
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

    tricks = {
        t.id: t
        for t in (
            await session.scalars(
                select(Trick).where(Trick.id.in_([l.trick_id for l in logs] or [0]))
            )
        ).all()
    }

    # --- Шапка: имя + ранг (без «текущий опыт»)
    lines = [
        f"👤 <b>{student.full_name}</b>",
        f"🎮 <b>Ранг:</b> {student.title} (Уровень: {student.level})",
    ]

    # --- Связки блоками, затем одиночные трюки (как в журнале тренировки)
    from handlers.coach.stats import _combo_exp_map
    from models import LevelExperience

    pairs = [(log, tricks.get(log.trick_id)) for log in logs]
    combo_exp = await _combo_exp_map(pairs, student, session)

    combos: dict[str, list[tuple]] = {}
    singles: list[tuple] = []
    for log, trick in pairs:
        if log.combo_id:
            combos.setdefault(log.combo_id, []).append((log, trick))
        else:
            singles.append((log, trick))

    if combos:
        lines.append("")
        for cid, steps in combos.items():
            steps.sort(key=lambda p: p[0].combo_step or 0)
            names = " → ".join(t.name if t else "?" for _log, t in steps)
            last_log = steps[-1][0]
            combo_points = last_log.calculated_points + (last_log.combo_bonus or 0.0)
            combo_xp = combo_exp.get(cid, 0)
            lines.append(
                f"🧩 Связка: {names} — {combo_points:.1f} очков, +{combo_xp} EXP"
            )

    if singles:
        lines.append("")
        for log, trick in singles:
            name = trick.name if trick else "Трюк"
            lines.append(f"• {name} 🏅✨")
            lines.append(
                f"{log.quantity} кон. ({log.cone_line}) — {log.calculated_points:.1f} очк. | +{log.calculated_xp} EXP"
            )

    # --- Живая шапка: база = сумма лучших 8 / 8, штраф = 3.0 за недостающую категорию,
    # опыт = топ-N трюков/связок по EXP (N = max_workout_logs из LevelExperience),
    # каждый шаг связки идет в топ-8 очков
    sport_results: list[float] = []
    xp_results: list[int] = []
    for log, _t in pairs:
        if not log.combo_id:
            sport_results.append(log.calculated_points)
            xp_results.append(log.calculated_xp)
    for cid, steps in combos.items():
        for s_log, _t in steps:
            sport_results.append(s_log.calculated_points)
        last_log = steps[-1][0]
        sport_results.append(last_log.calculated_points + (last_log.combo_bonus or 0.0))
        xp_results.append(combo_exp.get(cid, 0))

    level_reqs = await session.get(LevelExperience, student.level)
    categories_count = len({t.category for t in tricks.values() if t})

    sport_results.sort(reverse=True)
    base_points = sum(sport_results[:8]) / 8.0
    penalty = 0.0
    if level_reqs:
        missing = max(0, level_reqs.min_trick_categories - categories_count)
        penalty = missing * 3.0
    live_score = round(base_points - penalty, 2)

    xp_results.sort(reverse=True)
    live_xp = sum(
        xp_results[: (level_reqs.max_workout_logs if level_reqs else 10)]
    )

    max_logs = level_reqs.max_workout_logs if level_reqs else 10
    min_categories = level_reqs.min_trick_categories if level_reqs else 3

    def _chk(done, total):
        return "✅" if done >= total else "⚠️"

    lines.append("")
    lines.append("Предварительно:")
    pts_line = f"🏅 {live_score:.2f}"
    if penalty:
        pts_line += f" (- штраф {penalty:.1f})"
    lines.append(
        f"{pts_line} | ✨ +{live_xp} EXP\n"
        f"Зачет: ✨ {min(len(xp_results), max_logs)}/{max_logs} {_chk(len(xp_results), max_logs)} • "
        f"🏅 {min(len(sport_results), 8)}/8 {_chk(len(sport_results), 8)} • "
        f"Кат: {categories_count}/{min_categories} {_chk(categories_count, min_categories)}"
    )

    card_text = "\n".join(lines)

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
