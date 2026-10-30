"""Справочник трюков (только просмотр)."""

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from models import Trick
from keyboards.coach_kb import HandbookAction
from handlers.coach.helpers import get_trick_categories

router = Router()


def _handbook_categories_kb(categories: list[str]):
    """Клавиатура выбора категории справочника"""
    builder = InlineKeyboardBuilder()
    for cat in categories:
        builder.button(
            text=str(cat).capitalize(),
            callback_data=HandbookAction(action="hb_cat", category=str(cat)),
        )
    builder.adjust(2)
    return builder.as_markup()


@router.message(F.text == "📚 Справочник трюков")
async def show_tricks_handbook(message: Message, session: AsyncSession):
    """Просмотр справочника трюков: выбор категории"""
    categories = await get_trick_categories(session)

    if not categories:
        await message.answer("🤖 Справочник трюков пока пуст. Наполните базу данных.")
        return

    await message.answer(
        "📚 Справочник трюков. Выберите категорию:",
        reply_markup=_handbook_categories_kb(categories),
    )


@router.callback_query(HandbookAction.filter(F.action == "hb_cat"))
async def show_handbook_tricks(
    callback: CallbackQuery, callback_data: HandbookAction, session: AsyncSession
):
    """Список трюков выбранной категории с их параметрами"""
    tricks_stmt = select(Trick).where(Trick.category == callback_data.category)
    tricks = (await session.execute(tricks_stmt)).scalars().all()

    builder = InlineKeyboardBuilder()
    for trick in tricks:
        builder.button(
            text=f"{trick.name} (Tier {trick.trick_tier})",
            callback_data=HandbookAction(
                action="hb_trick", category=callback_data.category, trick_id=trick.id
            ),
        )
    builder.button(
        text="⬅️ К категориям", callback_data=HandbookAction(action="hb_back")
    )
    builder.adjust(1)

    await callback.message.edit_text(
        f"📚 Категория: {callback_data.category.upper()}. Выберите трюк:",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(HandbookAction.filter(F.action == "hb_trick"))
async def show_handbook_trick_details(
    callback: CallbackQuery, callback_data: HandbookAction, session: AsyncSession
):
    """Карточка трюка с коэффициентами и наградами"""
    trick = await session.get(Trick, callback_data.trick_id)
    if not trick:
        await callback.answer("Трюк не найден", show_alert=True)
        return

    text = (
        f"🎯 **{trick.name}**\n\n"
        f"📦 Категория: {trick.category}\n"
        f"🏅 Тир (сложность): {trick.trick_tier}\n"
        f"📐 База: {trick.base_quantity} конусов\n\n"
        f"📏 Коэфф. дорожки 50 см: {trick.coeff_50}\n"
        f"📏 Коэфф. дорожки 80 см: {trick.coeff_80}\n"
        f"📏 Коэфф. дорожки 120 см: {trick.coeff_120}\n\n"
        f"⭐ Базовые баллы: {trick.base_tech_points}\n"
        f"✨ Базовый опыт: {trick.base_xp} XP\n"
        f"➕ Бонус за конус свыше базы: +{int(trick.extra_bonus_pct * 100)}%"
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text="⬅️ К списку трюков",
        callback_data=HandbookAction(action="hb_cat", category=callback_data.category),
    )
    builder.adjust(1)

    await callback.message.edit_text(
        text, reply_markup=builder.as_markup(), parse_mode="Markdown"
    )


@router.callback_query(HandbookAction.filter(F.action == "hb_back"))
async def show_handbook_back_to_categories(
    callback: CallbackQuery, session: AsyncSession
):
    categories = await get_trick_categories(session)
    await callback.message.edit_text(
        "📚 Справочник трюков. Выберите категорию:",
        reply_markup=_handbook_categories_kb(categories),
    )
