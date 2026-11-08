import logging
import uuid
from typing import Optional
from aiogram import Router, F, types
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, distinct

# Твои реальные модели из проекта
from models import User, Workout, WorkoutRegistration, Trick, TrickLog

# Общая функция построения профиля + инлайн-кнопок деталей тренировок
from handlers.coach.stats import (
    build_student_profile_text,
    _workout_details_kb,
)

# как мы считаем если их не используем?)
from calculator import (
    calculate_single_trick,
    calculate_current_workout_totals,
    check_level_up,
)
from keyboards.skater_kb import (
    get_student_main_menu,
    get_student_training_menu,
)
from handlers.coach.stats import build_student_profile_text
from handlers.coach.helpers import get_trick_categories
from handlers.coach.handbook import _handbook_categories_kb
from handlers.track_state import DEFAULT_CONE_LINE, cone_line_text, track_row_markup
from states import StudentStates
from states.coach_states import ComboBuilderStates
from keyboards.coach_kb import ComboAction

logger = logging.getLogger(__name__)
router = Router()


# =====================================================================
# ХЭНДЛЕРЫ ПОДКЛЮЧЕНИЯ ПО PIN-КОДУ
# =====================================================================


@router.message(F.text == "🔑 Подключиться к тренировке")
async def student_ask_for_pin(
    message: Message, state: FSMContext, session: AsyncSession
):
    """Проверяем профиль и запрашиваем PIN-код тренировки"""
    query = select(User).where(User.telegram_id == message.from_user.id)
    user = (await session.execute(query)).scalar_one_or_none()

    if not user or not user.is_approved:
        await message.answer("❌ Ваша учетная запись еще не одобрена тренером.")
        return

    await state.set_state(StudentStates.waiting_for_pin)
    await message.answer(
        "Введите 4-значный PIN-код тренировки, который вам дал тренер:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена войти")]], resize_keyboard=True
        ),
    )


