"""Общий поток выбора и внесения трюка для тренера И ученика (DRY).

Тренер и студент проходят одни и те же шаги:
    выбор категории -> выбор трюка -> ввод количества конусов -> сохранение в TrickLog

Различия инкапсулированы в одном флаге by_student:
- by_student=False (тренер): стейт WorkoutCreationStates.waiting_for_quantity,
  сохранение с needs_review=False (сразу засчитано), можно указать поправку
  качества («9», «8,-10», «11,20»), после сохранения — кнопки тренера.
- by_student=True (ученик): стейт StudentStates.waiting_for_cones,
  сохранение с needs_review=True (ждет подтверждения тренером), ввод — только
  число конусов, после сохранения — меню ученика.

Всю навигацию несут callback'и фабрики TrickAction (prefix="trck"),
у ученика — те же callback'и с by_student=True.
"""

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import User, Trick, TrickLog
from keyboards.coach_kb import (
    TrickAction,
    WorkoutAction,
    get_categories_kb,
    get_tricks_by_cat_kb,
    get_cones_input_kb,
)
from calculator import calculate_single_trick
from handlers.coach.helpers import (
    parse_quantity_input,
    percentage_to_multiplier,
    format_accuracy_text,
)
from handlers.track_state import DEFAULT_CONE_LINE, cone_line_text
from states.coach_states import WorkoutCreationStates
from states.workout_states import CoachWorkoutStates
from states import StudentStates

router = Router()

# Состояние ожидания количества для каждой роли
_WAIT_STATES = {
    False: WorkoutCreationStates.waiting_for_quantity,  # тренер
    True: StudentStates.waiting_for_cones,  # ученик
}
# Состояние «активной тренировки», в которое возвращаемся после отмены/сохранения
_ACTIVE_STATES = {
    False: CoachWorkoutStates.training_active,
    True: StudentStates.training_active,
}

_QUANTITY_PROMPT = "{cone}\n\n" "🔢 Отправьте количество конусов.{extra}"
_STUDENT_QUANTITY_PROMPT = "{cone}\n\n" "🔢 Отправьте количество конусов."


# =====================================================================
# УНИВЕРСАЛЬНЫЕ ХЭНДЛЕРЫ (тренер + ученик, разделение по by_student)
# =====================================================================


@router.callback_query(TrickAction.filter(F.action == "choose_cat"))
async def process_choose_category(
    callback: CallbackQuery,
    callback_data: TrickAction,
    state: FSMContext,
    session: AsyncSession,
):
    """Экран выбора категории (для тренера и для ученика)."""
    categories = (
        (await session.execute(select(Trick.category).distinct())).scalars().all()
    )
    cone_line = (await state.get_data()).get("cone_line", DEFAULT_CONE_LINE)

    await callback.message.edit_text(
        f"Выберите категорию трюка.\n{cone_line_text(cone_line)}",
        reply_markup=get_categories_kb(
            callback_data.workout_id,
            callback_data.student_id,
            list(categories),
            by_student=callback_data.by_student,
            cone_line=cone_line,
        ),
    )
    await callback.answer()


@router.callback_query(TrickAction.filter(F.action == "coneset"))
async def process_cone_line_switch(
    callback: CallbackQuery,
    callback_data: TrickAction,
    state: FSMContext,
    session: AsyncSession,
):
    """Смена дорожки прямо на экране категорий (запоминается в FSM)."""
    cone_line = callback_data.line or DEFAULT_CONE_LINE
    await state.update_data(cone_line=cone_line)

    categories = (
        (await session.execute(select(Trick.category).distinct())).scalars().all()
    )
    await callback.message.edit_text(
        f"Выберите категорию трюка.\n{cone_line_text(cone_line)}",
        reply_markup=get_categories_kb(
            callback_data.workout_id,
            callback_data.student_id,
            list(categories),
            by_student=callback_data.by_student,
            cone_line=cone_line,
        ),
    )
    await callback.answer(f"Дорожка: {cone_line} см")


