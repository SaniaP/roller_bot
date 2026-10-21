from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime

from models import User  # Наша декларативная модель пользователя из Главы 2 [2.2]
from keyboards import get_coach_main_menu, get_skater_main_menu  # Наши кнопки
from states import SkaterRegistrationStates  # Сценарий регистрации ученика

router = Router()

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext, session: AsyncSession):
    """Хэндлер команды /start. Авторизует по telegram_id и выдает нужное меню"""
    # Сбрасываем любые прошлые шаги, если пользователь запутался
    await state.clear()
    
    tg_id = message.from_user.id

    # Делаем асинхронный запрос в базу через сессию, принесенную мидлварью [2.3]
    query = select(User).where(User.telegram_id == tg_id)
    result = await session.execute(query)
    user = result.scalar_one_or_none()

    # 1. Если пользователя нет в базе — запускаем квест регистрации
    if user is None:
        await message.answer(
            "👋 Привет! Добро пожаловать в RPG-систему роллер-школы!\n\n"
            "Я не нашел тебя в списках школы. Давай создадим твой профиль персонажа.\n"
            "Пожалуйста, **введи свои Фамилию и Имя** (например: Иванов Иван):"
        )
        # Переводим пользователя в состояние ожидания имени
        await state.set_state(SkaterRegistrationStates.waiting_for_name)
        return

    # 2. Если в базу вошел Тренер
    if user.role == "coach":
        await message.answer(
            f"👑 Рады видеть вас, Шеф! Добро пожаловать, Тренер **{user.full_name}**!\n"
            "Все судейские связки и лимиты опыта активны. Выберите действие:",
            reply_markup=get_coach_main_menu()
        )

    # 3. Если вошел Спортсмен (Ученик)
    else:
        await message.answer(
            f"🛹 Привет, **{user.full_name}**!\n"
            f"Твой текущий уровень: **{user.level}** лвл. Твои колеса готовы к тренировке!\n"
            "Выбирай действие на панели управления:",
            reply_markup=get_skater_main_menu()
        )


@router.message(SkaterRegistrationStates.waiting_for_name)
async def process_reg_name(message: Message, state: FSMContext):
    """Ловим ввод Имени и Фамилии спортсмена"""
    full_name = message.text.strip()
    
    # Небольшая валидация, чтобы не вводили всякий мусор вместо имени
    if len(full_name) < 3 or " " not in full_name:
        await message.answer("⚠️ Пожалуйста, введите корректно Фамилию и Имя через пробел (например: Петров Петр):")
        return

    # Сохраняем имя во временный буфер FSM памяти
    await state.update_data(full_name=full_name)
    
    await message.answer(
        f"Отлично, **{full_name}**!\n"
        "Теперь введите вашу **дату рождения** в формате `ДД.ММ.ГГГГ` (например: 12.04.2011):"
    )
    # Переводим на следующий шаг
    await state.set_state(SkaterRegistrationStates.waiting_for_birth_date)


@router.message(SkaterRegistrationStates.waiting_for_birth_date)
async def process_reg_birth_date(message: Message, state: FSMContext, session: AsyncSession):
    """Ловим дату рождения, валидируем её и окончательно пишем роллера в базу"""
    date_text = message.text.strip()
    
    try:
        # Проверяем, что дата введена в правильном формате
        birth_date = datetime.strptime(date_text, "%d.%m.%Y").date()
    except ValueError:
        await message.answer("⚠️ Неверный формат даты. Напишите её как `ДД.ММ.ГГГГ` (например: 25.10.2014):")
        return

    # Извлекаем имя, которое мы запомнили на предыдущем шаге
    user_data = await state.get_data()
    full_name = user_data.get("full_name")
    tg_id = message.from_user.id

    # Создаем новую запись игрока 1-го уровня [2.2]
    new_user = User(
        telegram_id=tg_id,
        full_name=full_name,
        birth_date=birth_date,
        role="student",
    )

    # Сохраняем в roller_school.db через сессию [2.3]
    session.add(new_user)
    
    # Закрываем блокнот FSM, очищая память шагов
    await state.clear()

    await message.answer(
        f"🎉 Ура! Регистрация завершена. Создан персонаж: **{full_name}**.\n"
        f"Твой стартовый уровень: **1** [XP: 0/200].\n\n"
        "Теперь ты можешь вносить свои выполненные трюки на тренировках, а тренер будет их утверждать!",
        reply_markup=get_skater_main_menu()
    )