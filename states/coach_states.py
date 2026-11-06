from aiogram.fsm.state import State, StatesGroup

class CoachRegistrationStates(StatesGroup):
    """Сценарий ручного добавления нового ученика тренером"""
    waiting_for_name = State()       # Ожидание ФИО спортсмена
    waiting_for_birth_date = State() # Ожидание даты рождения

class WorkoutCreationStates(StatesGroup):
    """Сценарий создания и заполнения тренировки тренером"""
    waiting_for_date = State()       # Ожидание даты проведения тренировки
    waiting_for_skater = State()     # Ожидание выбора спортсмена из списка
    waiting_for_trick = State()      # Ожидание выбора трюка
    waiting_for_quantity = State()   # Ожидание ввода количества (конусов или повторений)
    waiting_for_track_coeff = State()# Ожидание выбора дорожки (50см, 80см, 120см) [2.2]
    waiting_for_combo = State()      # Ожидание ответа: "Это элемент комбо-связки?" [2.2]

class ComboBuilderStates(StatesGroup):
    """Пошаговый FSM-конструктор связки (общий для тренера и ученика)"""
    choosing_first_trick = State()    # Выбор первого трюка (X)
    waiting_first_track = State()     # Дорожка первого трюка
    waiting_first_quantity = State()  # Конусы/качество первого трюка
    menu = State()                    # Меню связки: + добавить / ✅ сохранить / ❌ отмена
    choosing_next_trick = State()     # Выбор следующего трюка (Y)
    waiting_next_track = State()      # Дорожка следующего трюка
    waiting_next_quantity = State()   # Конусы/качество следующего трюка

class WorkoutVerificationStates(StatesGroup):
    """Сценарий проверки и корректировки тренировок, заполненных учениками"""
    waiting_for_workout_select = State() # Тренер выбирает тренировку из списка ожидания
    waiting_for_log_edit = State()       # Тренер выбирает конкретный трюк ученика для правки
    waiting_for_new_value = State()      # Тренер меняет количество или "стоимость" (базовый EXP/баллы) трюка
    waiting_for_multiplier = State()     # Тренер вводит поправку исполнения в % (-10 / 10) для подтверждения трюка