@router.callback_query(TrickAction.filter(F.action == "choose_trick"))
async def process_choose_trick(
    callback: CallbackQuery,
    callback_data: TrickAction,
    state: FSMContext,
    session: AsyncSession,
):
    tricks = (
        (
            await session.execute(
                select(Trick).where(Trick.category == callback_data.category)
            )
        )
        .scalars()
        .all()
    )

    cone_line = (await state.get_data()).get("cone_line", DEFAULT_CONE_LINE)

    await callback.message.edit_text(
        f"Категория: {callback_data.category.upper()}.\n{cone_line_text(cone_line)}\n\n"
        "Выберите трюк:",
        reply_markup=get_tricks_by_cat_kb(
            callback_data.workout_id,
            callback_data.student_id,
            list(tricks),
            by_student=callback_data.by_student,
        ),
    )
    await callback.answer()


@router.callback_query(TrickAction.filter(F.action == "log_trick"))
async def start_trick_quantity_input(
    callback: CallbackQuery, callback_data: TrickAction, state: FSMContext
):
    """Трюк выбран: запоминаем контекст и сразу запрашиваем количество конусов.
    Дорожка берется из FSM (по умолчанию 80, меняется на экране категорий)."""
    by_student = callback_data.by_student
    cone_line = (await state.get_data()).get("cone_line", DEFAULT_CONE_LINE)
    await state.update_data(
        workout_id=callback_data.workout_id,
        student_id=callback_data.student_id,
        trick_id=callback_data.trick_id,
        cone_line=cone_line,
        by_student=by_student,
    )
    await state.set_state(_WAIT_STATES[by_student])

    if by_student:
        prompt = _STUDENT_QUANTITY_PROMPT.format(cone=cone_line_text(cone_line))
    else:
        prompt = _QUANTITY_PROMPT.format(
            cone=cone_line_text(cone_line),
            extra=(" (можно с поправкой качества: `8,-10` — штраф / `11,20` — бонус)"),
        )

    await callback.message.edit_text(
        prompt,
        parse_mode="Markdown",
        reply_markup=get_cones_input_kb(
            callback_data.workout_id,
            callback_data.student_id,
            by_student=by_student,
        ),
    )
    await callback.answer()


# =====================================================================
# ОТМЕНА ВВОДА (защита от зависания FSM)
# =====================================================================


@router.callback_query(
    TrickAction.filter(F.action == "cancel_input"),
)
async def process_cancel_input(
    callback: CallbackQuery,
    callback_data: TrickAction,
    state: FSMContext,
    session: AsyncSession, # Добавляем сессию для вызова карточки
):
    """Явная кнопка «Отмена» (тренер и ученик)."""
    by_student = callback_data.by_student
    workout_id = callback_data.workout_id
    student_id = callback_data.student_id

    # 1. Если отменяет УЧЕНИК
    if by_student:
        from keyboards.skater_kb import get_student_training_menu
        
        # Сбрасываем состояние в активную тренировку ученика
        await state.clear()
        await state.set_state(StudentStates.training_active)
        await state.update_data(
            current_workout_id=workout_id,
        )
        
        # Удаляем старое инлайн-сообщение ввода трюка
        try:
            await callback.message.delete()
        except Exception:
            pass
            
        # Отправляем чистое Reply-меню активной тренировки
        await callback.message.answer(
            "↩️ Ввод трюка отменен.",
            reply_markup=get_student_training_menu()
        )

    # 2. Если отменяет ТРЕНЕР
    else:
        # Очищаем состояние конструктора трюка
        await state.clear()
        
        # Восстанавливаем данные в FSM тренера, чтобы карточка работала дальше
        await state.update_data(
            current_workout_id=workout_id,
            selected_student_id=student_id
        )
        
        from handlers.coach.workout import show_skater_card_or_main_list
        
        try:
            await callback.answer("Действие отменено")
        except Exception:
            pass

        # Перерисовываем сообщение ввода трюка обратно в карточку этого спортсмена
        await show_skater_card_or_main_list(
            callback,
            TrickAction(
                action="select_skater",
                workout_id=workout_id,
                student_id=student_id,
            ),
            session,
        )


