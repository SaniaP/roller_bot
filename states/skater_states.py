from aiogram.fsm.state import State, StatesGroup

class SkaterRegistrationStates(StatesGroup):
    """Сценарий первой самостоятельной регистрации спортсмена в боте"""
    waiting_for_name = State()       # Ожидание ФИО от спортсмена
    waiting_for_birth_date = State() # Ожидание даты рождения

class SkaterWorkoutStates(StatesGroup):
    """Сценарий самостоятельного внесения трюков спортсменом во время тренировки"""
    waiting_for_trick = State()      # Выбор трюка из справочника
    waiting_for_quantity = State()   # Ввод количества конусов/повторений
    waiting_for_track_coeff = State()# Выбор шага дорожки (50см, 80см, 120см) [2.2]
    waiting_for_combo = State()      # Отметка: "Это элемент комбо-связки?" [2.2]