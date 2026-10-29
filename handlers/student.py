import logging
from typing import Optional
from aiogram import Router, F, types
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, distinct

# Твои реальные модели из проекта
from models import User, Workout, WorkoutRegistration, Trick, TrickLog
#как мы считаем если их не используем?)
from calculator import calculate_single_trick, calculate_current_workout_totals, check_level_up
from keyboards.skater_kb import get_student_main_menu,get_student_training_menu,get_distance_inline_kb
from states import StudentStates

logger = logging.getLogger(__name__)
router = Router()


# =====================================================================
# ХЭНДЛЕРЫ ПОДКЛЮЧЕНИЯ ПО PIN-КОДУ
# =====================================================================

@router.message(F.text == "🔑 Подключиться к тренировке")
async def student_ask_for_pin(message: Message, state: FSMContext, session: AsyncSession):
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
            keyboard=[[KeyboardButton(text="❌ Отмена войти")]], 
            resize_keyboard=True
        )
    )

@router.message(StudentStates.waiting_for_pin, F.text == "❌ Отмена войти")
async def cancel_pin_input(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Вход отменен.", reply_markup=get_student_main_menu())

@router.message(StudentStates.waiting_for_pin)
async def student_process_pin(message: Message, state: FSMContext, session: AsyncSession):
    """Проверяем PIN, регистрируем связь в WorkoutRegistration"""
    pin_input = message.text.strip()

    if not pin_input.isdigit() or len(pin_input) != 4:
        await message.answer("⚠️ PIN-код должен состоять ровно из 4 цифр. Попробуйте еще раз:")
        return

    # Ищем активную тренировку по PIN
    query = select(Workout).where(Workout.status == "active", Workout.pin_code == pin_input)
    active_workout = (await session.execute(query)).scalar_one_or_none()

    if not active_workout:
        await message.answer("❌ Тренировка с таким PIN-кодом не найдена. Уточните код у тренера:")
        return

    # Находим внутренний ID пользователя в СУБД
    user_query = select(User).where(User.telegram_id == message.from_user.id)
    user = (await session.execute(user_query)).scalar_one_or_none()

    # Проверяем, не зарегистрирован ли он уже (на случай дублей)
    reg_check = select(WorkoutRegistration).where(
        WorkoutRegistration.workout_id == active_workout.id,
        WorkoutRegistration.student_id == user.id
    )
    existing_reg = (await session.execute(reg_check)).scalar_one_or_none()

    if not existing_reg:
        # Создаем запись в workout_registrations
        registration = WorkoutRegistration(
            workout_id=active_workout.id,
            student_id=user.id,
            training_points=0.0,
            training_xp=0
        )
        session.add(registration)
        await session.commit()

    # Сохраняем ID тренировки в контекст FSM ученика
    await state.update_data(current_workout_id=active_workout.id)
    await state.set_state(StudentStates.training_active)

    await message.answer(
        "✅ Вы успешно подключились к тренировке!\n\n"
        "Теперь вы можете самостоятельно отправлять выполненные трюки.\n"
        "По умолчанию они запишутся с чистым выполнением (1.0).",
        reply_markup=get_student_training_menu()
    )


# =====================================================================
# ХЭНДЛЕРЫ ВЫБОРА КАТЕГОРИЙ И ТРЮКОВ
# =====================================================================

@router.message(StudentStates.training_active, F.text == "🛹 Отправить выполненный трюк")
async def show_categories_dynamic(message: Message, session: AsyncSession):
    """Динамически вытаскиваем уникальные категории слалома из таблицы Tricks"""
    query = select(distinct(Trick.category))
    categories = (await session.execute(query)).scalars().all()

    if not categories:
        await message.answer("В базе данных пока не созданы трюки.")
        return

    buttons = []
    for cat in categories:
        if cat:
            buttons.append([types.InlineKeyboardButton(text=str(cat), callback_data=f"st_cat:{cat}")])
            
    await message.answer("Выберите категорию трюка:", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=buttons))

