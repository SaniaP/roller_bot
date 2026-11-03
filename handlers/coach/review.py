"""Проверка самостоятельных трюков учеников (needs_review)."""

import re

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from models import User, WorkoutRegistration, TrickLog, Trick
from states.coach_states import WorkoutVerificationStates
from keyboards.coach_kb import (
    get_review_students_kb,
    get_review_student_card_kb,
    WorkoutAction,
    ReviewAction,
)
from calculator import calculate_single_trick
from handlers.coach.helpers import get_coach_menu_for, percentage_to_multiplier

router = Router()


async def _get_pending_review(session: AsyncSession, workout_id: int):
    """Возвращает dict {student_id: [TrickLog]} — все неподтвержденные трюки активной тренировки"""
    pending_logs = (
        (
            await session.execute(
                select(TrickLog).where(
                    TrickLog.workout_id == workout_id,
                    TrickLog.needs_review == True,  # noqa: E712
                )
            )
        )
        .scalars()
        .all()
    )

    grouped = {}
    for log in pending_logs:
        grouped.setdefault(log.student_id, []).append(log)
    return grouped


def _group_logs_by_combo(logs: list[TrickLog]) -> dict:
    """Группирует логи: связки (combo_id) одним элементом, одиночные трюки — по одному.
    Возвращает список элементов вида:
      {"type": "combo", "combo_id": ..., "logs": [...]} или
      {"type": "single", "log": TrickLog}
    Связка считается одним элементом только если все её шаги нуждаются в проверке.
    """
    combos: dict[str, list[TrickLog]] = {}
    singles: list[TrickLog] = []
    for log in sorted(logs, key=lambda l: (l.combo_id or "", l.combo_step or 0)):
        if log.combo_id:
            combos.setdefault(log.combo_id, []).append(log)
        else:
            singles.append(log)

    items = [
        {"type": "combo", "combo_id": cid, "logs": steps}
        for cid, steps in combos.items()
    ]
    items += [{"type": "single", "log": log} for log in singles]
    return items


async def _pending_for_student(
    session: AsyncSession, workout_id: int, student_id: int
) -> list[TrickLog]:
    """Неподтвержденные трюки конкретного ученика на тренировке"""
    return list(
        (
            await session.execute(
                select(TrickLog).where(
                    TrickLog.workout_id == workout_id,
                    TrickLog.student_id == student_id,
                    TrickLog.needs_review == True,  # noqa: E712
                )
            )
        )
        .scalars()
        .all()
    )


async def _send_review_list(
    target: Message, session: AsyncSession, workout_id: int, edit: bool
):
    """Рисует/отправляет экран списка самостоятельных учеников, ждущих подтверждения.
    edit=True — правит существующее сообщение (из callback), False — отправляет новое"""
    grouped = await _get_pending_review(session, workout_id)

    if not grouped:
        text = "✅ Все самостоятельные трюки подтверждены. Список пуст."
        kb = None
    else:
        students = []
        for sid in grouped:
            st = await session.get(User, sid)
            if st:
                students.append(st)
        text = (
            "🔔 <b>Проверка тренировок</b>\n\n"
            "Спортсмены, отправившие трюки самостоятельно и ждущие подтверждения:\n"
            "Нажмите на имя, чтобы проверить трюки по одному, либо подтвердите всех сразу."
        )
        kb = get_review_students_kb(workout_id, students)

    if edit:
        await target.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=kb, parse_mode="HTML")


async def _send_student_card(
    target: Message, session: AsyncSession, workout_id: int, student_id: int, edit: bool
):
    """Рисует/отправляет карточку самостоятельного ученика с его неподтвержденными трюками.
    edit=True — правит существующее сообщение (из callback), False — отправляет новое"""
    logs = await _pending_for_student(session, workout_id, student_id)

    if not logs:
        text = "✅ Все трюки этого ученика подтверждены!"
        kb = None
    else:
        student = await session.get(User, student_id)
        trick_ids = {log.trick_id for log in logs}
        tricks = {
            t.id: t
            for t in (
                await session.execute(select(Trick).where(Trick.id.in_(trick_ids)))
            )
            .scalars()
            .all()
        }
        # Группируем: связки одним элементом, одиночные трюки — отдельно
        items = _group_logs_by_combo(list(logs))

        text = (
            f"👤 <b>{student.full_name}</b> — самостоятельные трюки и связки\n\n"
            f"🧩 — связка (подтверждается целиком), 🎯 — одиночный трюк.\n"
            f"Нажмите на элемент, чтобы задать поправку исполнения и подтвердить, "
            f"или подтвердите все сразу."
        )
        kb = get_review_student_card_kb(workout_id, student_id, items, tricks)

    if edit:
        await target.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=kb, parse_mode="HTML")