@router.callback_query(
    StateFilter(
        WorkoutCreationStates.waiting_for_quantity, 
        StudentStates.waiting_for_cones
    )
)
async def cancel_input_on_other_action(
    callback: CallbackQuery, 
    state: FSMContext,
    session: AsyncSession  # Добавляем сессию для вызова карточки тренера
):
    """Предохранитель: сбрасывает ожидание текста при клике на любую инлайн-кнопку."""
    data = await state.get_data()
    by_student = data.get("by_student", False)
    
    # Достаем сохраненные ID, чтобы знать, куда возвращать пользователя
    workout_id = data.get("current_workout_id") or data.get("workout_id")
    student_id = data.get("selected_student_id") or data.get("student_id")

    # 1. ЕСЛИ ЭТО УЧЕНИК — гасим старое окно и возвращаем его Reply-меню
    if by_student:
        from keyboards.skater_kb import get_student_training_menu
        
        await state.clear()
        await state.set_state(StudentStates.training_active)
        await state.update_data(current_workout_id=workout_id)
        
        try:
            await callback.message.delete()
        except Exception:
            pass
            
        await callback.message.answer(
            "↩️ Ввод количества конусов отменен.",
            reply_markup=get_student_training_menu()
        )
        await callback.answer("Ввод отменен")

    # 2. ЕСЛИ ЭТО ТРЕНЕР — возвращаем его назад в карточку спортсмена
    else:
        await state.clear()
        await state.update_data(
            current_workout_id=workout_id,
            selected_student_id=student_id
        )
        
        from handlers.coach.workout import show_skater_card_or_main_list
        
        await callback.answer("Ввод отменен")
        
        # Заменяем текущее сообщение обратно на карточку
        await show_skater_card_or_main_list(
            callback,
            TrickAction(
                action="select_skater",
                workout_id=workout_id,
                student_id=student_id,
            ),
            session,
        )


# =====================================================================
# СОХРАНЕНИЕ РЕЗУЛЬТАТА (тренер и ученик, разделение по by_student)
# =====================================================================


@router.message(WorkoutCreationStates.waiting_for_quantity)
async def process_coach_quantity_and_save(
    message: Message, state: FSMContext, session: AsyncSession
):
    """Ввод количества тренером: число + опциональная поправка качества."""
    await _save_trick_log(message, state, session, by_student=False)


@router.message(StudentStates.waiting_for_cones)
async def process_student_cones_and_save(
    message: Message, state: FSMContext, session: AsyncSession
):
    """Ввод количества учеником: только число конусов (без поправок)."""
    await _save_trick_log(message, state, session, by_student=True)


