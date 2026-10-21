from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder

def get_skater_main_menu() -> ReplyKeyboardMarkup:
    """Главное меню для роли Спортсмена (Ученика)"""
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="🎯 Внести выполненный трюк"))
    builder.row(
        KeyboardButton(text="📊 Мой Hero-Профиль"),
        KeyboardButton(text="🛹 Мои трюки")
    )
    builder.row(KeyboardButton(text="📜 История тренировок"))
    return builder.as_markup(resize_keyboard=True)