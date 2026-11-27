from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram import types

# =====================================================================
# 1. ТЕКСТОВЫЕ КЛАВИАТУРЫ (REPLY)
# =====================================================================


def get_student_main_menu(
    is_solo: bool = False,
    is_coach: bool = False,
    has_active_workout: bool = False,
) -> ReplyKeyboardMarkup:
    """Главное меню ученика вне тренировочного процесса.
    Для соло-спортсмена вместо PIN — кнопка личной тренировки и привязка к тренеру.
    Для тренера в режиме спортсмена (is_coach=True) вместо «Привязаться к тренеру»
    показывается кнопка возврата в меню тренера.
    has_active_workout — у спортсмена есть незавершенная личная тренировка:
    кнопка называется «▶️ Продолжить личную тренировку»."""
    if is_solo:
        bottom_row = [
            KeyboardButton(text="🔗 Привязаться к тренеру")
            if not is_coach
            else KeyboardButton(text="👨‍🏫 Меню тренера")
        ]
        workout_btn_text = (
            "▶️ Продолжить личную тренировку"
            if has_active_workout
            else "▶️ Начать личную тренировку"
        )
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="👤 Мой персонаж")],
                [KeyboardButton(text=workout_btn_text)],
                [
                    KeyboardButton(text="📚 База трюков"),
                    KeyboardButton(text="📈 Рейтинги"),
                ],
                bottom_row,
            ],
            resize_keyboard=True,
        )
    if is_coach:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="👤 Мой персонаж")],
                [KeyboardButton(text="🔑 Подключиться к тренировке")],
                [
                    KeyboardButton(text="📚 База трюков"),
                    KeyboardButton(text="📈 Рейтинги"),
                ],
                [KeyboardButton(text="👨‍🏫 Меню тренера")],
            ],
            resize_keyboard=True,
        )
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👤 Мой персонаж")],
            [KeyboardButton(text="🔑 Подключиться к тренировке")],
            [KeyboardButton(text="📚 База трюков"), KeyboardButton(text="📈 Рейтинги")],
        ],
        resize_keyboard=True,
    )


# Обратная совместимость: старое имя функции
get_solo_main_menu = get_student_main_menu


def get_student_training_menu(is_solo: bool = False) -> ReplyKeyboardMarkup:
    """Меню ученика во время активной тренировки на споте.
    Тренировку обычного ученика завершает тренер, поэтому кнопка
    «🏁 Завершить тренировку» есть только у соло-спортсмена."""
    keyboard = [
        [KeyboardButton(text="🛼 Отправить выполненный трюк")],
        [
            KeyboardButton(text="🧩 Отправить связку"),
            KeyboardButton(text="↩️ Отменить последнее"),
        ],
        [KeyboardButton(text="📊 Мои очки за сегодня")],
        [
            *(  # финишь — только соло (личную тренировку завершает сам спортсмен)
                [KeyboardButton(text="🏁 Завершить тренировку")] if is_solo else []
            ),
            KeyboardButton(text="⬅️ Назад"),
        ],
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


# Клавиатура выбора дорожки слалома
def get_distance_inline_kb() -> types.InlineKeyboardMarkup:
    buttons = [
        [
            types.InlineKeyboardButton(text="📏 50 см", callback_data="dist:50"),
            types.InlineKeyboardButton(text="📏 80 см", callback_data="dist:80"),
            types.InlineKeyboardButton(text="📏 120 см", callback_data="dist:120"),
        ]
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=buttons)


# =====================================================================
# 2. ИНТЕРАКТИВНЫЕ КЛАВИАТУРЫ (INLINE)
# =====================================================================


def get_categories_inline_kb(categories: list) -> InlineKeyboardMarkup:
    """
    Генерирует список инлайн-кнопок категорий трюков.
    categories: список уникальных строк-категорий из БД (Trick.category)
    """
    inline_keyboard = []
    for cat in categories:
        if cat:  # Исключаем пустые значения, если они есть в БД
            inline_keyboard.append(
                [InlineKeyboardButton(text=str(cat), callback_data=f"st_cat:{cat}")]
            )
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)


def get_tricks_inline_kb(tricks: list, category_name: str) -> InlineKeyboardMarkup:
    """
    Генерирует список трюков, отфильтрованных по выбранной категории.
    tricks: список объектов модели Trick
    """
    inline_keyboard = []
    for t in tricks:
        inline_keyboard.append(
            [InlineKeyboardButton(text=f"{t.name}", callback_data=f"st_trick:{t.id}")]
        )

    # Кнопка возврата на шаг назад к категориям
    inline_keyboard.append(
        [
            InlineKeyboardButton(
                text="⬅️ Назад к категориям", callback_data="st_back_to_cats"
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)


def get_distance_inline_kb() -> InlineKeyboardMarkup:
    """Клавиатура выбора размера слаломной дорожки (расстояния между конусами)"""
    inline_keyboard = [
        [
            InlineKeyboardButton(text="📏 50 см", callback_data="dist:50"),
            InlineKeyboardButton(text="📏 80 см", callback_data="dist:80"),
            InlineKeyboardButton(text="📏 120 см", callback_data="dist:120"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)
