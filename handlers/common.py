import logging

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from models import User, Workout
from config import CHIEF_COACH_ID
from states import SkaterRegistrationStates

# Импортируем клавиатуры 
from keyboards import get_coach_main_menu, get_coach_main_menu_sync, get_skater_main_menu, get_role_selection_kb
from keyboards.coach_kb import get_approve_user_kb

logger = logging.getLogger(__name__)

router = Router()

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext, session: AsyncSession):
    """Хэндлер команды /start. Проверяет авторизацию или регистрирует спортсмена."""
    await state.clear()  # Сброс FSM для текущего юзера
    tg_id = message.from_user.id
    
    # 1. Проверяем, есть ли пользователь в базе данных
    query = select(User).where(User.telegram_id == tg_id)
    result = await session.execute(query)
    user = result.scalar_one_or_none()

    # ==========================================
    # ЛОГИКА ДЛЯ АВТОРЕГИСТРАЦИИ НОВОГО СПОРТСМЕНА
    # ==========================================
    if user is None:
        username = message.from_user.username
        full_name = message.from_user.full_name

        # Создаем профиль спортсмена в БД со статусом НЕОДОБРЕН
        new_user = User(
            telegram_id=tg_id,
            username=username,
            full_name=full_name,
            role="student",
            is_approved=False,
            level=1  # Стартовый уровень в RPG-системе
        )
        session.add(new_user)
        await session.commit()
        
        # Отправляем запрос тренеру на одобрение заявки
        try:
            await message.bot.send_message(
                chat_id=CHIEF_COACH_ID,
                text=f"🔔 **Новая заявка на регистрацию!**\n\n"
                     f"Спортсмен: {full_name}\n"
                     f"Username: @{username or 'нет'}\n"
                     f"ID: `{tg_id}`\n\n"
                     f"Разрешить ему доступ к тренировкам?",
                reply_markup=get_approve_user_kb(tg_id),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление тренеру: {e}")

        # Отвечаем новому спортсмену
        await message.answer(
            f"Привет, {full_name}! 👋\n"
            f"Я бот для ведения тренировок по фристайл-слалому.\n\n"
            f"⏳ Ваша заявка отправлена тренеру на одобрение. "
            f"Как только тренер подтвердит профиль, вы сможете подключаться к тренировкам по PIN-коду!"
        )
        return

    # ==========================================
    # СТАРАЯ ЛОГИКА ТРЕНЕРА (ОСТАЛАСЬ БЕЗ ИЗМЕНЕНИЙ)
    # ==========================================
    if user.role == "coach":
        # Проверяем, есть ли активная тренировка, чтобы показать кнопку «Продолжить»
        active_w = (await session.execute(
            select(Workout).where(Workout.status == "active")
        )).scalar_one_or_none()
        await message.answer(
            f"👑 Рады видеть вас, Шеф! Добро пожаловать, Тренер <b>{user.full_name}</b>!\n"
            "Все судейские связки и лимиты опыта активны. Выберите действие:",
            reply_markup=get_coach_main_menu_sync(has_active_workout=active_w is not None)
        )
        return

    # ==========================================
    # ЛОГИКА ДЛЯ СУЩЕСТВУЮЩИХ СПОРТСМЕНОВ
    # ==========================================
    else:
        # Если спортсмен еще не одобрен тренером
        if not user.is_approved:
            await message.answer(
                f"⏳ Привет, <b>{user.full_name}</b>!\n\n"
                f"Ваша заявка всё ещё находится на рассмотрении у тренера.\n"
                f"Пожалуйста, дождитесь подтверждения."
            )
        # Если одобрен — открываем доступ к панели управления
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
        is_approved=True            #нужно ли тут?
    )
    session.add(new_coach)

    # commit выполнится автоматически благодаря нашей Middleware! session туда попадает и проверяется
    
    await callback.message.edit_text(
        f"👑 Авторизация успешна!\nСоздан профиль Тренера: <b>{full_name}</b>.\n"
        "Вам доступно управление тренировочным процессом.",
    )
    # Выводим главное меню тренера (с учётом активной тренировки)
    active_w = (await session.execute(
        select(Workout).where(Workout.status == "active")
    )).scalar_one_or_none()
    await callback.message.answer("Выберите действие:", reply_markup=get_coach_main_menu_sync(has_active_workout=active_w is not None))
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


@router.message(Command("set_student"))
async def set_role_student(message: Message, session: AsyncSession):
    """Секретная команда: принудительно делает тебя УЧЕНИКОМ в базе данных"""
    # 1. Ищем твою запись в базе
    result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = result.scalar_one_or_none()
    
    # 2. Если тебя вообще нет в базе (например, очистил БД), создаем профиль
    if not user:
        user = User(
            telegram_id=message.from_user.id,
            full_name=message.from_user.full_name,
            role="student",
            is_approved=True  # Сразу одобряем себя
        )
        session.add(user)
    else:
        # Если ты был, просто меняем роль и одобряем
        user.role = "student"
        user.is_approved = True
        
    await session.commit()
    
    await message.answer(
        "🔄 **Роль успешно изменена на: СПОРТСМЕН (Ученик)**\n"
        "Теперь тебе доступно игровое RPG-меню и внесение трюков!",
        reply_markup=get_skater_main_menu(),
        parse_mode="Markdown"
    )

@router.message(Command("set_coach"))
async def set_role_coach(message: Message, session: AsyncSession):
    """Секретная команда: принудительно делает тебя ТРЕНЕРОМ в базе данных"""
    result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = result.scalar_one_or_none()
    
    if not user:
        user = User(
            telegram_id=message.from_user.id,
            full_name=message.from_user.full_name,
            role="coach",
            is_approved=True
        )
        session.add(user)
    else:
        user.role = "coach"
        user.is_approved = True
        
    await session.commit()
    
    # Сюда можно подставить твою клавиатуру тренера, если она есть
    await message.answer(
        "🔄 **Роль успешно изменена на: ТРЕНЕР**\n"
        "Теперь ты можешь управлять тренировками и подтверждать трюки!",
        # reply_markup=get_coach_main_menu(), # Раскомментируй, если кнопка готова
        parse_mode="Markdown"
    )

@router.message(Command("reset"))
async def cmd_reset_profile(message: Message, state: FSMContext, session: AsyncSession):
    """Секретная команда для сброса профиля (удаления из БД)"""
    await state.clear()
    tg_id = message.from_user.id
    
    # Стираем пользователя из базы данных по его Telegram ID
    query = delete(User).where(User.telegram_id == tg_id)
    await session.execute(query)
    # commit выполнится автоматически вашей Middleware!
    
    await message.answer(
        "🗑 <b>Ваш профиль успешно удален из базы данных!</b>\n"
        "Теперь вы можете ввести /start, чтобы пройти регистрацию заново, как в самый первый раз."
    )