async def _confirm_logs(
    session: AsyncSession, logs: list[TrickLog], execution_multiplier: float = 1.0
):
    """Подтверждает логи: устанавливает execution_multiplier и пересчитывает очки/XP.
    Если среди логов есть связка (общий combo_id) — она пересчитывается ПОСЛЕДОВАТЕЛЬНО:
    сначала чистые баллы всех шагов с новой поправкой, затем combo_bonus каждого шага
    по формуле Result = f(prev_result, Y), т.к. каждый шаг зависит от предыдущего."""
    from calculator import calculate_combo_step

    # Сгруппируем: связки пересчитываем особым образом, одиночные — напрямую
    by_combo: dict[str, list[TrickLog]] = {}
    singles: list[TrickLog] = []
    for log in logs:
        if log.combo_id:
            by_combo.setdefault(log.combo_id, []).append(log)
        else:
            singles.append(log)

    # --- Одиночные трюки ---
    for log in singles:
        await _recalc_log(session, log, execution_multiplier)

    # --- Связки: шаг 1..N последовательно ---
    for combo_id, steps in by_combo.items():
        steps.sort(key=lambda s: s.combo_step or 0)
        prev_result: float | None = None
        for step in steps:
            # 1) пересчитываем чистый балл трюка с новой поправкой
            await _recalc_log(session, step, execution_multiplier)
            y = step.calculated_points
            # 2) combo_bonus по формуле: Result = ((X+Y)/2 + bump) * mult; X = prev Result
            if prev_result is None:
                # шаг 1: бонуса нет, X для следующего шага = чистый балл
                step.combo_bonus = 0.0
                prev_result = y
            else:
                result = calculate_combo_step(prev_result, y)
                step.combo_bonus = round(result - y, 2)
                prev_result = result
    await session.commit()


async def _recalc_log(
    session: AsyncSession, log: TrickLog, execution_multiplier: float
):
    """Пересчет одного лога с новой поправкой + обновление итогов регистрации"""
    trick = await session.get(Trick, log.trick_id)
    student = await session.get(User, log.student_id)
    if not trick or not student:
        return

    new_points, new_xp = await calculate_single_trick(
        trick=trick,
        cone_line=log.cone_line,
        quantity=log.quantity,
        execution_multiplier=execution_multiplier,
        skater_level=student.level,
        db_session=session,
    )
    log.execution_multiplier = execution_multiplier
    old_points, old_xp = log.calculated_points, log.calculated_xp
    log.calculated_points = new_points
    log.calculated_xp = new_xp
    log.needs_review = False

    # Пересчитываем накопленные результаты в регистрации на тренировку
    reg = (
        await session.execute(
            select(WorkoutRegistration).where(
                WorkoutRegistration.workout_id == log.workout_id,
                WorkoutRegistration.student_id == log.student_id,
            )
        )
    ).scalar_one_or_none()
    if reg:
        reg.training_points += new_points - old_points
        reg.training_xp += new_xp - old_xp


async def _confirm_combo_steps(
    session: AsyncSession, steps: list[TrickLog], percents: list[int]
):
    """Подтверждение связки с ОТДЕЛЬНОЙ поправкой на каждый шаг.
    percents[i] — поправка в % для i-го шага (в порядке combo_step).
    Сначала пересчитываем чистые баллы всех шагов, потом последовательно combo_bonus."""
    from calculator import calculate_combo_step

    # 1) чистые баллы каждого шага со своей поправкой
    for log, pct in zip(steps, percents):
        mult = percentage_to_multiplier(pct)
        trick = await session.get(Trick, log.trick_id)
        student = await session.get(User, log.student_id)
        if not trick or not student:
            continue
        new_points, new_xp = await calculate_single_trick(
            trick=trick,
            cone_line=log.cone_line,
            quantity=log.quantity,
            execution_multiplier=mult,
            skater_level=student.level,
            db_session=session,
        )
        old_points, old_xp = log.calculated_points, log.calculated_xp
        log.execution_multiplier = mult
        log.calculated_points = new_points
        log.calculated_xp = new_xp
        log.needs_review = False

        reg = (
            await session.execute(
                select(WorkoutRegistration).where(
                    WorkoutRegistration.workout_id == log.workout_id,
                    WorkoutRegistration.student_id == log.student_id,
                )
            )
        ).scalar_one_or_none()
        if reg:
            reg.training_points += new_points - old_points
            reg.training_xp += new_xp - old_xp

    # 2) пересчет цепочки бонусов: Result = f(prev Result, Y)
    prev_result: float | None = None
    for step in steps:
        y = step.calculated_points
        if prev_result is None:
            step.combo_bonus = 0.0
            prev_result = y
        else:
            result = calculate_combo_step(prev_result, y)
            step.combo_bonus = round(result - y, 2)
            prev_result = result

    await session.commit()


