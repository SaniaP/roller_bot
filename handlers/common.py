import logging
from html import escape
from typing import Optional

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from models import STATUS_ACTIVE, User, Workout, WorkoutRegistration, CoachStudent
from config import CHIEF_COACH_ID

# Импортируем клавиатуры
from keyboards import get_coach_main_menu_sync
from keyboards.coach_kb import get_approve_user_kb
from keyboards.skater_kb import get_student_main_menu
from states.workout_states import StudentWorkoutStates, CoachWorkoutStates
from states.skater_states import (
    SkaterRegistrationStates,
    SkaterWorkoutStates,
    StudentStates,
)
from states.coach_states import (
    CoachRegistrationStates,
    WorkoutCreationStates,
    ComboBuilderStates,
    WorkoutVerificationStates,
)
from keyboards.common_kb import (
    get_guest_menu,
    get_role_choice_kb,
    get_request_registration_kb,
    get_coach_pick_kb,
    CALLBACK_SEND_REQUEST,
    CALLBACK_REG_ROLE_SKATER,
    CALLBACK_REG_ROLE_VIEWER,
    CALLBACK_REG_ROLE_SOLO,
    GUEST_REGISTER_BTN,
    VIEWER_WATCH_BTN,
    HANDBOOK_BTN,
    RATINGS_BTN,
)


async def get_coach_for_student(
    session: AsyncSession, student_id: int
) -> Optional[User]:
    """Возвращает тренера ученика через CoachStudent (первого)."""
    link = (
        await session.execute(
            select(CoachStudent).where(CoachStudent.student_id == student_id)
        )
    ).scalar_one_or_none()
    if not link:
        return None
    return await session.get(User, link.coach_id)


async def notify_coach_about_request(
    bot, coach: User, text: str, reply_markup, session: AsyncSession
) -> bool:
    """Отправляет уведомление тренеру; если у тренера нет telegram_id ИЛИ
    отправка не удалась (бот заблокирован и т.п.) — отправляет главному
    тренеру (CHIEF_COACH_ID). Причина ошибки пишется в лог."""
    targets = []
    if coach.telegram_id:
        targets.append(coach.telegram_id)
    if CHIEF_COACH_ID not in targets:
        targets.append(CHIEF_COACH_ID)  # фолбэк: главный тренер всегда получит заявку

    last_error = None
    for target in targets:
        try:
            await bot.send_message(
                chat_id=target, text=text, reply_markup=reply_markup, parse_mode="HTML"
            )
            return True
        except Exception as e:
            last_error = e
            logger.warning(f"Не удалось отправить уведомление тренеру {target}: {e!r}")

    logger.error(
        f"Уведомление о заявке не доставлено никому из {targets}: {last_error!r}"
    )
    return False


logger = logging.getLogger(__name__)