@router.message(StudentStates.waiting_for_pin, F.text == "❌ Отмена войти")
async def cancel_pin_input(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Вход отменен.", reply_markup=get_student_main_menu())


@router.message(StudentStates.waiting_for_pin)
async def student_process_pin(
    message: Message, state: FSMContext, session: AsyncSession
):
    """Проверяем PIN, регистрируем связь в WorkoutRegistration"""
    pin_input = message.text.strip()

    if not pin_input.isdigit() or len(pin_input) != 4:
        await message.answer(
            "⚠️ PIN-код должен состоять ровно из 4 цифр. Попробуйте еще раз:"
        )
        return

    # Ищем активную тренировку по PIN
    query = select(Workout).where(
        Workout.status == "active", Workout.pin_code == pin_input
    )
    active_workout = (await session.execute(query)).scalar_one_or_none()

    if not active_workout:
        await message.answer(
            "❌ Тренировка с таким PIN-кодом не найдена. Уточните код у тренера:"
        )
        return

    # Находим внутренний ID пользователя в СУБД
    user_query = select(User).where(User.telegram_id == message.from_user.id)
    user = (await session.execute(user_query)).scalar_one_or_none()

    # Проверяем, не зарегистрирован ли он уже (на случай дублей)
    reg_check = select(WorkoutRegistration).where(
        WorkoutRegistration.workout_id == active_workout.id,
        WorkoutRegistration.student_id == user.id,
    )
    existing_reg = (await session.execute(reg_check)).scalar_one_or_none()

    if not existing_reg:
        # Создаем запись в workout_registrations
        registration = WorkoutRegistration(
            workout_id=active_workout.id,
            student_id=user.id,
            training_points=0.0,
            training_xp=0,
        )
        session.add(registration)
        await session.commit()

    # Сохраняем ID тренировки в контекст FSM ученика
    await state.update_data(current_workout_id=active_workout.id)
    await state.set_state(StudentStates.training_active)

    await message.answer(
        "✅ Вы успешно подключились к тренировке!\n\n"
        "Теперь вы можете самостоятельно отправлять выполненные трюки.\n"
        "Шнурки завязаны, бакли затянуты — погнали! 🚀",
        reply_markup=get_student_training_menu(),
    )


# =====================================================================
# ХЭНДЛЕРЫ ВЫБОРА КАТЕГОРИЙ И ТРЮКОВ
# =====================================================================


@router.message(
    StudentStates.training_active, F.text == "🛼 Отправить выполненный трюк"
)
async def show_categories_dynamic(
    message: Message, state: FSMContext, session: AsyncSession
):
    """Динамически вытаскиваем уникальные категории слалома из таблицы Tricks"""
    query = select(distinct(Trick.category))
    categories = (await session.execute(query)).scalars().all()

    if not categories:
        await message.answer("В базе данных пока не созданы трюки.")
        return

    buttons = []
    for cat in categories:
        if cat:
            buttons.append(
                [
                    types.InlineKeyboardButton(
                        text=str(cat), callback_data=f"st_cat:{cat}"
                    )
                ]
            )

    # Строка переключения дорожки (по умолчанию 80, запоминается между трюками)
    data = await state.get_data()
    cone_line = data.get("cone_line", DEFAULT_CONE_LINE)
    keyboard = [track_row_markup(cone_line, "st_coneset:{line}")] + buttons

    await message.answer(
        f"Выберите категорию трюка.\n{cone_line_text(cone_line)}",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


@router.callback_query(StudentStates.training_active, F.data.startswith("st_coneset:"))
async def student_switch_cone_line(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
):
    """Смена дорожки учеником прямо на экране категорий (запоминается в FSM)."""
    cone_line = callback.data.split(":")[1]
    await state.update_data(cone_line=cone_line)

    categories = (
        (await session.execute(select(distinct(Trick.category)))).scalars().all()
    )
    buttons = [
        [types.InlineKeyboardButton(text=str(cat), callback_data=f"st_cat:{cat}")]
        for cat in categories
        if cat
    ]
    keyboard = [track_row_markup(cone_line, "st_coneset:{line}")] + buttons
    await callback.message.edit_text(
        f"Выберите категорию трюка.\n{cone_line_text(cone_line)}",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
    )
    await callback.answer(f"Дорожка: {cone_line} см")


@router.callback_query(StudentStates.training_active, F.data.startswith("st_cat:"))
async def show_tricks_by_category(callback: CallbackQuery, session: AsyncSession):
    """Показываем трюки из выбранной текстовой категории"""
    category_name = callback.data.split(":", 1)[1]

    query = select(Trick).where(Trick.category == category_name)
    tricks = (await session.execute(query)).scalars().all()

    buttons = []
    for t in tricks:
        buttons.append(
            [
                types.InlineKeyboardButton(
                    text=f"{t.name}", callback_data=f"st_trick:{t.id}"
                )
            ]
        )
    buttons.append(
        [
            types.InlineKeyboardButton(
                text="⬅️ Назад к категориям", callback_data="st_back_to_cats"
            )
        ]
    )

    await callback.message.edit_text(
        f"Категория: {category_name}\nВыберите трюк:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(StudentStates.training_active, F.data == "st_back_to_cats")
async def back_to_categories(callback: CallbackQuery, session: AsyncSession):
    query = select(distinct(Trick.category))
    categories = (await session.execute(query)).scalars().all()
    buttons = [
        [types.InlineKeyboardButton(text=str(cat), callback_data=f"st_cat:{cat}")]
        for cat in categories
        if cat
    ]
    await callback.message.edit_text(
        "Выберите категорию трюка:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


# =====================================================================
# ШАГ ВЫБОРА ДОРОЖКИ И КОНУСОВ
# =====================================================================


@router.callback_query(StudentStates.training_active, F.data.startswith("st_trick:"))
async def student_select_trick_done(callback: CallbackQuery, state: FSMContext):
    """Трюк выбран: сразу запрашиваем конусы, дорожка берется из FSM"""
    trick_id = int(callback.data.split(":")[1])

    # Дорожка берется из FSM (по умолчанию 80, меняется на экране категорий)
    data = await state.get_data()
    distance = data.get("cone_line", DEFAULT_CONE_LINE)

    await state.update_data(selected_trick_id=trick_id, selected_distance=distance)
    await state.set_state(StudentStates.waiting_for_cones)

    await callback.message.edit_text(
        f"{cone_line_text(distance)}\n\n"
        "Отправьте сообщением количество чисто пройденных конусов (например: `20`):",
        parse_mode="Markdown",
    )
    await callback.answer()


@router.message(StudentStates.waiting_for_cones)
async def student_save_final_trick(
    message: Message, state: FSMContext, session: AsyncSession
):
    """Получаем конусы, считаем очки, пишем в TrickLog и обновляем суммарные очки тренировки"""
    cones_input = message.text.strip()

    if not cones_input.isdigit() or int(cones_input) <= 0:
        await message.answer(
            "⚠️ Пожалуйста, введите корректное число конусов (целое положительное число):"
        )
        return

    cones_count = int(cones_input)

    # Извлекаем данные из FSM сессии
    data = await state.get_data()
    workout_id = data.get("current_workout_id")
    trick_id = data.get("selected_trick_id")
    distance = data.get("selected_distance")  # "50", "80" или "120"

    # Получаем объекты сущностей
    trick = await session.get(Trick, trick_id)
    user = (
        await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
    ).scalar_one_or_none()

    # Множитель по умолчанию равен 1.0 (чисто)
    execution_multiplier = 1.0

    # Вызываем твой калькулятор (возвращает кортеж: (баллы, EXP))
    points_earned, xp_earned = await calculate_single_trick(
        trick=trick,
        cone_line=distance,
        quantity=cones_count,
        execution_multiplier=execution_multiplier,
        skater_level=user.level,
        db_session=session,
    )

    # 1. ЗАПИСЬ ДЕТАЛИЗИРОВАННОГО ЛОГА В trick_logs
    new_trick_log = TrickLog(
        workout_id=workout_id,
        student_id=user.id,  # Внутренний ID юзера
        trick_id=trick.id,
        cone_line=distance,  # "50", "80", "120"
        quantity=cones_count,
        execution_multiplier=execution_multiplier,
        calculated_points=points_earned,
        calculated_xp=xp_earned,
        needs_review=True,  # Трюк отправлен самостоятельно, ждем подтверждения тренера
    )
    session.add(new_trick_log)

    # НЕ начисляем очки сразу: результаты засчитаются только после
    # подтверждения тренером (секция проверки needs_review в coach.py)
    await session.commit()

    # Возвращаем FSM в режим активного ожидания следующего трюка
    await state.set_state(StudentStates.training_active)

    await message.answer(
        f"✨ Трюк отправлен на проверку тренеру!\n\n"
        f"🛹 Трюк: {trick.name}\n"
        f"📏 Дорожка: {distance} см\n"
        f"🪚 Пройдено конусов: {cones_count} шт.\n\n"
        f"📊 **Ожидаемое начисление:**\n"
        f"• Баллы: +{points_earned}\n"
        f"• Опыт: +{xp_earned} EXP\n\n"
        f"⏳ Очки и EXP засчитаются после подтверждения тренером.",
        reply_markup=get_student_training_menu(),
        parse_mode="Markdown",
    )


# =====================================================================
# СТАТИСТИКА ЗА СЕССИЮ И ВЫХОД
# =====================================================================


@router.message(StudentStates.training_active, F.text == "🧩 Отправить связку")
async def student_start_combo(
    message: Message, state: FSMContext, session: AsyncSession
):
    """Ученик начинает конструктор связки (общий FSM с тренером)."""
    user = (
        await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
    ).scalar_one_or_none()
    if not user:
        await message.answer("❌ Профиль не найден.")
        return

    # Ищем активную тренировку ученика
    workout = (
        await session.execute(select(Workout).where(Workout.status == "active"))
    ).scalar_one_or_none()
    if not workout:
        await message.answer("❌ Нет активной тренировки.")
        return

    await state.update_data(
        combo_workout_id=workout.id,
        combo_student_id=user.id,
        combo_items=[],
        combo_prev_result=None,
        combo_id=str(uuid.uuid4()),
        combo_needs_review=True,  # ученик отправляет самостоятьно - ждет подтверждения
    )

    cats = (await session.execute(select(Trick.category).distinct())).scalars().all()
    kb = InlineKeyboardBuilder()
    for c in cats:
        if c:
            kb.button(text=str(c), callback_data=f"cmb_cat:{c}")
    kb.button(text="❌ Отмена связки", callback_data="cmb_cancel")
    kb.adjust(2)

    await message.answer(
        "🧩 <b>Конструктор связки</b>\n\nШаг 1: выберите категорию первого трюка:",
        reply_markup=kb.as_markup(),
        parse_mode="HTML",
    )
    # Переводим ученика в общий FSM конструктора связки
    await state.set_state(ComboBuilderStates.choosing_first_trick)


@router.message(StudentStates.training_active, F.text == "📊 Мои очки за сегодня")
async def student_today_stats(
    message: Message, state: FSMContext, session: AsyncSession
):
    """Перечень отправленных тренеру трюков и связок со статусом
    (на проверке / подтвержден) + ожидаемые итоги тренировки."""
    user = (
        await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
    ).scalar_one_or_none()

    # Ищем активную тренировку
    workout_query = select(Workout).where(Workout.status == "active")
    active_workout = (await session.execute(workout_query)).scalar_one_or_none()

    if not active_workout:
        await message.answer("Тренировка уже завершена.")
        return

    data = await state.get_data()
    cone_line = data.get("cone_line", DEFAULT_CONE_LINE)

    logs = (
        (
            await session.execute(
                select(TrickLog)
                .where(
                    TrickLog.workout_id == active_workout.id,
                    TrickLog.student_id == user.id,
                )
                .order_by(TrickLog.id)
            )
        )
        .scalars()
        .all()
    )

    if not logs:
        await message.answer(
            "📭 Вы пока не отправляли трюки на этой тренировке.",
            reply_markup=get_student_training_menu(),
        )
        return

    # Группируем: связки одним элементом, одиночные трюки — по одному
    from handlers.coach.review import _group_logs_by_combo

    tricks = {
        t.id: t
        for t in (
            await session.scalars(
                select(Trick).where(Trick.id.in_([l.trick_id for l in logs]))
            )
        ).all()
    }

    # Стоимость связки = Result последнего шага; EXP — по формуле калькулятора
    from handlers.coach.stats import _combo_exp_map

    pairs = [(log, tricks.get(log.trick_id)) for log in logs if log.combo_id]
    combo_exp = await _combo_exp_map(pairs, user, session)

    items = _group_logs_by_combo(list(logs))
    lines = ["📊 Мои очки за сегодня:\n"]
    for item in items:
        if item["type"] == "combo":
            steps = item["logs"]
            names = " → ".join(
                tricks.get(s.trick_id).name if tricks.get(s.trick_id) else "?"
                for s in steps
            )
            confirmed = all(not s.needs_review for s in steps)
            status = "✅ подтверждена" if confirmed else "⏳ на проверке"
            # Стоимость связки = Result последнего шага, EXP = сумма шагов
            last = max(steps, key=lambda s: s.combo_step or 0)
            combo_points = last.calculated_points + (last.combo_bonus or 0.0)
            combo_xp = combo_exp.get(last.combo_id, 0)
            lines.append(
                f"🧩 Связка: {names} — "
                f"{round(combo_points, 2)} очков, +{combo_xp} EXP — {status}"
            )
        else:
            log = item["log"]
            trick = tricks.get(log.trick_id)
            name = trick.name if trick else "Трюк"
            status = "✅ подтвержден" if not log.needs_review else "⏳ на проверке"
            lines.append(
                f"🎯 {name}: {log.quantity} конусов, (дорожка {log.cone_line}) — "
                f"{log.calculated_points} очков, +{log.calculated_xp} EXP — {status}"
            )

    # Итог по подтвержденным: как в журнале тренировок (калькулятор итогов)
    from calculator import calculate_current_workout_totals

    student = await session.get(User, user.id)
    sport_score, total_xp = await calculate_current_workout_totals(
        list(logs), student, session
    )
    pending = [l for l in logs if l.needs_review]
    if pending:
        lines.append(
            f"\n⏳ На проверке: {len(pending)} — очки засчитаются после подтверждения тренером.\n"
            f"📊 Текущий результат (предварительно): {sport_score} очков, +{total_xp} EXP"
        )
    else:
        lines.append(
            f"\n✅ Все отправленные трюки подтверждены тренером.\n"
            f"📊 Итог тренировки: {sport_score} очков, +{total_xp} EXP"
        )

    await message.answer(
        "\n".join(lines),
        reply_markup=get_student_training_menu(),
    )


@router.message(StudentStates.training_active, F.text == "🚪 Покинуть тренировку")
async def student_leave_workout(message: Message, state: FSMContext):
    """Сбрасывает состояние и возвращает ученика в главное меню"""
    await state.clear()
    await message.answer(
        "🚪 Вы поехали отдыхать. Тренировка покинута!",
        reply_markup=get_student_main_menu(),
    )


# =====================================================================
# БАЗА ТРЮКОВ (дублирует справочник из coach/handbook.py через те же callback'и hb_*)
# =====================================================================


@router.message(F.text == "📚 База трюков")
async def student_show_handbook(message: Message, session: AsyncSession):
    """База трюков для ученика — тот же справочник, что у тренера.
    Дальнейшая навигация (hb_cat / hb_trick / hb_back) обрабатывается
    хендлерами handlers/coach/handbook.py — они зарегистрированы глобально."""
    categories = await get_trick_categories(session)
    if not categories:
        await message.answer("🤖 Справочник трюков пока пуст. Наполните базу данных.")
        return
    await message.answer(
        "📚 Справочник трюков. Выберите категорию:",
        reply_markup=_handbook_categories_kb(categories),
    )


# =====================================================================
# МОЙ ПРОФИЛЬ (тот же экран, что тренерская статистика по ученику)
# =====================================================================


@router.message(F.text == "👤 Мой персонаж")
async def student_my_profile(message: Message, session: AsyncSession):
    """Профиль ученика: BP, прогресс-бар и разбивка по тренировкам"""
    user = (
        await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
    ).scalar_one_or_none()

    if not user or not user.is_approved:
        await message.answer("❌ Ваша учетная запись еще не одобрена тренером.")
        return

    text = await build_student_profile_text(user, session)
    await message.answer(
        text,
        reply_markup=await _workout_details_kb(user, session),
        parse_mode="HTML",
    )
