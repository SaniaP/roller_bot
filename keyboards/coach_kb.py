from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.filters.callback_data import CallbackData
from typing import Optional
from models import User, Trick, TrickLog

# --- Фабрики Callback Data для безопасной передачи параметров ---
class WorkoutAction(CallbackData, prefix="wrk"):
    action: str        # "start_session", "add_skater_to_w", "select_skater", "finish_workout"
    workout_id: Optional[int] = None
    student_id: Optional[int] = None

class TrickAction(CallbackData, prefix="trck"):
    action: str        # "choose_cat", "choose_trick", "log_trick", "delete_log"
    workout_id: int
    student_id: int
    category: Optional[str] = None
    trick_id: Optional[int] = None
    log_id: Optional[int] = None

class HandbookAction(CallbackData, prefix="hb"):
    action: str        # "hb_cat", "hb_trick", "hb_back"
    category: Optional[str] = None
    trick_id: Optional[int] = None

class ReviewAction(CallbackData, prefix="rev"):
    action: str        # "rv_student" (карточка ученика), "rv_confirm_log", "rv_confirm_all_student", "rv_confirm_all", "rv_back"
    workout_id: int
    student_id: Optional[int] = None
    log_id: Optional[int] = None



def get_coach_main_menu_sync(has_active_workout: bool) -> ReplyKeyboardMarkup:
    """Главное меню тренера. Кнопка тренировки зависит от наличия активной тренировки в БД"""
    builder = ReplyKeyboardBuilder()
    workout_btn_text = "🏋️ Продолжить тренировку" if has_active_workout else "🏋️ Начать тренировку"
    builder.row(KeyboardButton(text=workout_btn_text))
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

# --- Новые Инлайн-клавиатуры для ведения тренировки тренером ---
def get_active_workout_kb(workout_id: int, current_students: list[User]) -> InlineKeyboardMarkup:
    """Экран: Идет тренировка. Позволяет добавлять учеников и переходить в их карточки"""
    builder = InlineKeyboardBuilder()
    
    # Кнопка добавления спортсмена в текущую сессию
    builder.button(
        text="➕ Добавить ученика в тренировку", 
        callback_data=WorkoutAction(action="add_skater_to_w", workout_id=workout_id)
    )
    
    # Кнопки для каждого добавленного спортсмена
    for student in current_students:
        builder.button(
            text=f"🏃‍♂️ {student.full_name}", 
            callback_data=WorkoutAction(action="select_skater", workout_id=workout_id, student_id=student.id)
        )
        
    builder.button(
        text="🏁 Завершить тренировку и посчитать итоги", 
        callback_data=WorkoutAction(action="finish_workout", workout_id=workout_id)
    )
    builder.adjust(1)
    return builder.as_markup()

def get_skater_workout_card_kb(workout_id: int, student_id: int, logs: list[TrickLog]) -> InlineKeyboardMarkup:
    """Последний ярус: Карточка управления трюками конкретного спортсмена"""
    builder = InlineKeyboardBuilder()
    
    builder.button(
        text="➕ Добавить трюк спортсмену", 
        callback_data=TrickAction(action="choose_cat", workout_id=workout_id, student_id=student_id)
    )
    
    # Если трюки уже внесены, выводим кнопку удаления последнего или редактирования
    if logs:
        builder.button(
            text="🗑 Отменить последний трюк", 
            callback_data=TrickAction(action="delete_log", workout_id=workout_id, student_id=student_id, log_id=logs[-1].id)
        )
        
    builder.button(
        text="⬅️ Назад к списку тренировки", 
        callback_data=WorkoutAction(action="select_skater", workout_id=workout_id)
    )
    builder.adjust(1)
    return builder.as_markup()

