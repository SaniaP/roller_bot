import os
from aiogram import Router, F
from aiogram.types import Message, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

# Твои модели и стейты
from models import User, Trick, TrickLog, Workout, WorkoutRegistration
from states.skater_states import SkaterWorkoutStates
from keyboards.skater_kb import get_skater_main_menu

student_router = Router()

# ==========================================
# 1. ИНФОРМАЦИОННЫЕ КНОПКИ (ПРОФИЛЬ И ТРЮКИ)
# ==========================================

@student_router.message(F.text == "📊 Мой RPG-Профиль")
async def show_rpg_profile(message: Message, session: AsyncSession):
    """Выводит игровой и спортивный профиль ученика на основе данных из БД"""
    result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = result.scalar_one_or_none()
    
    if not user:
        await message.answer("⚠️ Ты не найден в базе данных школы. Обратись к тренеру для регистрации!")
        return

    if not user.is_approved:
        await message.answer("🔒 Твой аккаунт еще не подтвержден тренером. Дождись одобрения!")
        return

    # Считаем сумму спортивных очков из логов
    points_result = await session.execute(
        select(func.sum(TrickLog.calculated_points)).where(TrickLog.student_id == user.id)
    )
    total_sports_points = points_result.scalar() or 0.0

    profile_text = (
        f"🏅 **ПРОФИЛЬ РАЙДЕРА: {user.full_name}** 🏅\n"
        f"« {user.title} »\n\n"
        f"🎮 **ИГРОВОЙ РЕЖИМ (RPG):**\n"
        f"⭐ Уровень: `{user.level} LVL`\n"
        f"🔥 Опыт: `{user.xp} XP`\n\n"
        f"🏆 **СПОРТИВНЫЙ РЕЖИМ:**\n"
        f"🎯 Спортивные очки: `{total_sports_points:.1f} pts`\n"
        f"📱 Телефон: `{user.phone or 'Не указан'}`\n\n"
        f"🛹 *Катай, собирай комбо на дорожках и качай свой уровень!*"
    )

    if user.photo_file_id:
        await message.answer_photo(photo=user.photo_file_id, caption=profile_text, parse_mode="Markdown")
    else:
        await message.answer(profile_text, parse_mode="Markdown")


@student_router.message(F.text == "🛹 Мои трюки")
async def show_my_tricks(message: Message, session: AsyncSession):
    """Выводит список уникальных трюков, которые спортсмен выполнял на тренировках"""
    user_result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = user_result.scalar_one_or_none()
    
    if not user:
        await message.answer("⚠️ Ошибка: пользователь не найден.")
        return

    query = (
        select(Trick.name, Trick.category, func.max(TrickLog.cone_line))
        .join(TrickLog, TrickLog.trick_id == Trick.id)
        .where(TrickLog.student_id == user.id)
        .group_by(Trick.name, Trick.category)
    )
    
    result = await session.execute(query)
    tricks_data = result.all()

    if not tricks_data:
        await message.answer("🛹 В твоем арсенале пока нет зафиксированных трюков. Время сделать первый подход на тренировке!")
        return

    tricks_text = "📜 **ТВОЙ АРСЕНАЛ ТРЮКОВ:**\n\n"
    for name, category, cone_line in tricks_data:
        cat_emoji = "🔹"
        if category == "jump": cat_emoji = "🦘"
        elif category == "spin": cat_emoji = "🌀"
        elif category == "wheelie": cat_emoji = "🛞"
        
        tricks_text += f"{cat_emoji} *{name}* (откатано на дорожке: {cone_line})\n"

    await message.answer(tricks_text, parse_mode="Markdown")


# ==========================================
# 2. ПОШАГОВЫЙ СЦЕНАРИЙ FSM (ВНЕСЕНИЕ ТРЮКА)
# ==========================================

@student_router.message(F.text == "🎯 Внести выполненный трюк")
async def start_trick_log(message: Message, state: FSMContext, session: AsyncSession):
    """Шаг 1: Проверяем активную тренировку и запрашиваем название трюка"""
    active_workout = await session.scalar(
        select(Workout).where(Workout.status == "active") 
    )
    
    if not active_workout:
        await message.answer("❌ Сейчас нет активных тренировок. Вносить трюки можно только во время занятия!")
        return

    user = await session.scalar(select(User).where(User.telegram_id == message.from_user.id))
    if not user:
        await message.answer("⚠️ Ты не зарегистрирован в системе!")
        return

    reg = await session.scalar(
        select(WorkoutRegistration).where(
            WorkoutRegistration.workout_id == active_workout.id,
            WorkoutRegistration.student_id == user.id
        )
    )
    if not reg:
        await message.answer("⚠️ Ты не зарегистрирован на текущую тренировку у тренера!")
        return

    await state.update_data(workout_id=active_workout.id, student_id=user.id)

    result = await session.execute(select(Trick.name))
    tricks = result.scalars().all()
    
    if not tricks:
        await message.answer("🤖 В справочнике трюков пока пусто. Напиши тренеру!")
        return

    builder = ReplyKeyboardBuilder()
    for trick_name in tricks:
        builder.button(text=trick_name)
    builder.adjust(2)
    builder.row(KeyboardButton(text="❌ Отмена"))

    await message.answer("🛹 Какой трюк ты сейчас выполнил? Выбери из списка:", reply_markup=builder.as_markup(resize_keyboard=True))
    await state.set_state(SkaterWorkoutStates.waiting_for_trick)


