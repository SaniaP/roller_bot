from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import types


# =====================================================================
# 1. ТЕКСТОВЫЕ КЛАВИАТУРЫ (REPLY)
# =====================================================================

def get_student_main_menu() -> ReplyKeyboardMarkup:
    """Главное меню ученика вне тренировочного процесса"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👤 Мой профиль (RPG)")],
            [KeyboardButton(text="🔑 Подключиться к тренировке")],
            [KeyboardButton(text="📚 База трюков")]
        ],
        resize_keyboard=True
    )

def get_student_training_menu() -> ReplyKeyboardMarkup:
    """Меню ученика во время активной тренировки на споте"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛹 Отправить выполненный трюк")],
            [KeyboardButton(text="📊 Мои очки за сегодня")],
            [KeyboardButton(text="🚪 Покинуть тренировку")]
        ],
        resize_keyboard=True
    )

# Клавиатура выбора дорожки слалома
def get_distance_inline_kb() -> types.InlineKeyboardMarkup:
    buttons = [
        [
            types.InlineKeyboardButton(text="📏 50 см", callback_data="dist:50"),
            types.InlineKeyboardButton(text="📏 80 см", callback_data="dist:80"),
            types.InlineKeyboardButton(text="📏 120 см", callback_data="dist:120")
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
            inline_keyboard.append([
                InlineKeyboardButton(text=str(cat), callback_data=f"st_cat:{cat}")
            ])
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)


def get_tricks_inline_kb(tricks: list, category_name: str) -> InlineKeyboardMarkup:
    """
    Генерирует список трюков, отфильтрованных по выбранной категории.
    tricks: список объектов модели Trick
    """
    inline_keyboard = []
    for t in tricks:
        inline_keyboard.append([
            InlineKeyboardButton(text=f"{t.name}", callback_data=f"st_trick:{t.id}")
        ])
    
    # Кнопка возврата на шаг назад к категориям
    inline_keyboard.append([
        InlineKeyboardButton(text="⬅️ Назад к категориям", callback_data="st_back_to_cats")
    ])
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)


def get_distance_inline_kb() -> InlineKeyboardMarkup:
    """Клавиатура выбора размера слаломной дорожки (расстояния между конусами)"""
    inline_keyboard = [
        [
            InlineKeyboardButton(text="📏 50 см", callback_data="dist:50"),
            InlineKeyboardButton(text="📏 80 см", callback_data="dist:80"),
            InlineKeyboardButton(text="📏 120 см", callback_data="dist:120")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)