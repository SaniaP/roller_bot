from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder

def get_coach_main_menu() -> ReplyKeyboardMarkup:
    """Главное меню для роли Тренера"""
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="🏋️ Начать тренировку"))
    builder.row(
        KeyboardButton(text="👤 Добавить ученика"),
        KeyboardButton(text="📚 Справочник трюков")
    )
    builder.row(KeyboardButton(text="🔔 Проверить тренировки учеников"))
    return builder.as_markup(resize_keyboard=True)

def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    """Универсальная кнопка отмены для выхода из любого пошагового режима FSM"""
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="❌ Отмена"))
    return builder.as_markup(resize_keyboard=True)