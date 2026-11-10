import logging
from html import escape

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from models import User, Workout
from config import CHIEF_COACH_ID

# Импортируем клавиатуры 
from keyboards import get_coach_main_menu_sync
from keyboards.coach_kb import get_approve_user_kb
from keyboards.skater_kb import get_student_main_menu
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
            full_name=full_name,
            role="student",
            is_approved=False,
            level=1  # Стартовый уровень в RPG-системе
        )
        session.add(new_user)
        await session.commit()
        
        # Отправляем запрос тренеру на одобрение заявки
        try:
            safe_name = escape(full_name or "")
            safe_username = escape(username) if username else "нет"
            await message.bot.send_message(
                chat_id=CHIEF_COACH_ID,
                text=(
                    "🔔 <b>Новая заявка на регистрацию!</b>\n\n"
                    f"Спортсмен: {safe_name}\n"
                    f"Username: @{safe_username}\n"
                    f"ID: <code>{tg_id}</code>\n\n"
                    "Разрешить ему доступ к тренировкам?"
                ),
                reply_markup=get_approve_user_kb(tg_id),
                parse_mode="HTML",
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
            f"👑 Рады видеть вас, Шеф! Добро пожаловать, Тренер <b>{escape(user.full_name or '')}</b>!\n"
            "Все судейские связки и лимиты опыта активны. Выберите действие:",
            reply_markup=get_coach_main_menu_sync(has_active_workout=active_w is not None),
            parse_mode="HTML",
        )
        return

    # ==========================================
    # ЛОГИКА ДЛЯ СУЩЕСТВУЮЩИХ СПОРТСМЕНОВ
    # ==========================================
    else:
        # Если спортсмен еще не одобрен тренером
        if not user.is_approved:
            await message.answer(
                f"⏳ Привет, <b>{escape(user.full_name or '')}</b>!\n\n"
                "Ваша заявка всё ещё находится на рассмотрении у тренера.\n"
                "Пожалуйста, дождитесь подтверждения.",
                parse_mode="HTML",
            )
        # Если одобрен — открываем доступ к панели управления
        else:
            await message.answer(
                f"🛹 Привет, <b>{escape(user.full_name or '')}</b>!\n"
                f"Твой текущий уровень: <b>{user.level}</b> лвл. Ты готов прокачаться?!\n"
                "Выбирай действие на панели управления:",
                reply_markup=get_student_main_menu(),
                parse_mode="HTML",
            )

