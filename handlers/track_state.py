"""Общая логика «текущей дорожки» (cone_line) для тренера и ученика.

Дорожка по умолчанию — 80 см. Пользователь может сменить её прямо на экране
выбора категорий; выбранное значение запоминается в FSM и действует до
следующей смены. Отдельный шаг выбора дорожки перед вводом конусов убран.
"""

from aiogram.types import InlineKeyboardButton

DEFAULT_CONE_LINE = "80"

_LINE_LABELS = {"50": "50 см", "80": "80 см", "120": "120 см"}


def cone_line_text(cone_line: str) -> str:
    """Строка-подсказка для экрана: «Дорожка: 80 см (по умолчанию)»."""
    suffix = " (по умолчанию)" if cone_line == DEFAULT_CONE_LINE else ""
    return f"📏 Дорожка: {_LINE_LABELS.get(cone_line, cone_line)} {suffix}"


def build_cone_row(cone_line: str, prefix: str) -> list[tuple[str, str]]:
    """Ряд кнопок дорожки с отметкой активной. prefix — шаблон с '{line}'."""
    return [
        (
            f"✅ {label}" if line == cone_line else f"📏 {label}",
            prefix.format(line=line),
        )
        for line, label in _LINE_LABELS.items()
    ]


def track_row_markup(cone_line: str, prefix: str) -> list:
    """Готовый ряд InlineKeyboardButton для вставки в существующую клавиатуру."""
    from aiogram.types import InlineKeyboardButton

    return [
        InlineKeyboardButton(text=text, callback_data=cb)
        for text, cb in build_cone_row(cone_line, prefix)
    ]
