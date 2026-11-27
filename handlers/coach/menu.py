"""Хендлеры тренера: главный экран, отмена, добавление ученика, статистика."""

from datetime import datetime

from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from models import User, CoachStudent, Workout, WorkoutRegistration, STATUS_ACTIVE
from states.coach_states import CoachRegistrationStates
from keyboards.coach_kb import get_cancel_keyboard
from handlers.coach.helpers import get_coach_menu_for, get_coach_menu_for_tg

router = Router()


# ==========================================
# СИСТЕМНЫЕ КОМАНДЫ
# ==========================================


@router.message(F.text == "❌ Отмена")
async def cmd_cancel(message: Message, state: FSMContext, session: AsyncSession):
    await state.clear()
    await message.answer(
        "❌ Действие отменено. Возвращаю вас в главное меню.",
        reply_markup=await get_coach_menu_for_tg(session, message.from_user.id),
    )


# ==========================================
# РУЧНОЕ ДОБАВЛЕНИЕ УЧЕНИКА ТРЕНЕРОМ
# ==========================================
@router.message(F.text == "👤 Добавить ученика")
async def coach_add_student_start(message: Message, state: FSMContext):
    await message.answer(
        "📝 Вы инициировали ручное добавление ученика.\nВведите его <b>Фамилию и Имя</b>:",
        reply_markup=get_cancel_keyboard(),
    )
    await state.set_state(CoachRegistrationStates.waiting_for_name)


@router.message(CoachRegistrationStates.waiting_for_name)
async def coach_add_student_name(message: Message, state: FSMContext):
    full_name = message.text.strip()
    if len(full_name) < 3 or " " not in full_name:
        await message.answer("⚠️ Введите корректно Фамилию и Имя через пробел:")
        return
    await state.update_data(full_name=full_name)
    await message.answer(
        f"Имя принято: <b>{full_name}</b>.\nТеперь введите его <b>дату рождения</b> (ДД.ММ.ГГГГ):"
    )
    await state.set_state(CoachRegistrationStates.waiting_for_birth_date)


@router.message(CoachRegistrationStates.waiting_for_birth_date)
async def coach_add_student_finish(
    message: Message, state: FSMContext, session: AsyncSession
):
    date_text = message.text.strip()
    try:
        birth_date = datetime.strptime(date_text, "%d.%m.%Y").date()
    except ValueError:
        await message.answer("⚠️ Неверный формат даты. Используйте ДД.ММ.ГГГГ:")
        return

    data = await state.get_data()
    new_student = User(
        telegram_id=None,
        full_name=data.get("full_name"),
        birth_date=birth_date,
        role="student",
        is_approved=True,
    )
    session.add(new_student)
    await session.flush()  # Получаем new_student.id до финального commit

    coach = (
        await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
    ).scalar_one_or_none()

    if coach:
        session.add(CoachStudent(coach_id=coach.id, student_id=new_student.id))

    await session.commit()
    await state.clear()

    await message.answer(
        f"🎉 Профиль ученика успешно создан!",
        reply_markup=await get_coach_menu_for(session, coach.id if coach else None),
    )


# ==========================================
# ПЕРЕКЛЮЧЕНИЕ: МЕНЮ ТРЕНЕРА ↔ МЕНЮ СПОРТСМЕНА
# (только для тренера в режиме спортсмена, athlete_community_id заполнен)
# ==========================================


@router.message(F.text == "🏂 Меню спортсмена")
async def coach_to_athlete_menu(message: Message, session: AsyncSession):
    """Тренер переключается в интерфейс спортсмена (без изменения БД).
    Кнопка видна только при включенном athlete-mode."""
    user = (
        await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
    ).scalar_one_or_none()
    if not user or not user.is_athlete_mode:
        await message.answer("⛔ Режим спортсмена для вас не включен.")
        return
    from keyboards.skater_kb import get_student_main_menu

    # Динамическая кнопка тренировки: если есть незавершенная личная — «Продолжить»
    has_active_workout = (
        await session.execute(
            select(Workout.id)
            .join(
                WorkoutRegistration,
                WorkoutRegistration.workout_id == Workout.id,
            )
            .where(
                WorkoutRegistration.student_id == user.id,
                Workout.status == STATUS_ACTIVE,
                Workout.is_solo.is_(True),
            )
            .limit(1)
        )
    ).scalar_one_or_none() is not None

    await message.answer(
        "🏂 <b>Режим спортсмена</b>\n\n"
        "Вы выступаете как спортсмен за свое комьюнити. "
        "Личные тренировки идут в его рейтинг и не мешают вашим ученикам.",
        reply_markup=get_student_main_menu(
            is_solo=True,
            is_coach=True,
            has_active_workout=has_active_workout,
        ),
        parse_mode="HTML",
    )


@router.message(F.text == "👨‍🏫 Меню тренера")
async def athlete_to_coach_menu(message: Message, session: AsyncSession):
    """Тренер-спортсмен возвращается из интерфейса спортсмена в меню тренера
    (без изменения БД). Обычный студент сюда попасть не может: кнопка
    в клавиатуре не показывается, а хендлер проверяет и роль, и athlete-mode."""
    user = (
        await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
    ).scalar_one_or_none()
    if not user or not user.is_athlete_mode:
        await message.answer("⛔ Меню тренера доступно только тренерам.")
        return
    await message.answer(
        "👨‍🏫 <b>Меню тренера</b>",
        reply_markup=await get_coach_menu_for(session, user.id),
        parse_mode="HTML",
    )
