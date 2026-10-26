from aiogram.fsm.state import StatesGroup, State

class StudentWorkoutStates(StatesGroup):
    waiting_for_pin = State()      # Ожидание ввода ПИН-кода
    training_active = State()      # Процесс тренировки (выбор трюков)

class CoachWorkoutStates(StatesGroup):
    training_active = State()      # Тренер управляет процессом