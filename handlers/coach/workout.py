"""Логика тренировки: старт/продолжение, добавление спортсменов, карточка, финализация."""

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from models import User, Workout, WorkoutRegistration, TrickLog
from states.workout_states import CoachWorkoutStates
from keyboards.coach_kb import (
    get_active_workout_kb,
    get_skater_workout_card_kb,
    WorkoutAction,
)
from calculator import calculate_current_workout_totals, check_level_up
from handlers.coach.helpers import (
    get_coach_menu_for,
    get_active_workout,
    get_workout_students,
    new_workout_now,
)
from keyboards.skater_kb import get_student_main_menu

router = Router()


@router.message(F.text.in_({"🏋️ Начать тренировку", "🏋️ Продолжить тренировку"}))
async def coach_start_workout(
    message: Message, state: FSMContext, session: AsyncSession
):
    """Открытие/продолжение тренировки. Если активная есть — открываем её меню"""
    active_workout = await get_active_workout(session)

    if active_workout:
        # Продолжаем: собираем текущий список спортсменов на занятии
        current_students = await get_workout_students(session, active_workout.id)

        # Выводим сообщение о продолжении тренировки и показываем сохраненный ранее пин-код
        await message.answer(
            f"🏋️‍♂️ **ИДЕТ ТРЕНИРОВКА**\n\n"
            f"PIN-код для подключения учеников: `{active_workout.pin_code}`\n\n"
            f"Список спортсменов на занятии:",
            reply_markup=get_active_workout_kb(active_workout.id, current_students),
            parse_mode="Markdown",
        )
        return

    # --- ЕСЛИ ТРЕНИРОВКИ НЕТ, СОЗДАЕМ НОВУЮ С PIN-КОДОМ ---
    new_workout = new_workout_now()
    session.add(new_workout)
    await session.commit()

    # Переводим тренера в состояние активности тренировки
    await state.set_state(CoachWorkoutStates.training_active)

    await message.answer(
        f"🏋️‍♂️ **Тренировка открыта!**\n\n"
        f"Сообщите ученикам этот PIN-код для подключения:\n"
        f"👉 `{new_workout.pin_code}` 👈 (нажмите, чтобы скопировать)\n\n"
        f"Ученики будут появляться в вашем списке автоматически по мере ввода кода.",
        reply_markup=get_active_workout_kb(new_workout.id, []),
        parse_mode="Markdown",
    )

    # Обновляем reply-меню: пока тренировка открыта, кнопка должна называться
    # «Продолжить тренировку», а не «Начать тренировку»
    await message.answer(
        "🏋️ Тренировка активна.",
        reply_markup=await get_coach_menu_for(session),
    )


