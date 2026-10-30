"""Хендлеры тренера: главный экран, отмена, добавление ученика, статистика."""

from datetime import datetime

from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from models import User
from states.coach_states import CoachRegistrationStates
from keyboards.coach_kb import get_cancel_keyboard
from handlers.coach.helpers import get_coach_menu_for

router = Router()


# ==========================================
# СИСТЕМНЫЕ КОМАНДЫ
# ==========================================


@router.message(F.text == "❌ Отмена")
async def cmd_cancel(message: Message, state: FSMContext, session: AsyncSession):
    await state.clear()
    await message.answer(
        "❌ Действие отменено. Возвращаю вас в главное меню.",
        reply_markup=await get_coach_menu_for(session),
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
    await session.commit()
    await state.clear()
    await message.answer(
        f"🎉 Профиль ученика успешно создан!",
        reply_markup=await get_coach_menu_for(session),
    )