# ==========================================
# ХЕНДЛЕРЫ ПРОВЕРКИ
# ==========================================


@router.callback_query(WorkoutAction.filter(F.action == "review_workout"))
async def coach_review_workouts(
    callback: CallbackQuery, callback_data: WorkoutAction, session: AsyncSession
):
    """Вход в меню проверки самостоятельных трюков (кнопка в меню активной тренировки)"""
    workout_id = callback_data.workout_id

    grouped = await _get_pending_review(session, workout_id)
    if not grouped:
        await callback.answer(
            "✅ Новых самостоятельных трюков для подтверждения нет.", show_alert=True
        )
        return

    await _send_review_list(callback.message, session, workout_id, edit=True)
    await callback.answer()


@router.callback_query(ReviewAction.filter(F.action == "rv_back"))
async def review_back_to_list(
    callback: CallbackQuery, callback_data: ReviewAction, session: AsyncSession
):
    """Возврат к списку учеников, ждущих подтверждения"""
    await _send_review_list(
        callback.message, session, callback_data.workout_id, edit=True
    )
    await callback.answer()


@router.callback_query(ReviewAction.filter(F.action == "rv_student"))
async def review_show_student(
    callback: CallbackQuery, callback_data: ReviewAction, session: AsyncSession
):
    """Карточка самостоятельного ученика: его неподтвержденные трюки"""
    if not (await _get_pending_review(session, callback_data.workout_id)).get(
        callback_data.student_id
    ):
        await callback.answer(
            "Все трюки этого ученика уже подтверждены!", show_alert=True
        )
        await _send_review_list(
            callback.message, session, callback_data.workout_id, edit=True
        )
        return

    await _send_student_card(
        callback.message,
        session,
        callback_data.workout_id,
        callback_data.student_id,
        edit=True,
    )
    await callback.answer()


@router.callback_query(ReviewAction.filter(F.action == "rv_edit_combo"))
async def review_edit_combo(
    callback: CallbackQuery, callback_data: ReviewAction, state: FSMContext
):
    """Тренер нажал на связку: подтверждаем все её шаги одной поправкой"""
    await state.update_data(
        review_workout_id=callback_data.workout_id,
        review_student_id=callback_data.student_id,
        review_log_id=callback_data.log_id,  # первый шаг — по нему найдем combo_id
        review_is_combo=True,
    )
    await callback.message.edit_text(
        "🧩 <b>Подтверждение связки</b>\n\n"
        "Каждый трюк связки подтверждается ОТДЕЛЬНО.\n"
        "Введите поправки в процентах через пробел — по одной на каждый шаг связки, "
        "в порядке шагов.\n\n"
        "Пример для связки из 3 трюков: <code>0 10 -5</code>\n"
        "→ шаг 1: ×1.0, шаг 2: ×1.1, шаг 3: ×0.95\n\n"
        "• <code>0</code> — чисто (×1.0)\n"
        "• <code>-10</code> — штраф 10%\n"
        "• <code>10</code> — бонус 10%",
        parse_mode="HTML",
    )
    await state.set_state(WorkoutVerificationStates.waiting_for_multiplier)
    await callback.answer()


@router.callback_query(ReviewAction.filter(F.action == "rv_edit_log"))
async def review_edit_log(
    callback: CallbackQuery, callback_data: ReviewAction, state: FSMContext
):
    """Тренер нажал на трюк: запрашиваем поправку исполнения в процентах"""
    await state.update_data(
        review_workout_id=callback_data.workout_id,
        review_student_id=callback_data.student_id,
        review_log_id=callback_data.log_id,
    )
    await callback.message.edit_text(
        "🎯 <b>Подтверждение трюка</b>\n\n"
        "Введите поправку за качество исполнения в процентах:\n"
        "• <code>0</code> — чисто (исполнение 1.0)\n"
        "• <code>-10</code> — штраф 10% за помарки\n"
        "• <code>10</code> или <code>+10</code> — бонус 10% за идеальное исполнение",
        parse_mode="HTML",
    )
    await state.set_state(WorkoutVerificationStates.waiting_for_multiplier)
    await callback.answer()