async def _save_trick_log(
    message: Message, state: FSMContext, session: AsyncSession, by_student: bool
):
    """Общая логика сохранения: расчет, TrickLog, вывод итога и кнопок."""
    text_input = (message.text or "").strip().replace(" ", "")

    if by_student:
        # Ученик вводит только целое положительное число конусов
        percentage_mod = None
        try:
            quantity = int(text_input)
            if quantity <= 0:
                raise ValueError
        except ValueError:
            quantity = None
    else:
        # Тренер может указать поправку качества: «9», «8,-10», «11,20»
        parsed = parse_quantity_input(text_input)
        if not parsed:
            await message.answer(
                "⚠️ **Неверный формат ввода!**\n\n"
                "Пожалуйста, введите данные строго по шаблону:\n"
                "• Просто число конусов: `9` (без поправок)\n"
                "• Со штрафом: `8,-10` (минус 10% к качеству)\n"
                "• С бонусом: `11,20` (плюс 20% к качеству)",
                parse_mode="Markdown",
            )
            return
        quantity, percentage_mod = parsed

    if quantity is None:
        await message.answer(
            "⚠️ Пожалуйста, введите корректное число конусов (целое положительное число):"
        )
        return

    execution_multiplier = (
        percentage_to_multiplier(int(percentage_mod)) if percentage_mod else 1.0
    )

    data = await state.get_data()
    # cone_line НЕ сбрасываем: выбранная дорожка действует до следующей смены
    workout_id = data["workout_id"]
    student_id = data["student_id"]
    trick_id = data["trick_id"]
    cone_line = data.get("cone_line", DEFAULT_CONE_LINE)

    student = await session.get(User, student_id)
    trick = await session.get(Trick, trick_id)

    # Расчет очков и EXP (общий для тренера и ученика)
    calc_points, calc_xp = await calculate_single_trick(
        trick=trick,
        cone_line=cone_line,
        quantity=quantity,
        execution_multiplier=execution_multiplier,
        skater_level=student.level,
        db_session=session,
    )

    # Сохраняем лог подходов. Ученик отправляет сам — ждет подтверждения тренера.
    new_log = TrickLog(
        workout_id=workout_id,
        student_id=student_id,
        trick_id=trick_id,
        cone_line=cone_line,
        quantity=quantity,
        execution_multiplier=execution_multiplier,
        calculated_points=calc_points,
        calculated_xp=calc_xp,
        needs_review=by_student,
    )
    session.add(new_log)
    await session.commit()

    # Выходим из режима ожидания количества: иначе все следующие текстовые
    # сообщения продолжают перехватываться хендлером как «количество конусов»
    await state.set_state(_ACTIVE_STATES[by_student])

    if by_student:
        await message.answer(
            f"✨ Трюк отправлен на проверку тренеру!\n\n"
            f"🛹 Трюк: {trick.name}\n"
            f"📏 Дорожка: {cone_line} см\n"
            f"🪚 Пройдено конусов: {quantity} шт.\n\n"
            f"📊 **Ожидаемое начисление:**\n"
            f"• Баллы: +{calc_points}\n"
            f"• Опыт: +{calc_xp} EXP\n\n"
            f"⏳ Очки и EXP засчитаются после подтверждения тренером.",
            reply_markup=_student_training_menu(),
            parse_mode="Markdown",
        )
    else:
        accuracy_text = format_accuracy_text(percentage_mod)
        await message.answer(
            f"✅ Трюк <b>{trick.name}</b> успешно добавлен!\n\n"
            f"📏 Дорожка: {cone_line} см\n"
            f"📊 **Параметры подхода:**\n"
            f"• Количество конусов: <code>{quantity}</code>\n"
            f"• Точность выполнения: <code>{accuracy_text}</code> "
            f"(коэфф. {execution_multiplier:.2f})\n\n"
            f"✨ **Начислено:** +{calc_points} баллов | +{calc_xp} EXP",
            reply_markup=_coach_after_save_kb(workout_id, student_id),
            parse_mode="HTML",
        )


def _coach_after_save_kb(workout_id: int, student_id: int):
    """Кнопки после сохранения тренером: карточка спортсмена / добавить еще трюк."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="⬅️ Карточка спортсмена",
        callback_data=WorkoutAction(
            action="select_skater", workout_id=workout_id, student_id=student_id
        ),
    )
    builder.button(
        text="➕ Добавить трюк",
        callback_data=TrickAction(
            action="choose_cat",
            workout_id=workout_id,
            student_id=student_id,
            by_student=False,
        ),
    )
    return builder.as_markup()


def _student_training_menu():
    """Reply-меню ученика после сохранения трюка."""
    from keyboards.skater_kb import get_student_training_menu

    return get_student_training_menu()