def get_categories_kb(workout_id: int, student_id: int, categories: list[str]) -> InlineKeyboardMarkup:
    """Выбор категории трюка из справочника"""
    builder = InlineKeyboardBuilder()
    for cat in categories:
        builder.button(
            text=str(cat).capitalize(), 
            callback_data=TrickAction(action="choose_trick", workout_id=workout_id, student_id=student_id, category=cat)
        )
    builder.button(
        text="❌ Отмена", 
        callback_data=WorkoutAction(action="select_skater", workout_id=workout_id, student_id=student_id)
    )
    builder.adjust(2)
    return builder.as_markup()

def get_tricks_by_cat_kb(workout_id: int, student_id: int, tricks: list[Trick]) -> InlineKeyboardMarkup:
    """Выбор конкретного трюка из выбранной категории"""
    builder = InlineKeyboardBuilder()
    for trick in tricks:
        builder.button(
            text=f"{trick.name} (Tier {trick.trick_tier})", 
            callback_data=TrickAction(action="log_trick", workout_id=workout_id, student_id=student_id, trick_id=trick.id)
        )
    builder.button(
        text="⬅️ К категориям", 
        callback_data=TrickAction(action="choose_cat", workout_id=workout_id, student_id=student_id)
    )
    builder.adjust(1)
    return builder.as_markup()

def get_track_coeff_kb() -> InlineKeyboardMarkup:
    """Выбор размера дорожки конусов"""
    builder = InlineKeyboardBuilder()
    builder.button(text="50 см", callback_data="cone_50")
    builder.button(text="80 см", callback_data="cone_80")
    builder.button(text="120 см", callback_data="cone_120")
    builder.adjust(3)
    return builder.as_markup()


def get_review_students_kb(workout_id: int, students: list[User]) -> InlineKeyboardMarkup:
    """Экран проверки: список самостоятельных спортсменов, ждущих подтверждения"""
    builder = InlineKeyboardBuilder()
    for st in students:
        builder.button(
            text=f"🧍 {st.full_name}",
            callback_data=ReviewAction(action="rv_student", workout_id=workout_id, student_id=st.id)
        )
    builder.button(
        text="✅ Подтвердить всех",
        callback_data=ReviewAction(action="rv_confirm_all", workout_id=workout_id)
    )
    builder.adjust(1)
    return builder.as_markup()


def get_review_student_card_kb(workout_id: int, student_id: int, logs: list[TrickLog], tricks: dict[int, "Trick"]) -> InlineKeyboardMarkup:
    """Карточка самостоятельного ученика: список его неподтвержденных трюков.
    Нажатие на трюк открывает ввод поправки исполнения (execution_multiplier)"""
    builder = InlineKeyboardBuilder()
    for log in logs:
        trick = tricks.get(log.trick_id)
        trick_name = trick.name if trick else "Трюк"
        builder.button(
            text=f"🎯 {trick_name} ({log.cone_line} см, {log.quantity} кон.)",
            callback_data=ReviewAction(action="rv_edit_log", workout_id=workout_id, student_id=student_id, log_id=log.id)
        )
    builder.button(
        text="✅ Подтвердить все трюки",
        callback_data=ReviewAction(action="rv_confirm_all_student", workout_id=workout_id, student_id=student_id)
    )
    builder.button(
        text="⬅️ К списку учеников",
        callback_data=ReviewAction(action="rv_back", workout_id=workout_id)
    )
    builder.adjust(1)
    return builder.as_markup()


def get_approve_user_kb(student_tg_id: int) -> InlineKeyboardMarkup:
    """Кнопки для тренера, чтобы одобрить или отклонить заявку ученика"""
    builder = InlineKeyboardBuilder()
    
    # В callback_data зашиваем ID студента, чтобы хэндлер знал, кого именно одобрять
    builder.button(text="✅ Одобрить", callback_data=f"approve_user:{student_tg_id}")
    builder.button(text="❌ Отклонить", callback_data=f"reject_user:{student_tg_id}")
    
    builder.adjust(2) # Располагаем кнопки в один ряд (две колонки)
    return builder.as_markup()