@router.message(WorkoutVerificationStates.waiting_for_multiplier)
async def review_process_multiplier(
    message: Message, state: FSMContext, session: AsyncSession
):
    """Принимаем поправку(и) в %, подтверждаем трюк/связку с вычисленными множителями"""
    raw = (message.text or "").strip().replace("%", "")
    tokens = raw.split()

    if not tokens:
        await message.answer(
            "⚠️ Введите целое число в процентах: например 0, -10 или 15"
        )
        return

    # Парсим поправки: одно значение — одиночный трюк (или одна поправка на всю
    # связку), несколько значений через пробел — по одному на каждый шаг связки
    percents_input: list[int] = []
    for tok in tokens:
        if not re.match(r"^[+-]?\d+$", tok):
            await message.answer(
                "⚠️ Введите целые числа в процентах через пробел: "
                "один трюк — 0, связка из 3 шагов — 0 10 -5"
            )
            return
        percents_input.append(max(int(tok), -100))

    data = await state.get_data()
    await state.clear()

    workout_id = data["review_workout_id"]
    student_id = data["review_student_id"]
    log_id = data["review_log_id"]
    is_combo = data.get("review_is_combo", False)

    anchor_log = await session.get(TrickLog, log_id)
    if not anchor_log or not anchor_log.needs_review:
        await message.answer(
            "⚠️ Этот трюк уже подтвержден.",
            reply_markup=await get_coach_menu_for(session),
        )
        return

    if is_combo and anchor_log.combo_id:
        # Каждый трюк связки подтверждается СВОЕЙ поправкой (ответ на сообщение):
        # тренер ввел несколько значений через пробел, по одному на шаг
        combo_logs = list(
            (
                await session.execute(
                    select(TrickLog).where(
                        TrickLog.workout_id == workout_id,
                        TrickLog.student_id == student_id,
                        TrickLog.combo_id == anchor_log.combo_id,
                        TrickLog.needs_review == True,  # noqa: E712
                    )
                )
            )
            .scalars()
            .all()
        )
        combo_logs.sort(key=lambda s: s.combo_step or 0)

        percents = percents_input[:]
        # Значений меньше шагов — остаток подтверждаем последним введенным значением
        # (одно значение = одна поправка на все шаги связки)
        while len(percents) < len(combo_logs):
            percents.append(percents[-1])

        # Пересчет: _confirm_logs с пошаговыми поправками
        await _confirm_combo_steps(session, combo_logs, percents)
    else:
        # Одиночный трюк: берем первое значение (лишние игнорируем)
        await _confirm_logs(
            session,
            [anchor_log],
            execution_multiplier=percentage_to_multiplier(percents_input[0]),
        )

    # Если у ученика еще остались неподтвержденные трюки — возвращаемся к его карточке,
    # иначе он исчезает из списка и возвращаемся к списку учеников
    remaining = await _pending_for_student(session, workout_id, student_id)

    if remaining:
        await _send_student_card(message, session, workout_id, student_id, edit=False)
    else:
        await message.answer("✅ Все трюки этого ученика подтверждены!")
        await _send_review_list(message, session, workout_id, edit=False)


@router.callback_query(ReviewAction.filter(F.action == "rv_confirm_all_student"))
async def review_confirm_all_student(
    callback: CallbackQuery, callback_data: ReviewAction, session: AsyncSession
):
    """Подтвердить все трюки конкретного ученика"""
    logs = await _pending_for_student(
        session, callback_data.workout_id, callback_data.student_id
    )

    if not logs:
        await callback.answer(
            "У этого ученика нет трюков на подтверждение", show_alert=True
        )
        await _send_review_list(
            callback.message, session, callback_data.workout_id, edit=True
        )
        return

    await _confirm_logs(session, logs)
    await callback.answer(f"Подтверждено трюков: {len(logs)}")
    await _send_review_list(
        callback.message, session, callback_data.workout_id, edit=True
    )


@router.callback_query(ReviewAction.filter(F.action == "rv_confirm_all"))
async def review_confirm_all(
    callback: CallbackQuery, callback_data: ReviewAction, session: AsyncSession
):
    """Подтвердить все трюки всех ждущих учеников текущей тренировки"""
    grouped = await _get_pending_review(session, callback_data.workout_id)
    all_logs = [log for logs in grouped.values() for log in logs]

    if not all_logs:
        await callback.answer("Нет трюков на подтверждение", show_alert=True)
        return

    await _confirm_logs(session, all_logs)
    await callback.answer(f"Подтверждено трюков: {len(all_logs)}")
    await _send_review_list(
        callback.message, session, callback_data.workout_id, edit=True
    )
