"""Статистика: список учеников с сортировкой по уровню и профиль с разбивкой по тренировкам."""

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.filters.callback_data import CallbackData
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from models import User, Workout, WorkoutRegistration, TrickLog, Trick, LevelExperience
from handlers.coach.helpers import get_coach_menu_for, draw_progress_bar

router = Router()


# --- Фабрика callback-данных статистики ---
class StatsAction(CallbackData, prefix="stat"):
    action: str  # "stats_student" — профиль ученика, "stats_back" — назад к списку
    student_id: int = 0


def _students_kb(students: list[User]) -> InlineKeyboardMarkup:
    """Инлайн-кнопки с именами учеников"""
    builder = InlineKeyboardBuilder()
    for st in students:
        builder.button(
            text=f"{st.full_name} (Ур. {st.level})",
            callback_data=StatsAction(action="stats_student", student_id=st.id),
        )
    builder.adjust(1)
    return builder.as_markup()


@router.message(F.text == "📊 Статистика")
async def coach_stats(message: Message, session: AsyncSession):
    """Список всех учеников, отсортированный по уровню (по убыванию)"""
    students = (
        (
            await session.execute(
                select(User)
                .where(User.role == "student")
                .order_by(User.level.desc(), User.xp.desc())
            )
        )
        .scalars()
        .all()
    )

    if not students:
        await message.answer(
            "📊 <b>Статистика</b>\n\nУчеников пока нет.",
            reply_markup=await get_coach_menu_for(session),
            parse_mode="HTML",
        )
        return

    lines = ["📊 <b>Статистика учеников</b> (сортировка по уровню)\n"]

    for i, st in enumerate(students, start=1):
        best_points = await _best_training_points(session, st.id)
        bp = f"{best_points:.1f}" if best_points is not None else "—"
        next_reqs = await session.get(LevelExperience, st.level + 1)
        if next_reqs:
            req_xp = next_reqs.required_xp
            percent = min(100.0, (st.xp / req_xp * 100) if req_xp > 0 else 0.0)
            bar = draw_progress_bar(percent)
        else:
            bar = "🏆 Максимальный уровень достигнут!"
        lines.append(
            f"{i}. 👤 <b>{st.full_name}</b> - {st.level} LVL, BP : {bp}\n" f"{bar}"
        )

    await message.answer(
        "\n".join(lines),
        reply_markup=_students_kb(students),
        parse_mode="HTML",
    )


async def _workout_caused_level_up(
    session: AsyncSession, student: User, workout_id: int, training_xp: int
) -> bool:
    """Проверяет по логам: boosted ли уровень этой тренировкой.
    Уровень растёт, когда накопленный XP (без этой тренировки) был ниже порога,
    а с этой тренировкой — достиг"""
    # XP всех ДРУГИХ закрытых тренировок ученика
    other_regs = (
        (
            await session.execute(
                select(WorkoutRegistration.training_xp)
                .join(Workout, Workout.id == WorkoutRegistration.workout_id)
                .where(
                    WorkoutRegistration.student_id == student.id,
                    WorkoutRegistration.workout_id != workout_id,
                    Workout.status == "closed",
                )
            )
        )
        .scalars()
        .all()
    )
    xp_before = sum(other_regs)
    xp_after = xp_before + training_xp

    next_reqs = await session.get(LevelExperience, student.level)
    if not next_reqs:
        return False

    threshold = next_reqs.required_xp
    # Ученик сейчас на уровне L: значит порог достижения уровня L был превышен.
    # Тренировка вызвала lvl up, если xp_before < threshold <= xp_after
    return xp_before < threshold <= xp_after


async def _best_training_points(session: AsyncSession, student_id: int) -> float | None:
    """Лучший (максимальный) результат training_points спортсмена по всем тренировкам"""
    return await session.scalar(
        select(func.max(WorkoutRegistration.training_points)).where(
            WorkoutRegistration.student_id == student_id
        )
    )