@router.callback_query(F.data.startswith("approve_user:"))
async def approve_user_handler(callback: CallbackQuery, session: AsyncSession):
    """Тренер одобряет заявку: юзер получает доступ к меню ученика"""
    student_tg_id = int(callback.data.split(":")[1])

    user = (await session.execute(
        select(User).where(User.telegram_id == student_tg_id)
    )).scalar_one_or_none()

    if not user:
        await callback.answer("❌ Пользователь не найден в базе (возможно, удалил профиль).", show_alert=True)
        return

    user.is_approved = True
    await session.commit()

    # Убираем кнопки и сообщаем тренеру
    await callback.message.edit_text(
        f"✅ Заявка <b>{escape(user.full_name or '')}</b> одобрена! Доступ к тренировкам открыт.",
        parse_mode="HTML",
    )
    await callback.answer("Заявка одобрена!")

    # Уведомляем ученика, что его одобрили
    try:
        await callback.bot.send_message(
            chat_id=student_tg_id,
            text=(
                f"🎉 Отличные новости, <b>{escape(user.full_name or '')}</b>!\n"
                "Тренер одобрил твою заявку! Теперь тебе доступно полное меню бота."
            ),
            reply_markup=get_student_main_menu(),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Не удалось уведомить ученика {student_tg_id} об одобрении: {e}")


@router.callback_query(F.data.startswith("reject_user:"))
async def reject_user_handler(callback: CallbackQuery, session: AsyncSession):
    """Тренер отклоняет заявку: юзер удаляется из базы"""
    student_tg_id = int(callback.data.split(":")[1])

    user = (await session.execute(
        select(User).where(User.telegram_id == student_tg_id)
    )).scalar_one_or_none()

    if user:
        full_name = user.full_name
        await session.delete(user)
        await session.commit()
    else:
        full_name = "неизвестный пользователь"

    # Убираем кнопки и сообщаем тренеру
    await callback.message.edit_text(
        f"❌ Заявка <b>{escape(full_name or '')}</b> отклонена. Профиль удален из базы.",
        parse_mode="HTML",
    )
    await callback.answer("Заявка отклонена.")

    # Уведомляем ученика об отклонении
    try:
        await callback.bot.send_message(
            chat_id=student_tg_id,
            text=(
                "😔 К сожалению, тренер отклонил твою заявку.\n"
                "Если это ошибка — свяжись с тренером и отправь /start заново."
            ),
        )
    except Exception as e:
        logger.error(f"Не удалось уведомить ученика {student_tg_id} об отклонении: {e}")


@router.message(Command("test_mode"))
async def toggle_test_mode(message: Message, state: FSMContext, session: AsyncSession):
    """Секретная команда для Главного Тренера: переключение между режимами Тренер/Ученик"""
    tg_id = message.from_user.id

    # Жесткая проверка: пользоваться командой может ТОЛЬКО шеф
    if tg_id != CHIEF_COACH_ID:
        await message.answer("⛔ Ошибка доступа. Данная команда предназначена только для разработчика/главного тренера.")
        return

    # Сбрасываем текущие состояния FSM, чтобы контексты тренировок не накладывались
    await state.clear()

    # Ищем твой профиль в БД
    query = select(User).where(User.telegram_id == tg_id)
    user = (await session.execute(query)).scalar_one_or_none()

    if not user:
        await message.answer("❌ Сначала зарегистрируйтесь в боте через команду /start")
        return

    # МЕНЯЕМ РОЛЬ НА ПРОТИВОПОЛОЖНУЮ
    if user.role == "coach":
        user.role = "student"
        user.is_approved = True # Чтобы сразу иметь доступ к PIN-коду
        await session.commit() 
        
        await message.answer(
            "🔄 <b>РЕЖИМ ТЕСТИРОВАНИЯ: УЧЕНИК</b> 🛹\n\n"
            "Твоя роль в базе временно изменена на <code>student</code>.\n"
            "Теперь ты можешь проверить ввод PIN-кода и отправку трюков.",
            reply_markup=get_student_main_menu(),
            parse_mode="HTML",
        )
    else:
        user.role = "coach"
        await session.commit() 
        
        # Проверяем активную тренировку для правильного отображения меню тренера
        active_w = (await session.execute(
            select(Workout).where(Workout.status == "active")
        )).scalar_one_or_none()
        
        await message.answer(
            "🔄 <b>РЕЖИМ ТЕСТИРОВАНИЯ: ТРЕНЕР</b> 👑\n\n"
            "Роль успешно возвращена на <code>coach</code>.\n"
            "Вам снова доступна панель управления процессом.",
            reply_markup=get_coach_main_menu_sync(has_active_workout=active_w is not None),
            parse_mode="HTML",
        )


@router.message(Command("test_new_user"))
async def simulate_new_user(message: Message, state: FSMContext, session: AsyncSession):
    """Эмуляция НОВОГО пользователя: полностью стирает твой профиль из БД,
    чтобы следующий /start прошел весь путь регистрации с нуля
    (заявка тренеру -> одобрение -> доступ)."""
    tg_id = message.from_user.id

    if tg_id != CHIEF_COACH_ID:
        await message.answer("⛔ Ошибка доступа. Данная команда предназначена только для разработчика/главного тренера.")
        return

    # Сбрасываем FSM, чтобы не осталось контекста тренировок/регистраций
    await state.clear()

    # Удаляем профиль — теперь бот считает тебя абсолютно новым юзером
    query = delete(User).where(User.telegram_id == tg_id)
    await session.execute(query)
    await session.commit()

    await message.answer(
        "🧪 <b>РЕЖИМ ТЕСТИРОВАНИЯ: НОВЫЙ ПОЛЬЗОВАТЕЛЬ</b>\n\n"
        "Твой профиль полностью удален из базы данных.\n"
        "Теперь отправь /start — бот пройдет весь путь регистрации заново: "
        "создаст новую заявку и отправит её тренеру на одобрение.",
        parse_mode="HTML",
    )