@router.callback_query(StudentStates.training_active, F.data.startswith("st_cat:"))
async def show_tricks_by_category(callback: CallbackQuery, session: AsyncSession):
    """Показываем трюки из выбранной текстовой категории"""
    category_name = callback.data.split(":", 1)[1]
    
    query = select(Trick).where(Trick.category == category_name)
    tricks = (await session.execute(query)).scalars().all()

    buttons = []
    for t in tricks:
        buttons.append([types.InlineKeyboardButton(text=f"{t.name}", callback_data=f"st_trick:{t.id}")])
    buttons.append([types.InlineKeyboardButton(text="⬅️ Назад к категориям", callback_data="st_back_to_cats")])

    await callback.message.edit_text(f"Категория: {category_name}\nВыберите трюк:", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()

@router.callback_query(StudentStates.training_active, F.data == "st_back_to_cats")
async def back_to_categories(callback: CallbackQuery, session: AsyncSession):
    query = select(distinct(Trick.category))
    categories = (await session.execute(query)).scalars().all()
    buttons = [[types.InlineKeyboardButton(text=str(cat), callback_data=f"st_cat:{cat}")] for cat in categories if cat]
    await callback.message.edit_text("Выберите категорию трюка:", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


# =====================================================================
# ШАГ ВЫБОРА ДОРОЖКИ И КОНУСОВ
# =====================================================================

@router.callback_query(StudentStates.training_active, F.data.startswith("st_trick:"))
async def student_select_trick_done(callback: CallbackQuery, state: FSMContext):
    """Трюк выбран. Переходим к выбору дорожки (cone_line)"""
    trick_id = int(callback.data.split(":")[1])
    
    await state.update_data(selected_trick_id=trick_id)
    await state.set_state(StudentStates.waiting_for_distance)
    
    await callback.message.edit_text(
        "Выберите размер дорожки (расстояние между конусами):", 
        reply_markup=get_distance_inline_kb()
    )
    await callback.answer()

@router.callback_query(StudentStates.waiting_for_distance, F.data.startswith("dist:"))
async def student_select_distance_done(callback: CallbackQuery, state: FSMContext):
    """Дорожка выбрана. Запрашиваем количество конусов (quantity)"""
    distance = callback.data.split(":")[1] # Строка "50", "80" или "120"
    
    await state.update_data(selected_distance=distance)
    await state.set_state(StudentStates.waiting_for_cones)
    
    await callback.message.edit_text(
        f"Выбрана дорожка: `{distance} см`.\n\n"
        "Отправьте сообщением количество чисто пройденных конусов (например: `20`):",
        parse_mode="Markdown"
    )
    await callback.answer()


@router.message(StudentStates.waiting_for_cones)
async def student_save_final_trick(message: Message, state: FSMContext, session: AsyncSession):
    """Получаем конусы, считаем очки, пишем в TrickLog и обновляем суммарные очки тренировки"""
    cones_input = message.text.strip()
    
    if not cones_input.isdigit() or int(cones_input) <= 0:
        await message.answer("⚠️ Пожалуйста, введите корректное число конусов (целое положительное число):")
        return
        
    cones_count = int(cones_input)
    
    # Извлекаем данные из FSM сессии
    data = await state.get_data()
    workout_id = data.get("current_workout_id")
    trick_id = data.get("selected_trick_id")
    distance = data.get("selected_distance") # "50", "80" или "120"
    
    # Получаем объекты сущностей
    trick = await session.get(Trick, trick_id)
    user = (await session.execute(
        select(User).where(User.telegram_id == message.from_user.id)
    )).scalar_one_or_none()

    # Множитель по умолчанию равен 1.0 (чисто)
    execution_multiplier = 1.0

    # Вызываем твой калькулятор (возвращает кортеж: (баллы, XP))
    points_earned, xp_earned = await calculate_single_trick(
        trick=trick,
        cone_line=distance,
        quantity=cones_count,
        execution_multiplier=execution_multiplier,
        skater_level=user.level,
        db_session=session
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
        needs_review=True  # Трюк отправлен самостоятельно, ждем подтверждения тренера
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
        f"🪚 Пройдено конусов: {cones_count} шт.\n"
        f"🎯 Исполнение: 1.0 (Чисто)\n\n"
        f"⏳ Очки и XP засчитаются после подтверждения тренером.",
        reply_markup=get_student_training_menu(),
        parse_mode="Markdown"
    )


# =====================================================================
# СТАТИСТИКА ЗА СЕССИЮ И ВЫХОД
# =====================================================================

@router.message(StudentStates.training_active, F.text == "📊 Мои очки за сегодня")
async def student_today_stats(message: Message, session: AsyncSession):
    """Выводит текущую сумму набранных очков напрямую из workout_registrations"""
    user = (await session.execute(
        select(User).where(User.telegram_id == message.from_user.id)
    )).scalar_one_or_none()

    # Ищем активную тренировку
    workout_query = select(Workout).where(Workout.status == "active")
    active_workout = (await session.execute(workout_query)).scalar_one_or_none()
    
    if not active_workout:
        await message.answer("Тренировка уже завершена.")
        return

    reg_query = select(WorkoutRegistration).where(
        WorkoutRegistration.workout_id == active_workout.id,
        WorkoutRegistration.student_id == user.id
    )
    registration = (await session.execute(reg_query)).scalar_one_or_none()
    
    if not registration:
        await message.answer("У вас пока нет записей на этой тренировке.")
        return

    await message.answer(
        f"📊 Твои результаты на текущем занятии:\n\n"
        f"✨ Набранный опыт: +{registration.training_xp} XP\n"
        f"🏆 Спортивные баллы: +{registration.training_points} PTS",
        parse_mode="Markdown"
    )


@router.message(StudentStates.training_active, F.text == "🚪 Покинуть тренировку")
async def student_leave_workout(message: Message, state: FSMContext):
    """Сбрасывает состояние и возвращает ученика в главное меню"""
    await state.clear()
    await message.answer(
        "🚪 Вы поехали отдыхать. Тренировка покинута!", 
        reply_markup=get_student_main_menu()
    )