async def _progress_line(student: User, session: AsyncSession) -> str:
    """Строка: имя — уровень, BP : лучший training_points; ниже голый бар прогресса опыта"""
    best_points = await _best_training_points(session, student.id)
    bp = f"{best_points:.1f}" if best_points is not None else "—"
    header = f"👤 <b>{student.full_name}</b> - {student.level} LVL, BP : {bp}"

    next_reqs = await session.get(LevelExperience, student.level + 1)
    if not next_reqs:
        return f"{header}\n🏆 Максимальный уровень достигнут!"
    req_xp = next_reqs.required_xp
    percent = min(100.0, (student.xp / req_xp * 100) if req_xp > 0 else 0.0)
    return f"{header}\n{draw_progress_bar(percent)}"


async def build_student_profile_text(student: User, session: AsyncSession) -> str:
    """Полный текст профиля ученика: шапка с BP и баром + разбивка по тренировкам.
    Используется и тренерской статистикой, и кнопкой «Мой профиль» у ученика"""
    progress = await _progress_line(student, session)

    # Все тренировки, где участвовал ученик (свежие сверху)
    regs = (
        await session.execute(
            select(WorkoutRegistration, Workout)
            .join(Workout, Workout.id == WorkoutRegistration.workout_id)
            .where(WorkoutRegistration.student_id == student.id)
            .order_by(Workout.date.desc())
        )
    ).all()

    parts = [progress, ""]

    if not regs:
        parts.append("Тренировок пока не было.")
        return "\n".join(parts)

    parts.append("📊 <b>Разбивка по тренировкам:</b>")

    for reg, workout in regs:
        date_str = workout.date.strftime("%d.%m.%Y") if workout.date else "—"
        lvl_up_note = ""
        # Если после этой тренировки у ученика вырос уровень — фиксируем это в разбивке
        if workout.status == "closed" and reg.training_xp:
            lvl_up_note = (
                "\n🔥 Уровень повышен на этой тренировке!"
                if await _workout_caused_level_up(
                    session, student, workout.id, reg.training_xp
                )
                else ""
            )
        parts.append(
            f"\n🏋️ <b>Тренировка от {date_str}</b> "
            f"(№{workout.id}, {workout.status})\n"
            f"Итог: 🏅 {reg.training_points:.1f} очков | ✨ +{reg.training_xp} XP{lvl_up_note}"
        )

        logs = (
            await session.execute(
                select(TrickLog, Trick)
                .join(Trick, Trick.id == TrickLog.trick_id)
                .where(
                    TrickLog.workout_id == workout.id,
                    TrickLog.student_id == student.id,
                )
                .order_by(TrickLog.id)
            )
        ).all()

        if not logs:
            parts.append("Трюки не записаны.")
        else:
            for log, trick in logs:
                mult_note = ""
                if log.execution_multiplier and log.execution_multiplier != 1.0:
                    mult_note = f" (качество ×{log.execution_multiplier:.2f})"
                parts.append(
                    f" • {trick.name}: {log.quantity} кон. (дорожка {log.cone_line}){mult_note} — "
                    f"{log.calculated_points:.1f} очк., +{log.calculated_xp} XP"
                )

    return "\n".join(parts)


@router.callback_query(StatsAction.filter(F.action == "stats_student"))
async def stats_student_profile(
    callback: CallbackQuery, callback_data: StatsAction, session: AsyncSession
):
    """Профиль ученика: разбивка по каждой тренировке (трюки, очки, опыт)"""
    student = await session.get(User, callback_data.student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return

    text = await build_student_profile_text(student, session)

    builder = InlineKeyboardBuilder()
    builder.button(
        text="⬅️ К списку учеников", callback_data=StatsAction(action="stats_back")
    )
    await callback.message.edit_text(
        text, reply_markup=builder.as_markup(), parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(StatsAction.filter(F.action == "stats_back"))
async def stats_back_to_list(callback: CallbackQuery, session: AsyncSession):
    """Возврат к списку учеников"""
    students = (
        (
            await session.execute(
                select(User)
                .where(User.role == "student")
                .order_by(User.level.desc(), User.xp.desc())
            )
        )
        .scalars()
        .all()
    )

    text = "📊 <b>Статистика учеников</b> (сортировка по уровню)\n\n"
    if students:
        text += "\n".join(
            f"{i}. 👤 <b>{st.full_name}</b> — Ур. {st.level}, {st.xp} XP ({st.title})"
            for i, st in enumerate(students, start=1)
        )
    else:
        text += "Учеников пока нет."

    await callback.message.edit_text(
        text, reply_markup=_students_kb(list(students)), parse_mode="HTML"
    )
    await callback.answer()
