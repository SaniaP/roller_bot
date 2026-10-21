from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from models import User
from config import CHIEF_COACH_ID
from states import SkaterRegistrationStates
# Импортируем клавиатуры (функцию get_role_selection_kb добавим на следующем шаге)
from keyboards.common_kb import get_role_selection_kb 
from keyboards import get_coach_main_menu, get_skater_main_menu

router = Router()

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext, session: AsyncSession):
    """Хэндлер команды /start. Проверяет авторизацию или предлагает выбрать роль."""
    await state.clear()
    tg_id = message.from_user.id

    # Проверяем, есть ли пользователь в базе данных
    query = select(User).where(User.telegram_id == tg_id)
    result = await session.execute(query)
    user = result.scalar_one_or_none()

    # 1. Если пользователя нет в базе — отправляем выбирать роль
    if user is None:
        await message.answer(
            "👋 Привет! Добро пожаловать в RPG-систему роллер-школы!\n\n"
            "Я не нашел тебя в списках. Давай создадим твой профиль персонажа.\n"
            "Для начала выбери свою <b>роль в системе</b>:",
            reply_markup=get_role_selection_kb()
        )
        return

    # 2. Если вошел Тренер
    if user.role == "coach":
        await message.answer(
            f"👑 Рады видеть вас, Шеф! Добро пожаловать, Тренер <b>{user.full_name}</b>!\n"
            "Все судейские связки и лимиты опыта активны. Выберите действие:",
            reply_markup=get_coach_main_menu()
        )

    # 3. Если вошел Спортсмен (Ученик)
    else:
        await message.answer(
            f"🛹 Привет, <b>{user.full_name}</b>!\n"
            f"Твой текущий уровень: <b>{user.level}</b> лвл. Твои колеса готовы к тренировке!\n"
            "Выбирай действие на панели управления:",
            reply_markup=get_skater_main_menu()
        )


@router.callback_query(F.data == "select_role_coach")
async def process_select_coach(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    """Ловим нажатие кнопки 'Я Тренер' и проверяем права"""
    tg_id = callback.from_user.id

    # Валидация по CHIEF_COACH_ID из config.py
    if tg_id != CHIEF_COACH_ID:
        await callback.answer(
            "⛔ Доступ запрещен! Вы не зарегистрированы в системе как Главный Тренер.", 
            show_alert=True
        )
        return

    # Если ID совпал, сразу регистрируем шефа (ФИО берем из аккаунта TG)
    full_name = callback.from_user.full_name or "Главный Тренер"
    
    new_coach = User(
        telegram_id=tg_id,
        full_name=full_name,
        role="coach",
        is_approved=True
    )
    session.add(new_coach)
    # commit выполнится автоматически благодаря нашей Middleware!
    
    await callback.message.edit_text(
        f"👑 Авторизация успешна!\nСоздан профиль Тренера: <b>{full_name}</b>.\n"
        "Вам доступно управление тренировочным процессом.",
    )
    # Выводим главное меню тренера
    await callback.message.answer("Выберите действие:", reply_markup=get_coach_main_menu())
    await callback.answer()


@router.callback_query(F.data == "select_role_student")
async def process_select_student(callback: CallbackQuery, state: FSMContext):
    """Ловим нажатие кнопки 'Я Ученик' и запускаем цепочку FSM анкетирования"""
    await callback.message.edit_text(
        "📝 Отлично! Начнем регистрацию ученика.\n\n"
        "Пожалуйста, <b>введите свои Фамилию и Имя</b> через пробел (например: Иванов Иван):"
    )
    await state.set_state(SkaterRegistrationStates.waiting_for_name)
    await callback.answer()


@router.message(SkaterRegistrationStates.waiting_for_name)
async def process_reg_name(message: Message, state: FSMContext):
    """Ловим ввод Имени и Фамилии спортсмена"""
    full_name = message.text.strip()
    
    if len(full_name) < 3 or " " not in full_name:
        await message.answer("⚠️ Пожалуйста, введите корректно Фамилию и Имя через пробел (например: Петров Петр):")
        return

    await state.update_data(full_name=full_name)
    
    await message.answer(
        f"Отлично, <b>{full_name}</b>!\n"
        "Теперь введите вашу <b>дату рождения</b> в формате <code>ДД.ММ.ГГГГ</code> (например: 12.04.2011):"
    )
    await state.set_state(SkaterRegistrationStates.waiting_for_birth_date)


@router.message(SkaterRegistrationStates.waiting_for_birth_date)
async def process_reg_birth_date(message: Message, state: FSMContext, session: AsyncSession):
    """Ловим дату рождения и окончательно пишем роллера в базу"""
    from datetime import datetime
    date_text = message.text.strip()
    
    try:
        birth_date = datetime.strptime(date_text, "%d.%m.%Y").date()
    except ValueError:
        await message.answer("⚠️ Неверный формат даты. Напишите её как <code>ДД.ММ.ГГГГ</code> (например: 25.10.2014):")
        return

    user_data = await state.get_data()
    full_name = user_data.get("full_name")
    tg_id = message.from_user.id

    new_user = User(
        telegram_id=tg_id,
        full_name=full_name,
        birth_date=birth_date,
        role="student",
    )

    session.add(new_user)
    await state.clear()

    await message.answer(
        f"🎉 Ура! Регистрация завершена. Создан персонаж: <b>{full_name}</b>.\n"
        f"Твой стартовый уровень: <b>1</b> [XP: 0/200].\n\n"
        "Теперь ты можешь записываться на тренировки и вносить свои трюки!",
        reply_markup=get_skater_main_menu()
    )