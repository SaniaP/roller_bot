"""Пошаговый инлайн-ввод трюков тренером (FSM: дорожка -> количество -> сохранение)."""

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from models import User, Trick, TrickLog
from states.coach_states import WorkoutCreationStates
from keyboards.coach_kb import (
    get_cancel_keyboard,
    get_track_coeff_kb,
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
from handlers.coach.workout import show_skater_card_or_main_list

router = Router()


@router.callback_query(TrickAction.filter(F.action == "choose_cat"))
async def process_choose_category(
    callback: CallbackQuery, callback_data: TrickAction, session: AsyncSession
):
    categories = (
        (await session.execute(select(Trick.category).distinct())).scalars().all()
    )

    await callback.message.edit_text(
        "Выберите категорию трюка:",
        reply_markup=get_categories_kb(
            callback_data.workout_id, callback_data.student_id, list(categories)
        ),
    )


@router.callback_query(TrickAction.filter(F.action == "choose_trick"))
async def process_choose_trick(
    callback: CallbackQuery, callback_data: TrickAction, session: AsyncSession
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

    await callback.message.edit_text(
        f"Категория: {callback_data.category.upper()}. Выберите трюк:",
        reply_markup=get_tricks_by_cat_kb(
            callback_data.workout_id, callback_data.student_id, list(tricks)
        ),
    )


@router.callback_query(TrickAction.filter(F.action == "log_trick"))
async def start_fsm_trick_params(
    callback: CallbackQuery, callback_data: TrickAction, state: FSMContext
):
    """Переходим в FSM, сохраняем контекст и запрашиваем дорожку"""
    await state.update_data(
        workout_id=callback_data.workout_id,
        student_id=callback_data.student_id,
        trick_id=callback_data.trick_id,
    )

    await callback.message.edit_text(
        "Выберите размер дорожки (конусов):", reply_markup=get_track_coeff_kb()
    )
    await state.set_state(WorkoutCreationStates.waiting_for_track_coeff)


@router.callback_query(
    WorkoutCreationStates.waiting_for_track_coeff, F.data.startswith("cone_")
)
async def process_fsm_track_coeff(callback: CallbackQuery, state: FSMContext):
    cone_size = callback.data.split("_")[1]  # Извлекаем "50", "80" или "120"
    await state.update_data(cone_line=cone_size)

    await callback.message.delete()
    await callback.message.answer(
        "🔢 **Введите результаты выполнения**\n\n"
        "Отправьте количество конусов. Если нужно учесть чистоту/качество исполнения трюка, "
        "укажите через запятую поправку в процентах.\n\n"
        "**Примеры ввода:**\n"
        "• `9` [хорошее исполнение]\n"
        "• `8,-10` — штраф 10% за помарки и недачеты в технике\n"
        "• `11,20` — 20% бонус за идеальное исполнение трюка",
        reply_markup=get_cancel_keyboard(),
        parse_mode="Markdown",
    )
    await state.set_state(WorkoutCreationStates.waiting_for_quantity)


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
    await state.clear()  # Очищаем стейт FSM, возвращаемся в инлайн-меню

    workout_id = data["workout_id"]
    student_id = data["student_id"]
    trick_id = data["trick_id"]
    cone_line = data["cone_line"]

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

    await message.answer(
        f"✅ Трюк <b>{trick.name}</b> успешно добавлен!\n\n"
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