@student_router.message(SkaterWorkoutStates.waiting_for_trick)
async def process_trick_choice(message: Message, state: FSMContext, session: AsyncSession):
    """Шаг 2: Фиксируем трюк и запрашиваем количество конусов"""
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Внесение трюка отменено.", reply_markup=get_skater_main_menu())
        return

    trick = await session.scalar(select(Trick).where(Trick.name == message.text))
    if not trick:
        await message.answer("⚠️ Пожалуйста, выбери трюк из списка на кнопках!")
        return

    await state.update_data(trick_id=trick.id, base_xp=trick.base_xp, base_tech_points=trick.base_tech_points)

    builder = ReplyKeyboardBuilder()
    for q in [2,3,4,5,6,8,10,15,20]:  #Подсказки для быстрого ввода количества конусов
        builder.button(text=str(q))
    builder.row(KeyboardButton(text="❌ Отмена"))
    builder.adjust(5)

    await message.answer(
        f"Укажи количество конусов или повторений для трюка *{trick.name}* (базово: {trick.base_quantity}):", 
        reply_markup=builder.as_markup(resize_keyboard=True),
        parse_mode="Markdown"
    )
    await state.set_state(SkaterWorkoutStates.waiting_for_quantity)


@student_router.message(SkaterWorkoutStates.waiting_for_quantity)
async def process_quantity(message: Message, state: FSMContext):
    """Шаг 3: Фиксируем количество и запрашиваем шаг дорожки"""
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Внесение трюка отменено.", reply_markup=get_skater_main_menu())
        return

    if not message.text.isdigit():
        await message.answer("⚠️ Введи целое число конусов!")
        return

    await state.update_data(quantity=int(message.text))

    builder = ReplyKeyboardBuilder()
    builder.button(text="50 см")
    builder.button(text="80 см")
    builder.button(text="120 см")
    builder.row(KeyboardButton(text="❌ Отмена"))
    builder.adjust(3)

    await message.answer("📐 Выбери шаг дорожки (расстояние между конусами):", reply_markup=builder.as_markup(resize_keyboard=True))
    await state.set_state(SkaterWorkoutStates.waiting_for_track_coeff)


@student_router.message(SkaterWorkoutStates.waiting_for_track_coeff)
async def process_track_coeff(message: Message, state: FSMContext):
    """Шаг 4: Фиксируем дорожку и спрашиваем про комбо"""
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Внесение трюка отменено.", reply_markup=get_skater_main_menu())
        return

    if message.text not in ["50 см", "80 см", "120 см"]:
        await message.answer("⚠️ Выбери один из вариантов на кнопках!")
        return

    cone_line = message.text.replace(" см", "")
    await state.update_data(cone_line=cone_line)

    builder = ReplyKeyboardBuilder()
    builder.button(text="🧩 Да, это элемент связки")
    builder.button(text="👤 Нет, это одиночный трюк")
    builder.row(KeyboardButton(text="❌ Отмена"))
    builder.adjust(2)

    await message.answer("🔗 Это элемент комбо-связки?", reply_markup=builder.as_markup(resize_keyboard=True))
    await state.set_state(SkaterWorkoutStates.waiting_for_combo)


@student_router.message(SkaterWorkoutStates.waiting_for_combo)
async def process_combo_and_save(message: Message, state: FSMContext, session: AsyncSession):
    """Шаг 5: Считаем очки, сохраняем в базу и завершаем опрос"""
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Внесение трюка отменено.", reply_markup=get_skater_main_menu())
        return

    if message.text not in ["🧩 Да, это элемент связки", "👤 Нет, это одиночный трюк"]:
        await message.answer("⚠️ Пожалуйста, выбери один из вариантов на кнопках!")
        return

    data = await state.get_data()
    await state.clear()

    trick = await session.scalar(select(Trick).where(Trick.id == data['trick_id']))
    if not trick:
        await message.answer("⚠️ Ошибка: трюк не найден в справочнике. Попробуй начать заново.", reply_markup=get_skater_main_menu())
        return

    coeff = 1.0
    if data['cone_line'] == "50": coeff = trick.coeff_50
    elif data['cone_line'] == "80": coeff = trick.coeff_80
    elif data['cone_line'] == "120": coeff = trick.coeff_120

    calculated_points = data['base_tech_points'] * (data['quantity'] / trick.base_quantity) * coeff
    calculated_xp = int(data['base_xp'] * (data['quantity'] / trick.base_quantity))

    is_combo = "Да" in message.text
    if is_combo:
        calculated_points *= (1.0 + trick.extra_bonus_pct)
        calculated_xp = int(calculated_xp * (1.0 + trick.extra_bonus_pct))

    # Все строки ниже сдвинуты вправо (на 4 пробела), так как они внутри функции
    new_log = TrickLog(
        workout_id=data['workout_id'],
        student_id=data['student_id'],
        trick_id=data['trick_id'],
        cone_line=data['cone_line'],
        quantity=data['quantity'],
        calculated_points=round(calculated_points, 2),
        calculated_xp=calculated_xp,
        combo_id="combo_active" if is_combo else None
    )
    
    session.add(new_log)
    await session.commit()

    await message.answer(
        f"✅ **Трюк успешно зафиксирован в лог тренировки!**\n\n"
        f"📊 Расчет за подход:\n"
        f"🎯 Спортивные очки: `+{calculated_points:.2f} pts`\n"
        f"🔥 Игровой опыт: `+{calculated_xp} XP` "
        f"{'(+бонус за комбо!)' if is_combo else ''}\n\n"
        f"Отличная работа, продолжай в том же духе! 🛹",
        reply_markup=get_skater_main_menu(),
        parse_mode="Markdown"
    )