from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

# =====================================================================
# REPLY-МЕНЮ ГОСТЯ (незарегистрированный пользователь)
# =====================================================================

GUEST_REGISTER_BTN = "📝 Зарегистрироваться"
VIEWER_WATCH_BTN = "👤 Наблюдать за персонажем"
INLINE_BTN_SKATER = "🛼 Спортсмен"
INLINE_BTN_VIEWER = "👀 Зритель"
INLINE_BTN_SOLO = "🎧 Одиночный режим"
CALLBACK_REG_ROLE_SKATER = "reg_role_skater"
CALLBACK_REG_ROLE_VIEWER = "reg_role_viewer"
CALLBACK_REG_ROLE_SOLO = "reg_role_solo"
CALLBACK_VIEWER_CONFIRM = "viewer_send_request"
HANDBOOK_BTN = "📚 База трюков"
RATINGS_BTN = "📈 Рейтинги"


def get_guest_menu(is_viewer: bool = False) -> ReplyKeyboardMarkup:
    """Reply-меню для незарегистрированных пользователей (гостей).
    Одобренный зритель вместо «Зарегистрироваться» получает кнопку
    «Наблюдать за персонажем» (профиль спортсмена, за которым следит)."""
    first_row = (
        VIEWER_WATCH_BTN if is_viewer else GUEST_REGISTER_BTN
    )
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=first_row)],
            [KeyboardButton(text=HANDBOOK_BTN), KeyboardButton(text=RATINGS_BTN)],
        ],
        resize_keyboard=True,
    )


# =====================================================================
# INLINE-КЛАВИАТУРА ВЫБОРА РОЛИ И ЗАЯВКИ НА РЕГИСТРАЦИЮ
# =====================================================================

CALLBACK_SEND_REQUEST = "guest_send_request"


def get_role_choice_kb() -> InlineKeyboardMarkup:
    """Инлайн-выбор роли: [Спортсмен] [Зритель] / [Одиночный режим]."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=INLINE_BTN_SKATER, callback_data=CALLBACK_REG_ROLE_SKATER
                ),
                InlineKeyboardButton(
                    text=INLINE_BTN_VIEWER, callback_data=CALLBACK_REG_ROLE_VIEWER
                ),
            ],
            [
                InlineKeyboardButton(
                    text=INLINE_BTN_SOLO, callback_data=CALLBACK_REG_ROLE_SOLO
                )
            ],
        ]
    )


def get_coach_pick_kb(coaches: list) -> InlineKeyboardMarkup:
    """Inline-список тренеров для выбора при регистрации спортсмена.
    В payload кнопки зашиваем id тренера."""
    builder_rows = [
        [
            InlineKeyboardButton(
                text=f"👑 {c.full_name}", callback_data=f"reg_pick_coach:{c.id}"
            )
        ]
        for c in coaches
    ]
    builder_rows.append(
        [InlineKeyboardButton(text="❌ Отмена", callback_data="reg_pick_coach:cancel")]
    )
    return InlineKeyboardMarkup(inline_keyboard=builder_rows)


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
def get_help_kb() -> InlineKeyboardMarkup:
    """Инлайн-клавиатура для команды /help: контакты создателя проекта."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✉️ Написать в Telegram",
                    url="https://t.me/Alexarender",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📸 Instagram (@sania87oo)",
                    url="https://instagram.com/sania87oo",
                )
            ],
        ]
    )
