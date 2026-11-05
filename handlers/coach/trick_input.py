"""Ввод трюков тренером (упрощенный сценарий).

Отдельный шаг выбора дорожки убран: текущая дорожка (по умолчанию 80 см)
показывается прямо на экране категорий и переключается там же. Выбранная
дорожка запоминается в FSM и действует до следующей смены.
"""

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from models import User, Trick, TrickLog
from keyboards.coach_kb import (
    get_categories_kb,
    get_tricks_by_cat_kb,
    WorkoutAction,
    TrickAction,
)
from calculator import calculate_single_trick
from handlers.coach.helpers import (
    parse_quantity_input,
    percentage_to_multiplier,
    format_accuracy_text,
)
from handlers.track_state import DEFAULT_CONE_LINE, cone_line_text, track_row_markup
from states.coach_states import WorkoutCreationStates
from states.workout_states import CoachWorkoutStates
from handlers.coach.workout import show_skater_card_or_main_list

router = Router()

# callback_data смены дорожки: coneset:<workout_id>:<student_id>:<50|80|120>
_CONE_SET_PREFIX = "coneset:{workout_id}:{student_id}:{{line}}"


async def _get_cone_line(state: FSMContext) -> str:
    """Текущая дорожка из FSM (по умолчанию 80)."""
    data = await state.get_data()
    return data.get("cone_line", DEFAULT_CONE_LINE)


async def _render_categories_screen(
    message, session: AsyncSession, workout_id: int, student_id: int, cone_line: str
):
    """Экран выбора категории + строка переключения дорожки."""
    categories = (
        (await session.execute(select(Trick.category).distinct())).scalars().all()
    )
    kb = get_categories_kb(workout_id, student_id, list(categories))
    rows = [
        track_row_markup(
            cone_line,
            _CONE_SET_PREFIX.format(workout_id=workout_id, student_id=student_id),
        )
    ]
    rows += list(kb.inline_keyboard)

    await message.edit_text(
        f"Выберите категорию трюка.\n{cone_line_text(cone_line)}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(TrickAction.filter(F.action == "choose_cat"))
async def process_choose_category(
    callback: CallbackQuery,
    callback_data: TrickAction,
    state: FSMContext,
    session: AsyncSession,
):
    cone_line = await _get_cone_line(state)

    await _render_categories_screen(
        callback.message,
        session,
        callback_data.workout_id,
        callback_data.student_id,
        cone_line,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("coneset:"))
async def process_cone_line_switch(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
):
    """Смена дорожки прямо на экране категорий.
    callback_data: coneset:<workout_id>:<student_id>:<50|80|120>"""
    _, workout_id, student_id, cone_line = callback.data.split(":")
    await state.update_data(cone_line=cone_line)

    await _render_categories_screen(
        callback.message, session, int(workout_id), int(student_id), cone_line
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

    cone_line = await _get_cone_line(state)

    await callback.message.edit_text(
        f"Категория: {callback_data.category.upper()}.\n{cone_line_text(cone_line)}\n\n"
        "Выберите трюк:",
        reply_markup=get_tricks_by_cat_kb(
            callback_data.workout_id, callback_data.student_id, list(tricks)
        ),
    )
    await callback.answer()


@router.callback_query(TrickAction.filter(F.action == "log_trick"))
async def start_fsm_trick_quantity(
    callback: CallbackQuery, callback_data: TrickAction, state: FSMContext
):
    """Трюк выбран: запоминаем контекст и сразу запрашиваем количество конусов.
    Дорожка берется из FSM (по умолчанию 80, меняется на экране категорий)."""
    cone_line = await _get_cone_line(state)
    await state.update_data(
        workout_id=callback_data.workout_id,
        student_id=callback_data.student_id,
        trick_id=callback_data.trick_id,
        cone_line=cone_line,
    )

    await callback.message.edit_text(
        f"{cone_line_text(cone_line)}\n\n"
        "🔢 **Введите результаты выполнения**\n\n"
        "Отправьте количество конусов. Если нужно учесть чистоту/качество исполнения трюка, "
        "укажите через запятую поправку в процентах.\n\n"
        "**Примеры ввода:**\n"
        "• `9` [хорошее исполнение]\n"
        "• `8,-10` — штраф 10% за помарки и недачеты в технике\n"
        "• `11,20` — 20% бонус за идеальное исполнение трюка",
        reply_markup=None,
        parse_mode="Markdown",
    )
    await state.set_state(WorkoutCreationStates.waiting_for_quantity)
    await callback.answer()


@router.message(WorkoutCreationStates.waiting_for_quantity)
async def process_fsm_quantity_and_save(
    message: Message, state: FSMContext, session: AsyncSession
):
    text_input = message.text.strip().replace(" ", "")  # Убираем пробелы

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

    # Вызываем calculator.py с динамическим execution_multiplier
    calc_points, calc_xp = await calculate_single_trick(
        trick=trick,
        cone_line=cone_line,
        quantity=quantity,
        execution_multiplier=execution_multiplier,
        skater_level=student.level,
        db_session=session,
    )

    # Сохраняем в лог подходов с учетом введенной точности
    new_log = TrickLog(
        workout_id=workout_id,
        student_id=student_id,
        trick_id=trick_id,
        cone_line=cone_line,
        quantity=quantity,
        execution_multiplier=execution_multiplier,  # Записываем точный коэфф
        calculated_points=calc_points,
        calculated_xp=calc_xp,
        needs_review=False,  # Тренер вносит лично — подтверждение не требуется
    )
    session.add(new_log)
    await session.commit()

    # Генерируем кнопки возврата
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
            action="choose_cat", workout_id=workout_id, student_id=student_id
        ),
    )

    accuracy_text = format_accuracy_text(percentage_mod)

    # Выходим из режима ожидания количества: иначе все следующие текстовые
    # сообщения тренера (кнопки меню, «📊 Статистика» и т.д.) продолжают
    # перехватываться этим хендлером как «количество конусов»
    await state.set_state(CoachWorkoutStates.training_active)

    await message.answer(
        f"✅ Трюк <b>{trick.name}</b> успешно добавлен!\n\n"
        f"📏 Дорожка: {cone_line} см\n"
        f"📊 **Параметры подхода:**\n"
        f"• Количество конусов: <code>{quantity}</code>\n"
        f"• Точность выполнения: <code>{accuracy_text}</code> (коэфф. {execution_multiplier:.2f})\n\n"
        f"✨ **Начислено:** +{calc_points} баллов | +{calc_xp} XP",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


@router.callback_query(TrickAction.filter(F.action == "delete_log"))
async def delete_last_trick_log(
    callback: CallbackQuery, callback_data: TrickAction, session: AsyncSession
):
    """Отмена ошибочно внесенного трюка"""
    await session.execute(delete(TrickLog).where(TrickLog.id == callback_data.log_id))
    await session.commit()

    await callback.answer("Последний трюк успешно отменен!", show_alert=True)

    # Перерисовываем карточку
    redirect_action = WorkoutAction(
        action="select_skater",
        workout_id=callback_data.workout_id,
        student_id=callback_data.student_id,
    )
    await show_skater_card_or_main_list(callback, redirect_action, session)