router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext, session: AsyncSession):
    """Хэндлер команды /start. Проверяет авторизацию или показывает гостевое меню."""
    await state.clear()  # Сброс FSM для текущего юзера
    tg_id = message.from_user.id

    # 1. Проверяем, есть ли пользователь в базе данных
    query = select(User).where(User.telegram_id == tg_id)
    result = await session.execute(query)
    user = result.scalar_one_or_none()

    # ==========================================
    # ГОСТЬ — НОВЫЙ ПОЛЬЗОВАТЕЛЬ (НЕ СОЗДАЕМ ЗАПИСЬ В БД)
    # ==========================================
    if user is None:
        full_name = (message.from_user.full_name or "").strip().title()
        await message.answer(
            f"👋 Привет, <b>{escape(full_name)}</b>!\n\n"
            "🛼 Добро пожаловать в <b>Roller Heroes</b> — твой трекер тренировок по фристайл-слалому.\n"
            "Здесь ты закрываешь трюки, фармишь опыт (EXP), прокачиваешь персонажа и штурмуешь рейтинги!\n\n"
            "✨ <b>Чтобы начать прокачку и открыть полный доступ к рейтингам, нажми «📝 Зарегистрироваться» внизу.</b>\n"
            "<i>(Гостям доступна база трюков и только ТОП-10 лучших райдеров).</i>",
            reply_markup=get_guest_menu(),
            parse_mode="HTML",
        )
        return

    # ==========================================
    # ЛОГИКА ТРЕНЕРА (ОСТАЛАСЬ БЕЗ ИЗМЕНЕНИЙ)
    # ==========================================
    if user.role == "coach":
        # Проверяем, есть ли активная тренировка, чтобы показать кнопку «Продолжить»
        active_w = (
            await session.execute(
                select(Workout).where(Workout.status == STATUS_ACTIVE).limit(1)
            )
        ).scalar_one_or_none()
        await message.answer(
            f"👑 Рады видеть вас, Шеф! Добро пожаловать, Тренер <b>{escape(user.full_name or '')}</b>!\n"
            "Все судейские связки и лимиты опыта активны. Выберите действие:",
            reply_markup=get_coach_main_menu_sync(
                has_active_workout=active_w is not None
            ),
            parse_mode="HTML",
        )
        return

    # ==========================================
    # ЛОГИКА ДЛЯ СУЩЕСТВУЮЩИХ СПОРТСМЕНОВ И ЗРИТЕЛЕЙ
    # ==========================================
    else:
        # Если пользователь еще не одобрен тренером (спортсмен или зритель)
        if not user.is_approved:
            await message.answer(
                f"⏳ Привет, <b>{escape(user.full_name or '')}</b>!\n\n"
                "Ваша заявка всё ещё находится на рассмотрении у тренера.\n"
                "Пожалуйста, дождитесь подтверждения.",
                reply_markup=get_guest_menu(),
                parse_mode="HTML",
            )
        # Одобренный зритель — меню с рейтингом (полный доступ)
        elif user.role == "viewer":
            await message.answer(
                f"👀 С возвращением, <b>{escape(user.full_name or '')}</b>!\n\n"
                "Вы подтверждены как зритель. Полный рейтинг доступен "
                "через кнопку «📈 Рейтинги».",
                reply_markup=get_guest_menu(is_viewer=True),
                parse_mode="HTML",
            )
        # Если одобрен — открываем доступ к панели управления
        else:
            await message.answer(
                f"🛹 Привет, <b>{escape(user.full_name or '')}</b>!\n"
                f"Твой текущий уровень: <b>{user.level}</b> лвл. Ты готов прокачаться?!\n"
                "Выбирай действие на панели управления:",
                reply_markup=get_student_main_menu(is_solo=bool(user.is_solo)),
                parse_mode="HTML",
            )


# =====================================================================
# ЗАЯВКА НА РЕГИСТРАЦИЮ (ГОСТЬ)
# =====================================================================


