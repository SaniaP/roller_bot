from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

# =====================================================================
# REPLY-МЕНЮ ГОСТЯ (незарегистрированный пользователь)
# =====================================================================

GUEST_REGISTER_BTN = "📝 Зарегистрироваться как спортсмен"
HANDBOOK_BTN = "📚 База трюков"
RATINGS_BTN = "📈 Рейтинги"


def get_guest_menu() -> ReplyKeyboardMarkup:
    """Reply-меню для незарегистрированных пользователей (гостей)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=GUEST_REGISTER_BTN)],
            [KeyboardButton(text=HANDBOOK_BTN), KeyboardButton(text=RATINGS_BTN)],
        ],
        resize_keyboard=True,
    )


# =====================================================================
# INLINE-КЛАВИАТУРА ЗАЯВКИ НА РЕГИСТРАЦИЮ
# =====================================================================

CALLBACK_SEND_REQUEST = "guest_send_request"


def get_request_registration_kb() -> InlineKeyboardMarkup:
    """Inline-кнопка отправки заявки тренеру (красивое сообщение при /start гостя)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 Отправить заявку тренеру",
                    callback_data=CALLBACK_SEND_REQUEST,
                )
            ]
        ]
    )
