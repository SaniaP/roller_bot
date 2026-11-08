"""Внесение трюков тренером: удаление ошибочного лога.

Общий поток выбора трюка (категории, трюки, дорожки, FSM ввода количества,
отмена) для тренера и ученика вынесен в handlers/coach/trick_flow.py.
Здесь остался только хендлер «отменить последний трюк» — он специфичен
для карточки спортсмена у тренера.
"""

from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete

from models import TrickLog
from keyboards.coach_kb import TrickAction, WorkoutAction
from handlers.coach.workout import show_skater_card_or_main_list

router = Router()


@router.callback_query(TrickAction.filter(F.action == "delete_log"))
async def delete_last_trick_log(
    callback: CallbackQuery, callback_data: TrickAction, session: AsyncSession
):
    """Отмена ошибочно внесенного трюка"""
    await session.execute(delete(TrickLog).where(TrickLog.id == callback_data.log_id))
    await session.commit()

    await callback.answer("Последний трюк успешно отменен!", show_alert=True)

    # Перерисовываем карточку
    redirect_action = WorkoutAction(
        action="select_skater",
        workout_id=callback_data.workout_id,
        student_id=callback_data.student_id,
    )
    await show_skater_card_or_main_list(callback, redirect_action, session)