@router.message(F.text == GUEST_REGISTER_BTN)
async def guest_register_request(message: Message, session: AsyncSession):
    """Гость нажимает 'Зарегистрироваться' — предлагаем выбрать роль."""
    tg_id = message.from_user.id
    user = (
        await session.execute(select(User).where(User.telegram_id == tg_id))
    ).scalar_one_or_none()

    # Если заявка уже на рассмотрении — напоминаем
    if user is not None and not user.is_approved:
        await message.answer(
            "⏳ Ваша заявка уже находится на рассмотрении у тренера.\n"
            "Пожалуйста, дождитесь решения. 🙏"
        )
        return

    # Если пользователь уже одобрен — просто показываем меню ученика
    if user is not None and user.is_approved:
        if user.role == "viewer":
            # Зрителю не показываем «меню спортсмена»: возвращаем зрительское меню
            await message.answer(
                "👀 Вы уже зарегистрированы как зритель и одобрены тренером.\n"
                "Полный рейтинг доступен через кнопку «📈 Рейтинги»,\n"
                "а профиль наблюдаемого спортсмена — через «👤 Наблюдать за персонажем».",
                reply_markup=get_guest_menu(is_viewer=True),
            )
        else:
            await message.answer(
                "✅ Вы уже зарегистрированы и одобрены тренером!",
                reply_markup=get_student_main_menu(),
            )
        return

    # Гость еще не в базе — предлагаем выбрать роль
    await message.answer(
        "🛼 <b>Регистрация в Roller Heroes</b>\n\n"
        "Выбери подходящий формат участия:\n"
        "• <b>Спортсмен</b> — занятия под руководством тренера и участие в общих рейтингах.\n"
        "• <b>Одиночный режим</b> — самостоятельный учёт тренировок и прокачка персонажа.\n"
        "• <b>Зритель</b> — наблюдение за тренировками и прогрессом одного спортсмена.\n\n"
        "👇 <b>Выбери свою роль:</b>",
        reply_markup=get_role_choice_kb(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == CALLBACK_REG_ROLE_SKATER)
async def guest_pick_skater(callback: CallbackQuery, session: AsyncSession):
    """Гость выбрал [Спортсмен] — показываем список тренеров для выбора."""
    coaches = (
        (
            await session.execute(
                select(User).where(User.role == "coach").order_by(User.full_name)
            )
        )
        .scalars()
        .all()
    )
    if not coaches:
        await callback.answer(
            "😔 Пока нет ни одного тренера. Попробуйте позже.", show_alert=True
        )
        return
    await callback.message.edit_text(
        "🛼 <b>Выбери своего тренера</b>\n\n"
        "Заявка уйдет выбранному тренеру на одобрение.",
        reply_markup=get_coach_pick_kb(coaches),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == CALLBACK_REG_ROLE_SOLO)
async def guest_pick_solo(callback: CallbackQuery, session: AsyncSession):
    """Гость выбрал [Одиночный режим] — сразу создаем одобренного соло-спортсмена."""
    tg_id = callback.from_user.id
    existing = (
        await session.execute(select(User).where(User.telegram_id == tg_id))
    ).scalar_one_or_none()
    if existing:
        await callback.answer("⏳ Вы уже зарегистрированы в боте.", show_alert=True)
        return

    new_user = User(
        telegram_id=tg_id,
        full_name=callback.from_user.full_name or "Спортсмен",
        role="student",
        is_approved=True,  # соло-режим не требует одобрения тренером
        is_solo=True,
        level=1,
    )
    session.add(new_user)
    await session.commit()

    await callback.answer("🚀 Добро пожаловать в одиночный режим!")
    await callback.message.answer(
        f"🚀 <b>{escape(new_user.full_name or '')}</b>, вы в одиночном режиме!\n\n"
        "Вы тренируетесь самостоятельно: трюки засчитываются сразу, "
        "без судейства тренера.\n\n"
        "▶️ Начните личную тренировку кнопкой ниже.",
        reply_markup=get_student_main_menu(is_solo=True),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("reg_pick_coach:"))
async def guest_pick_coach(callback: CallbackQuery, session: AsyncSession):
    """Гость выбрал тренера — создаем неодобренного студента и шлем заявку
    именно этому тренеру."""
    raw = callback.data.split(":")[1]
    if raw == "cancel":
        await callback.message.edit_text("🚫 Регистрация отменена.")
        await callback.answer()
        return

    coach_id = int(raw)
    coach = await session.get(User, coach_id)
    tg_id = callback.from_user.id

    if not coach or coach.role != "coach":
        await callback.answer("❌ Тренер не найден, выберите другого.", show_alert=True)
        return

    existing = (
        await session.execute(select(User).where(User.telegram_id == tg_id))
    ).scalar_one_or_none()

    solo_transition = False
    if existing is not None and not existing.is_solo:
        # Уже на рассмотрении ИЛИ уже одобренный спортсмен/зритель
        if existing.is_approved:
            await callback.answer(
                "✅ Вы уже занимаетесь с тренером — новая заявка не нужна.",
                show_alert=True,
            )
        else:
            await callback.answer(
                "⏳ Ваша заявка уже на рассмотрении у тренера.", show_alert=True
            )
        return

    if existing is not None and existing.is_solo:
        # Переход из Соло: сбрасываем рейтинговый прогресс полностью.
        # Логи тренировок (TrickLog) и архив соло-тренировок остаются —
        # но в общие/клубные рейтинги они не попадают.
        solo_transition = True
        existing.is_solo = False
        existing.is_approved = False
        existing.role = "student"
        existing.level = 1
        existing.xp = 0
        existing.title = "Новичок"
        new_user = existing
        await session.commit()
    else:
        new_user = User(
            telegram_id=tg_id,
            full_name=callback.from_user.full_name or "Спортсмен",
            role="student",
            is_approved=False,
            is_solo=False,
            level=1,
        )
        session.add(new_user)
        await session.commit()

    username = callback.from_user.username
    safe_name = escape(new_user.full_name or "")
    safe_username = escape(username) if username else "нет"
    sent = await notify_coach_about_request(
        callback.bot,
        coach,
        (
            "🔔 <b>Новая заявка на регистрацию!</b>\n\n"
            + (
                "⚠️ <i>Переход из Соло-режима!</i>\n"
                "Рейтинговый прогресс будет сброшен при одобрении.\n\n"
                if solo_transition
                else ""
            )
            + f"Спортсмен: <a href='tg://user?id={tg_id}'>{safe_name}</a>\n"
            f"Username: @{safe_username}\n"
            f"ID: <code>{tg_id}</code>\n\n"
            "Разрешить ему доступ к тренировкам?"
        ),
        get_approve_user_kb(tg_id, coach_id=coach.id),
        session,
    )

    await callback.answer(
        "📨 Заявка отправлена тренеру!" if sent else "⚠️ Тренер недоступен.",
        show_alert=not sent,
    )
    if sent:
        await callback.message.edit_text(
            "⏳ Ваша заявка отправлена тренеру на одобрение.\n"
            "Как только тренер подтвердит профиль, вы сможете подключаться к тренировкам!"
        )
    else:
        await callback.message.edit_text(
            "⚠️ Не удалось доставить заявку тренеру.\n"
            "Попробуйте позже или напишите тренеру напрямую."
        )


@router.callback_query(F.data == CALLBACK_REG_ROLE_VIEWER)
async def guest_pick_viewer(callback: CallbackQuery, state: FSMContext):
    """Гость выбрал [Зритель] — запрашиваем, за кем он наблюдает."""
    await state.set_state(StudentStates.waiting_for_viewer_target)
    await callback.message.edit_text(
        "👀 <b>Регистрация зрителя</b>\n\n"
        "Укажите спортсмена, за кем вы наблюдаете — напишите его "
        "<b>Фамилию и Имя</b> (как записан у тренера).\n\n"
        "После подтверждения тренером вам откроется полный рейтинг."
    )
    await callback.answer()


@router.message(StudentStates.waiting_for_viewer_target, F.text == "❌ Отмена")
async def viewer_reg_cancel(message: Message, state: FSMContext):
    """Отмена регистрации зрителя."""
    await state.clear()
    await message.answer("🚫 Регистрация отменена.", reply_markup=get_guest_menu())


@router.message(StudentStates.waiting_for_viewer_target)
async def viewer_reg_target(message: Message, state: FSMContext, session: AsyncSession):
    """Зритель ввел ФИО спортсмена — проверяем, что такой одобренный спортсмен есть."""
    target_name = (message.text or "").strip()

    athlete = (
        await session.execute(
            select(User).where(
                User.full_name == target_name,
                User.role == "student",
                User.is_approved == True,  # noqa: E712
            )
        )
    ).scalar_one_or_none()

    if not athlete:
        await message.answer(
            "❌ Спортсмен с таким именем не найден среди одобренных.\n"
            "Проверьте написание (Фамилия и Имя) и отправьте еще раз,\n"
            "или нажмите «❌ Отмена»."
        )
        return

    if athlete.telegram_id == message.from_user.id:
        await message.answer(
            "🤔 Вы и есть этот спортсмен? Тогда зарегистрируйтесь как спортсмен."
        )
        return

    await state.update_data(viewer_target_id=athlete.id)
    await state.set_state(StudentStates.waiting_for_viewer_relation)
    await message.answer(
        f"✅ Спортсмен найден: <b>{escape(athlete.full_name or '')}</b>.\n\n"
        "Кем вы приходитесь этому спортсмену, а так же как вас зовут?\n"
        "Напишите одним сообщением, например: «мама, Ева», «папа, Адам», «лучший друг, Боря»."
    )


@router.message(StudentStates.waiting_for_viewer_relation)
async def viewer_reg_relation(
    message: Message, state: FSMContext, session: AsyncSession
):
    """Зритель указал родство — создаем НЕодобренную запись role='viewer'
    и отправляем запрос тренеру на подтверждение."""
    data = await state.get_data()
    target_id = data.get("viewer_target_id")
    relation = (message.text or "").strip()
    await state.clear()

    if not target_id:
        await message.answer("⚠️ Ошибка сценария. Отправьте /start заново.")
        return

    tg_id = message.from_user.id
    existing = (
        await session.execute(select(User).where(User.telegram_id == tg_id))
    ).scalar_one_or_none()
    if existing:
        await message.answer("⏳ Ваша заявка уже на рассмотрении у тренера. 🙏")
        return

    athlete = await session.get(User, target_id)
    if not athlete:
        await message.answer("❌ Спортсмен не найден. Отправьте /start заново.")
        return

    # Зритель попадает в базу только ПОСЛЕ подтверждения тренером:
    # запись создаем неодобренной (is_approved=False), роль viewer.
    new_viewer = User(
        telegram_id=tg_id,
        full_name=message.from_user.full_name or "Зритель",
        role="viewer",
        is_approved=False,
        viewer_for_id=athlete.id,
        relation=relation,
        level=1,
    )
    session.add(new_viewer)
    await session.commit()

    # Заявку зрителя отправляем тренеру выбранного спортсмена
    if athlete.is_solo:
        await message.answer(
            "❌ Этот спортсмен тренируется в одиночном режиме —\n"
            "наблюдение за ним недоступно. Выберите другого спортсмена."
        )
        await session.delete(new_viewer)
        await session.commit()
        return

    coach = await get_coach_for_student(session, athlete.id)
    if not coach:
        await message.answer(
            "❌ У этого спортсмена нет тренера, заявку некому одобрить.\n"
            "Выберите другого спортсмена."
        )
        await session.delete(new_viewer)
        await session.commit()
        return

    try:
        username = message.from_user.username
        safe_viewer = escape(new_viewer.full_name or "")
        safe_athlete = escape(athlete.full_name or "")
        safe_username = escape(username) if username else "нет"
        await notify_coach_about_request(
            bot=message.bot,
            coach=coach,
            text=(
                "🔔 <b>Новая заявка: ЗРИТЕЛЬ!</b>\n\n"
                f"Зритель: <a href='tg://user?id={tg_id}'>{safe_viewer}</a>\n"
                # f"Зритель: {safe_viewer}\n"
                f"Username: @{safe_username}\n"
                f"ID: <code>{tg_id}</code>\n"
                f"Наблюдает за: {safe_athlete}\n"
                f"Кем приходится: {escape(relation)}\n\n"
                "Подтвердить доступ к полному рейтингу?"
            ),
            reply_markup=get_approve_user_kb(tg_id, coach_id=coach.id),
            session=session,
        )
    except Exception as e:
        logger.error(f"Не удалось отправить запрос по зрителю тренеру: {e}")

    await message.answer(
        "📨 Заявка зрителя отправлена тренеру.\n"
        "До одобрения вам доступен только ТОП-10 рейтинга.\n"
        "После подтверждения откроется полный рейтинг. 🙏",
        reply_markup=get_guest_menu(),
    )


@router.message(F.text == VIEWER_WATCH_BTN)
async def viewer_watch_profile(message: Message, session: AsyncSession):
    """Зритель: профиль спортсмена, за которым он наблюдает (как «Мой персонаж»)."""
    from handlers.coach.stats import send_profile_message as _send_profile

    tg_id = message.from_user.id
    user = (
        await session.execute(select(User).where(User.telegram_id == tg_id))
    ).scalar_one_or_none()

    # Доступно только одобренному зрителю
    if not user or user.role != "viewer" or not user.is_approved:
        await message.answer("🔒 Эта кнопка доступна только подтвержденным зрителям.")
        return

    athlete = None
    if user.viewer_for_id:
        athlete = await session.get(User, user.viewer_for_id)

    if not athlete:
        await message.answer(
            "😔 Спортсмен, за которым вы наблюдаете, больше не найден в базе.\n"
            "Свяжитесь с тренером, чтобы переназначить наблюдение."
        )
        return

    await _send_profile(message.bot, message.chat.id, athlete, session)


@router.message(F.text == "🔗 Привязаться к тренеру")
async def solo_link_coach(message: Message, session: AsyncSession):
    """Соло-спортсмен хочет перейти под крыло тренера — список тренеров."""
    user = (
        await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
    ).scalar_one_or_none()
    if not user or not user.is_solo:
        await message.answer("⛔ Эта кнопка для спортсменов в одиночном режиме.")
        return

    coaches = (
        (
            await session.execute(
                select(User).where(User.role == "coach").order_by(User.full_name)
            )
        )
        .scalars()
        .all()
    )
    if not coaches:
        await message.answer("😔 Пока нет ни одного тренера.")
        return

    from keyboards.common_kb import get_coach_pick_kb

    await message.answer(
        "🔗 <b>Переход из Соло-режима</b>\n\n"
        "⚠️ При одобрении тренером ваш рейтинговый прогресс будет сброшен"
        " (XP = 0, уровень = 1), а сольные тренировки останутся в личном архиве, "
        "но не будут учтены в общем ТОПе.\n\n"
        "Выберите тренера:",
        reply_markup=get_coach_pick_kb(coaches),
        parse_mode="HTML",
    )


@router.callback_query(F.data == CALLBACK_SEND_REQUEST)
async def guest_send_request(callback: CallbackQuery, session: AsyncSession):
    """Гость нажал Inline-кнопку: создаем запись в users со статусом is_approved=False
    и уведомляем главного тренера."""
    tg_id = callback.from_user.id

    user = (
        await session.execute(select(User).where(User.telegram_id == tg_id))
    ).scalar_one_or_none()

    # Повторное нажатие, пока заявка на рассмотрении
    if user is not None and not user.is_approved and not user.is_solo:
        await callback.answer(
            "⏳ Ваша заявка уже на рассмотрении у тренера. Дождитесь решения! 🙏",
            show_alert=True,
        )
        return

    # Если вдруг одобрен — просто уведомляем
    if user is not None and user.is_approved:
        await callback.answer("✅ Вы уже одобрены тренером!", show_alert=True)
        return

    # Переход из Соло: пользователь уже есть в базе с is_solo=True
    solo_transition = bool(user is not None and user.is_solo)

    # Старый сценарий (заявка без выбора тренера) —fallback: шлем главному тренеру.
    # Создаем профиль спортсмена в БД со статусом НЕОДОБРЕН (или обновляем соло-юзера)
    if user is not None:
        user.is_approved = False
        user.is_solo = False
        new_user = user
    else:
        new_user = User(
            telegram_id=tg_id,
            full_name=callback.from_user.full_name,
            role="student",
            is_approved=False,
            level=1,  # Стартовый уровень в RPG-системе
        )
        session.add(new_user)
    await session.commit()

    # Отправляем запрос главному тренеру (fallback)
    chief = (
        await session.execute(select(User).where(User.telegram_id == CHIEF_COACH_ID))
    ).scalar_one_or_none()
    try:
        username = callback.from_user.username
        safe_name = escape(callback.from_user.full_name or "")
        safe_username = escape(username) if username else "нет"
        await notify_coach_about_request(
            callback.bot,
            chief or User(telegram_id=CHIEF_COACH_ID, full_name="Шеф"),
            (
                "🔔 <b>Новая заявка на регистрацию!</b>\n\n"
                + (
                    "⚠️ <i>Переход из Соло-режима!</i>\n"
                    f"Текущий прогресс: уровень {new_user.level}, XP {new_user.xp}"
                    " (будет сброшен при одобрении)\n\n"
                    if solo_transition
                    else ""
                )
                + f"Спортсмен: <a href='tg://user?id={tg_id}'>{safe_name}</a>\n"
                # f"Спортсмен: {safe_name}\n"
                f"Username: @{safe_username}\n"
                f"ID: <code>{tg_id}</code>\n\n"
                "Разрешить ему доступ к тренировкам?"
            ),
            get_approve_user_kb(tg_id),
            session,
        )
    except Exception as e:
        logger.error(f"Не удалось отправить уведомление тренеру: {e}")

    await callback.answer("📨 Заявка отправлена тренеру!")
    await callback.message.answer(
        "⏳ Ваша заявка отправлена тренеру на одобрение.\n"
        "Как только тренер подтвердит профиль, вы сможете подключаться к тренировкам!"
    )


# =====================================================================
# ЗАГЛУШКИ ГОСТЯ
# =====================================================================


# «📈 Рейтинги» реализован в handlers/coach/stats.py (роутер тренера
# регистрируется раньше common): гостям — текстовый ТОП-10,
# авторизованным — интерактивные фильтры.


@router.callback_query(F.data.startswith("approve_user:"))
async def approve_user_handler(callback: CallbackQuery, session: AsyncSession):
    """Тренер одобряет заявку: юзер получает доступ к меню ученика.
    callback: approve_user:<student_tg_id>:<coach_id|none>"""
    parts = callback.data.split(":")
    student_tg_id = int(parts[1])

    user = (
        await session.execute(select(User).where(User.telegram_id == student_tg_id))
    ).scalar_one_or_none()

    if not user:
        await callback.answer(
            "❌ Пользователь не найден в базе (возможно, удалил профиль).",
            show_alert=True,
        )
        return

    user.is_approved = True
    await session.commit()

    # Мульти-тренерство: создаем связь CoachStudent с тренером, который одобрил
    approving_coach_id = (
        int(parts[2]) if len(parts) > 2 and parts[2] != "none" else None
    )
    if user.role == "student" and approving_coach_id:
        exists = (
            await session.execute(
                select(CoachStudent).where(
                    CoachStudent.coach_id == approving_coach_id,
                    CoachStudent.student_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if not exists:
            session.add(CoachStudent(coach_id=approving_coach_id, student_id=user.id))

        # Переход из Соло: сбрасываем рейтинговый прогресс до стартового.
        # Сольные тренировки (Workout.is_solo) остаются в архиве, но в общие
        # и клубные рейтинги не попадают.
        if user.is_solo:
            user.is_solo = False
            user.xp = 0
            user.level = 1
            user.title = "Новичок"
        await session.commit()

    is_viewer = user.role == "viewer"

    # Убираем кнопки и сообщаем тренеру
    await callback.message.edit_text(
        (
            f"✅ Зритель <b>{escape(user.full_name or '')}</b> подтвержден! "
            "Доступ к полному рейтингу открыт."
            if is_viewer
            else f"✅ Заявка <b>{escape(user.full_name or '')}</b> одобрена! Доступ к тренировкам открыт."
        ),
        parse_mode="HTML",
    )
    await callback.answer("Заявка одобрена!")

    # Уведомляем ученика/зрителя, что его одобрили
    try:
        await callback.bot.send_message(
            chat_id=student_tg_id,
            text=(
                (
                    f"🎉 Отличные новости, <b>{escape(user.full_name or '')}</b>!\n"
                    "Тренер подтвердил вас как зрителя! Теперь вам доступно полное меню бота."
                )
                if is_viewer
                else (
                    f"🎉 Отличные новости, <b>{escape(user.full_name or '')}</b>!\n"
                    "Тренер одобрил твою заявку! Теперь тебе доступно полное меню бота."
                )
            ),
            reply_markup=(
                get_guest_menu(is_viewer=True) if is_viewer else get_student_main_menu()
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Не удалось уведомить ученика {student_tg_id} об одобрении: {e}")


@router.callback_query(F.data.startswith("reject_user:"))
async def reject_user_handler(callback: CallbackQuery, session: AsyncSession):
    """Тренер отклоняет заявку: юзер удаляется из базы (reject_user:<tg_id>:<...>)"""
    student_tg_id = int(callback.data.split(":")[1])

    user = (
        await session.execute(select(User).where(User.telegram_id == student_tg_id))
    ).scalar_one_or_none()

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
        await message.answer(
            "⛔ Ошибка доступа. Данная команда предназначена только для разработчика/главного тренера."
        )
        return

    # Ищем твой профиль в БД
    query = select(User).where(User.telegram_id == tg_id)
    user = (await session.execute(query)).scalar_one_or_none()

    if not user:
        await message.answer("❌ Сначала зарегистрируйтесь в боте через команду /start")
        return

    # ПОЛНАЯ зачистка FSM-состояний: своя + все известные машины состояний
    # (хэндлеры в разных модулях сидят в разных StatesGroup — простого
    # state.clear() мало, часть сценариев "залипала")
    await state.clear()
    bot_id = message.bot.id
    for group in (
        StudentWorkoutStates,
        CoachWorkoutStates,
        SkaterRegistrationStates,
        SkaterWorkoutStates,
        StudentStates,
        CoachRegistrationStates,
        WorkoutCreationStates,
        ComboBuilderStates,
        WorkoutVerificationStates,
    ):
        stub = FSMContext(
            storage=state.storage,
            key=StorageKey(bot_id=bot_id, chat_id=tg_id, user_id=tg_id),
        )
        await stub.set_state(None)
    # Чистим и данные, и сам state-ключ на всякий случай
    stub = FSMContext(
        storage=state.storage,
        key=StorageKey(bot_id=bot_id, chat_id=tg_id, user_id=tg_id),
    )
    await stub.set_data({})

    # МЕНЯЕМ РОЛЬ НА ПРОТИВОПОЛОЖНУЮ
    if user.role == "coach":
        user.role = "student"
        user.is_approved = True  # Чтобы сразу иметь доступ к PIN-коду
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

        # Регистрацию в активной тренировке СОХРАНЯЕМ: если тестовый ученик
        # подключился по PIN и сдал трюки, он должен остаться в списке
        # участников и участвовать в финализации. Удаляем только FSM-состояние
        # ученика, чтобы не блокировать тренерские сценарии.
        await session.commit()

        # Сбрасываем FSM-состояние ученика (активная тренировка), если оно было
        bot_id = message.bot.id
        stub = FSMContext(
            storage=state.storage,
            key=StorageKey(bot_id=bot_id, chat_id=tg_id, user_id=tg_id),
        )
        await stub.set_state(None)
        await stub.set_data({})

        # Проверяем активную тренировку для правильного отображения меню тренера
        active_w = (
            await session.execute(
                select(Workout).where(Workout.status == STATUS_ACTIVE).limit(1)
            )
        ).scalar_one_or_none()

        await message.answer(
            "🔄 <b>РЕЖИМ ТЕСТИРОВАНИЯ: ТРЕНЕР</b> 👑\n\n"
            "Роль успешно возвращена на <code>coach</code>.\n"
            "Вам снова доступна панель управления процессом.",
            reply_markup=get_coach_main_menu_sync(
                has_active_workout=active_w is not None
            ),
            parse_mode="HTML",
        )


@router.message(Command("test_new_user"))
async def simulate_new_user(message: Message, state: FSMContext, session: AsyncSession):
    """Эмуляция НОВОГО пользователя: полностью стирает твой профиль из БД,
    чтобы следующий /start прошел весь путь регистрации с нуля
    (заявка тренеру -> одобрение -> доступ)."""
    tg_id = message.from_user.id

    if tg_id != CHIEF_COACH_ID:
        await message.answer(
            "⛔ Ошибка доступа. Данная команда предназначена только для разработчика/главного тренера."
        )
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