@router.callback_query(WorkoutAction.filter(F.action == "add_skater_to_w"))
async def add_skater_from_db_list(
    callback: CallbackQuery, callback_data: WorkoutAction, session: AsyncSession
):
    """Вывод списка спортсменов из БД, которых еще нет в текущей сессии"""
    workout_id = callback_data.workout_id

    # Субквери: кого уже добавили
    added_ids = (
        (
            await session.execute(
                select(WorkoutRegistration.student_id).where(
                    WorkoutRegistration.workout_id == workout_id
                )
            )
        )
        .scalars()
        .all()
    )

    # Достаем остальных
    stmt = select(User).where(User.role == "student")
    if added_ids:
        stmt = stmt.where(User.id.not_in(added_ids))

    students = (await session.execute(stmt)).scalars().all()

    if not students:
        await callback.answer(
            "Все доступные ученики из базы данных уже на тренировке!", show_alert=True
        )
        return

    builder = InlineKeyboardBuilder()
    for skater in students:
        builder.button(
            text=skater.full_name,
            callback_data=WorkoutAction(
                action="select_skater", workout_id=workout_id, student_id=skater.id
            ),
        )
    builder.button(
        text="⬅️ Назад",
        callback_data=WorkoutAction(action="select_skater", workout_id=workout_id),
    )
    builder.adjust(1)

    await callback.message.edit_text(
        "Выберите ученика для добавления в тренировку:",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(WorkoutAction.filter(F.action == "select_skater"))
async def show_skater_card_or_main_list(
    callback: CallbackQuery, callback_data: WorkoutAction, session: AsyncSession
):
    """Ярус карточки спортсмена: показывает текущий лвл, опыт и предварительный балл"""
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

    await callback.message.edit_text(
        text=card_text,
        reply_markup=get_skater_workout_card_kb(workout_id, student_id, list(logs)),
    )


# ==========================================
# ФИНАЛИЗАЦИЯ ТРЕНИРОВКИ И LEVEL UP
# ==========================================


@router.callback_query(WorkoutAction.filter(F.action == "finish_workout"))
async def process_finish_workout(
    callback: CallbackQuery,
    callback_data: WorkoutAction,
    state: FSMContext,
    session: AsyncSession,
):
    workout_id = callback_data.workout_id
    workout = await session.get(Workout, workout_id)

    if not workout:
        await callback.answer("Ошибка: Тренировка не найдена", show_alert=True)
        return

    # 1. Извлекаем все регистрации на эту тренировку
    registrations = (
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

    if not registrations:
        # Если на тренировку никто не записался, просто закрываем её
        workout.status = "closed"
        await session.commit()
        await state.clear()
        await callback.message.delete()
        await callback.message.answer(
            "🏁 Тренировка завершена (учеников не было).", 
            reply_markup=await get_coach_menu_for(session)
        )
        return

    # Собираем все уникальные student_id, которые есть на тренировке
    student_ids = list({reg.student_id for reg in registrations})

    # 2. Загружаем ВСЕХ нужных учеников ОДНИМ запросом через IN
    students_result = await session.execute(
        select(User).where(User.id.in_(student_ids))
    )
    # Создаем словарь {id_ученика: объект_User} для моментального поиска в памяти
    students_dict = {user.id: user for user in students_result.scalars().all()}

    # 3. Загружаем ВСЕ логи трюков для этой тренировки ОДНИМ запросом
    all_logs = (
        (
            await session.execute(
                select(TrickLog).where(TrickLog.workout_id == workout_id)
            )
        )
        .scalars()
        .all()
    )

    # Группируем логи по student_id в оперативной памяти
    logs_by_student = {}
    for log in all_logs:
        logs_by_student.setdefault(log.student_id, []).append(log)

    summary_report = "🏁 ТРЕНИРОВКА ЗАВЕРШЕНА И ПОСЧИТАНА!\n\n"
    bot_id = callback.bot.id

    for reg in registrations:
        # Достаем ученика из созданного словаря без запроса к БД
        student = students_dict.get(reg.student_id)
        if not student:
            continue

        # Достаем логи ученика из словаря
        student_logs = logs_by_student.get(student.id, [])

        # Считаем финальные итоги через калькулятор
        final_sport_score, final_xp_gain = await calculate_current_workout_totals(
            student_logs, student, session
        )

        # Обновляем регистрационную запись тренировки
        reg.training_points = final_sport_score
        reg.training_xp = final_xp_gain

        # Начисляем опыт спортсмену глобально
        student.xp += final_xp_gain

        # Проверяем Level Up
        new_level, level_upgraded = await check_level_up(
            student.level, student.xp, session
        )
        lvl_up_text = ""

        if level_upgraded:
            student.level = new_level
            lvl_up_text = f" 🔥 LEVEL UP! Новый уровень: {new_level}!"

        summary_report += (
            f"🏃‍♂️ {student.full_name}:\n"
            f"Score: {final_sport_score} | EXP: +{final_xp_gain}{lvl_up_text}\n\n"
        )

    # Закрываем тренировку и сохраняем все изменения одним пакетом
    workout.status = "closed"
    await session.commit()

    # Уведомляем всех записанных учеников и возвращаем их в главное меню
    for reg in registrations:
        student = students_dict.get(reg.student_id)
        if not student or not student.telegram_id:
            continue
            
        # Исправленный StorageKey (bot_id вместо bot)
        student_state = FSMContext(
            storage=state.storage,
            key=StorageKey(
                bot_id=bot_id,
                chat_id=student.telegram_id,
                user_id=student.telegram_id,
            ),
        )
        await student_state.clear()
        try:
            await callback.bot.send_message(
                student.telegram_id,
                "🏁 Тренировка завершена тренером.\n"
                f"Ваш итог: {reg.training_points} очков, +{reg.training_xp} EXP.",
                reply_markup=get_student_main_menu(),
            )
        except Exception:
            # Ученик мог заблокировать бота — не прерываем закрытие тренировки
            pass

    # Сбрасываем FSM тренера и возвращаем его в главное меню
    await state.clear()

    await callback.message.delete()
    await callback.message.answer(
        summary_report, reply_markup=await get_coach_menu_for(session)
    )