from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

def get_role_selection_kb() -> InlineKeyboardMarkup:
    """Генерирует инлайн-кнопки выбора начальной роли при первой команде /start"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="👑 Я Тренер", callback_data="select_role_coach"),
        InlineKeyboardButton(text="🛹 Я Ученик", callback_data="select_role_student")
    )
    return builder.as_markup()