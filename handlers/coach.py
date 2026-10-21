import logging
from datetime import datetime, date
from aiogram import Router, F
from aiogram.types import KeyboardButton, Message, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from models import User, Workout, WorkoutRegistration, TrickLog, Trick
from states.coach_states import CoachRegistrationStates, WorkoutCreationStates
from keyboards.coach_kb import get_coach_main_menu, get_cancel_keyboard
from keyboards import get_skater_main_menu
from calculator import check_workout_diversity_penalty

logger = logging.getLogger(__name__)
router = Router()

# ==========================================
# 0. СИСТЕМНЫЕ КОМАНДЫ (ОТМЕНА)
# ==========================================

@router.message(F.text == "❌ Отмена")
async def cmd_cancel(message: Message, state: FSMContext):
    """Универсальная отмена для выхода из любого стейта тренера"""
    await state.clear()
    await message.answer(
        "❌ Действие отменено. Возвращаю вас в главное меню.",
        reply_markup=get_coach_main_menu()
    )

# ==========================================
# 1. РУЧНОЕ ДОБАВЛЕНИЕ УЧЕНИКА ТРЕНЕРОМ
# ==========================================

@router.message(F.text == "👤 Добавить ученика")
async def coach_add_student_start(message: Message, state: FSMContext):
    """Начало квеста создания профиля нового ученика руками тренера"""
    await message.answer(
        "📝 Вы инициировали ручное добавление ученика.\n"
        "Пожалуйста, введите его <b>Фамилию и Имя</b> (например: Семенов Игорь):",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(CoachRegistrationStates.waiting_for_name)

@router.message(CoachRegistrationStates.waiting_for_name)
async def coach_add_student_name(message: Message, state: FSMContext):
    """Принимаем имя создаваемого ученика"""
    full_name = message.text.strip()
    if len(full_name) < 3 or " " not in full_name:
        await message.answer("⚠️ Введите корректно Фамилию и Имя через пробел (например: Петров Петр):")
        return

    await state.update_data(full_name=full_name)
    await message.answer(
        f"Имя принято: <b>{full_name}</b>.\n"
        "Теперь введите его <b>дату рождения</b> в формате <code>ДД.ММ.ГГГГ</code> (например: 15.08.2012):"
    )
    await state.set_state(CoachRegistrationStates.waiting_for_birth_date)

@router.message(CoachRegistrationStates.waiting_for_birth_date)
async def coach_add_student_finish(message: Message, state: FSMContext, session: AsyncSession):
    """Валидируем дату и окончательно сохраняем нового ученика без привязки к TG ID"""
    date_text = message.text.strip()
    try:
        birth_date = datetime.strptime(date_text, "%d.%m.%Y").date()
    except ValueError:
        await message.answer("⚠️ Неверный формат даты. Используйте <code>ДД.ММ.ГГГГ</code>:")
        return

    data = await state.get_data()
    full_name = data.get("full_name")

    # Создаем ученика. telegram_id=None означает, что профиль локальный (создан тренером)
    new_student = User(
        telegram_id=None,
        full_name=full_name,
        birth_date=birth_date,
        role="student",
        is_approved=True  # Раз добавил тренер, значит профиль подтвержден
    )
    session.add(new_student)
    await state.clear()

    await message.answer(
        f"🎉 Профиль ученика <b>{full_name}</b> успешно создан!\n"
        f"Он сможет заниматься, когда вы добавите его в открытую тренировку.",
        reply_markup=get_coach_main_menu()
    )

# ==========================================
# 2. УПРАВЛЕНИЕ ТРЕНИРОВКАМИ (ОТКРЫТИЕ СЕССИИ)
# ==========================================

@router.message(F.text == "🏋️ Начать тренировку")
async def coach_start_workout(message: Message, session: AsyncSession):
    """Создает новую активную тренировочную сессию"""
    # Проверяем, нет ли уже запущенной тренировки
    query = select(Workout).where(Workout.status == "active")
    result = await session.execute(query)
    active_workout = result.scalar_one_or_none()

    if active_workout:
        await message.answer(
            "⚠️ У вас уже есть активная тренировка! "
            "Сначала закройте текущую сессию перед созданием новой.",
            reply_markup=get_coach_main_menu()
        )
        return

    # Создаем новую тренировку на текущую дату и время
    new_workout = Workout(
        date=datetime.now(),
        status="active"
    )
    session.add(new_workout)
    
    await message.answer(
        "🚀 <b>Тренировочная сессия успешно открыта!</b>\n"
        "Теперь ученики могут подключаться к ней со своих телефонов, "
        "вносить трюки и зарабатывать опыт.",
        reply_markup=get_coach_main_menu()
    )

# ==========================================
# 3. ПРОВЕРКА И ЗАКРЫТИЕ ТРЕНИРОВКИ
# ==========================================

@router.message(F.text == "🔔 Проверить тренировки учеников")
async def coach_review_and_close_workout(message: Message, session: AsyncSession):
    """Показывает статус текущей активной тренировки и закрывает её с расчетом баллов"""
    # Находим активную сессию
    query = select(Workout).where(Workout.status == "active")
    result = await session.execute(query)
    workout = result.scalar_one_or_none()

    if not workout:
        await message.answer("❌ В данный момент нет активных тренировок.")
        return

    # Запрашиваем всех записавшихся на эту тренировку учеников
    reg_query = select(WorkoutRegistration, User).join(
        User, WorkoutRegistration.student_id == User.id
    ).where(WorkoutRegistration.workout_id == workout.id)
    
    reg_result = await session.execute(reg_query)
    registrations = reg_result.all()

    if not registrations:
        # Если никто не пришел, тренер может просто закрыть её без штрафов
        workout.status = "closed"
        await message.answer(
            "📭 На тренировку никто не записался. Сессия автоматически закрыта.",
            reply_markup=get_coach_main_menu()
        )
        return

    report_text = f"📋 <b>Итоги тренировочной сессии от {workout.date.strftime('%d.%m.%Y')}:</b>\n\n"

    # Обрабатываем результаты каждого ученика
    for reg, student in registrations:
        # Считаем уникальные категории трюков, которые ученик сделал за сегодня
        log_query = select(Trick.category).join(
            TrickLog, TrickLog.trick_id == Trick.id
        ).where(
            TrickLog.workout_id == workout.id, 
            TrickLog.student_id == student.id
        ).distinct()
        
        log_result = await session.execute(log_query)
        categories_used = len(log_result.scalars().all())

        # Применяем проверку разнообразия к набранным тех. баллам
        # В вашей логике: penalty режет опыт/очки, если категорий меньше нормы уровня
        final_points = check_workout_diversity_penalty(
            skater_level=student.level,
            categories_used=categories_used,
            base_xp=int(reg.training_points) # передаем набранные тех. баллы
        )
        
        # Обновляем финальные тех. очки в записи регистрации
        reg.training_points = final_points

        report_text += (
            f"👤 Роллер: <b>{student.full_name}</b>\n"
            f"   Level: {student.level} | Задействовано категорий: {categories_used}\n"
            f"   Набрано тех. баллов: <b>{final_points}</b>\n"
            f"   Получено опыта за сессию: +<b>{reg.training_xp} XP</b>\n\n"
        )

    # Меняем статус тренировки на закрытый
    workout.status = "closed"
    
    await message.answer(
        report_text + "🔒 <b>Тренировка успешно проверена, все баллы зафиксированы и закрыты!</b>",
        reply_markup=get_coach_main_menu()
    )

@router.message(F.text == "📚 Справочник трюков")
async def coach_tricks_directory(message: Message, session: AsyncSession):
    """Вспомогательный хэндлер: показывает тренеру список трюков в базе для тестов"""
    query = select(Trick).limit(10)
    result = await session.execute(query)
    tricks = result.scalars().all()
    
    if not tricks:
        await message.answer("📭 Справочник трюков пуст. Запустите seed_db.py.")
        return
        
    text = "📚 <b>Доступные трюки для тестирования:</b>\n\n"
    for t in tricks:
        text += f"🔹 {t.name} (Кат: {t.category}, Базовые очки: {t.base_tech_points})\n"
        
    await message.answer(text)

# ==========================================
# 4. ЗАПОЛНЕНИЕ ТРЕНИРОВКИ ТРЕНЕРОМ ЗА УЧЕНИКА
# ==========================================

@router.message(F.text == "🏋️ Начать тренировку") # Изменим этот хэндлер, чтобы он давал выбор
async def coach_start_workout(message: Message, session: AsyncSession):
    """Открывает сессию или, если она уже открыта, предлагает вносить данные"""
    query = select(Workout).where(Workout.status == "active")
    result = await session.execute(query)
    active_workout = result.scalar_one_or_none()

    if not active_workout:
        # Если тренировки нет — создаем
        active_workout = Workout(date=datetime.now(), status="active")
        session.add(active_workout)
        await session.commit()
        await message.answer("🚀 Тренировочная сессия успешно открыта!")

    # Выводим дополнительное меню для тренера (можно просто текстом с инструкцией)
    await message.answer(
        "Вы можете вносить трюки за учеников. \n"
        "Чтобы записать трюк, используйте команду: /add_trick_for_student"
    )

@router.message(Command("add_trick_for_student"))
async def coach_trick_for_student_start(message: Message, state: FSMContext, session: AsyncSession):
    """Начало квеста: Тренер выбирает, какому ученику записать трюк"""
    # Проверяем, открыта ли тренировка
    query = select(Workout).where(Workout.status == "active")
    result = await session.execute(query)
    active_workout = result.scalar_one_or_none()

    if not active_workout:
        await message.answer("❌ Сначала откройте тренировку нажав '🏋️ Начать тренировку'")
        return

    # Вытаскиваем всех учеников из базы, чтобы тренер выбрал одного
    students_query = select(User).where(User.role == "student")
    students_res = await session.execute(students_query)
    students = students_res.scalars().all()

    if not students:
        await message.answer("📭 В школе пока нет учеников. Сначала добавьте их!")
        return

    # Строим клавиатуру со списком учеников
    builder = ReplyKeyboardBuilder()
    for student in students:
        builder.add(KeyboardButton(text=student.full_name))
    builder.adjust(2)
    builder.row(KeyboardButton(text="❌ Отмена"))

    await state.update_data(workout_id=active_workout.id)
    await message.answer("👥 Выберите ученика из списка:", reply_markup=builder.as_markup(resize_keyboard=True))
    await state.set_state(WorkoutCreationStates.waiting_for_skater)

@router.message(WorkoutCreationStates.waiting_for_skater)
async def coach_trick_for_student_skater(message: Message, state: FSMContext, session: AsyncSession):
    """Принимаем выбор ученика и выводим список трюков"""
    skater_name = message.text.strip()
    
    # Ищем ученика по имени
    query = select(User).where(User.full_name == skater_name, User.role == "student")
    res = await session.execute(query)
    student = res.scalar_one_or_none()

    if not student:
        await message.answer("⚠️ Выберите ученика с помощью кнопок на клавиатуре!")
        return

    # Автоматически регистрируем ученика на тренировку, если он еще не записан
    data = await state.get_data()
    workout_id = data.get("workout_id")
    
    reg_query = select(WorkoutRegistration).where(
        WorkoutRegistration.workout_id == workout_id,
        WorkoutRegistration.student_id == student.id
    )
    reg_res = await session.execute(reg_query)
    reg = reg_res.scalar_one_or_none()
    
    if not reg:
        reg = WorkoutRegistration(workout_id=workout_id, student_id=student.id)
        session.add(reg)

    await state.update_data(student_db_id=student.id, student_level=student.level)

    # Выводим трюки
    tricks_query = select(Trick)
    tricks_res = await session.execute(tricks_query)
    tricks = tricks_res.scalars().all()

    builder = ReplyKeyboardBuilder()
    for t in tricks:
        builder.add(KeyboardButton(text=t.name))
    builder.adjust(2)
    builder.row(KeyboardButton(text="❌ Отмена"))

    await message.answer(f"📚 Какой трюк выполнил <b>{student.full_name}</b>?", reply_markup=builder.as_markup(resize_keyboard=True))
    await state.set_state(WorkoutCreationStates.waiting_for_trick)

@router.message(WorkoutCreationStates.waiting_for_trick)
async def coach_trick_for_student_trick(message: Message, state: FSMContext, session: AsyncSession):
    """Принимаем трюк и спрашиваем количество"""
    trick_name = message.text.strip()
    
    query = select(Trick).where(Trick.name == trick_name)
    res = await session.execute(query)
    trick = res.scalar_one_or_none()

    if not trick:
        await message.answer("⚠️ Выберите трюк из списка!")
        return

    await state.update_data(trick_id=trick.id, trick_measurement=trick.measurement_type)
    
    prompt = "Укажите количество конусов:" if trick.measurement_type == "cones" else "Укажите количество повторений:"
    await message.answer(prompt, reply_markup=get_cancel_keyboard())
    await state.set_state(WorkoutCreationStates.waiting_for_quantity)

@router.message(WorkoutCreationStates.waiting_for_quantity)
async def coach_trick_for_student_qty(message: Message, state: FSMContext):
    """Принимаем количество и, если нужно, просим шаг дорожки"""
    try:
        quantity = int(message.text.strip())
        if quantity <= 0: raise ValueError
    except ValueError:
        await message.answer("⚠️ Введите целое положительное число:")
        return

    data = await state.get_data()
    await state.update_data(quantity=quantity)

    if data.get("trick_measurement") == "reps":
        # Если повторения — дорожка не нужна, завершаем расчет прямо тут
        await state.update_data(track_coeff="coeff_80") # дефолт коэффициент 1.0
        await save_trick_by_coach(message, state)
    else:
        # Если конусы — просим выбрать дорожку
        builder = ReplyKeyboardBuilder()
        builder.row(KeyboardButton(text="50 см"), KeyboardButton(text="80 см"), KeyboardButton(text="120 см"))
        builder.row(KeyboardButton(text="❌ Отмена"))
        await message.answer("📐 Выберите шаг дорожки:", reply_markup=builder.as_markup(resize_keyboard=True))
        await state.set_state(WorkoutCreationStates.waiting_for_track_coeff)

@router.message(WorkoutCreationStates.waiting_for_track_coeff)
async def coach_trick_for_student_coeff(message: Message, state: FSMContext):
    """Принимаем шаг дорожки и финишируем"""
    choice = message.text.strip()
    if choice == "50 см": field = "coeff_50"
    elif choice == "80 см": field = "coeff_80"
    elif choice == "120 см": field = "coeff_120"
    else:
        await message.answer("⚠️ Выберите вариант на клавиатуре!")
        return

    await state.update_data(selected_coeff_field=field)
    await save_trick_by_coach(message, state)

# Функция сохранения лога тренером (внутри handlers/coach.py)
async def save_trick_by_coach(message: Message, state: FSMContext):
    from database import async_session
    from calculator import calculate_trick_xp_and_points
    from models import XpPenalty, LevelExperience
    
    data = await state.get_data()
    workout_id = data.get("workout_id")
    student_db_id = data.get("student_db_id")
    trick_id = data.get("trick_id")
    quantity = data.get("quantity")
    coeff_field = data.get("selected_coeff_field", "coeff_80")

    async with async_session() as session:
        trick = await session.get(Trick, trick_id)
        student = await session.get(User, student_db_id)
        
        coeff = getattr(trick, coeff_field, 1.0)
        raw_xp, calculated_points = calculate_trick_xp_and_points(trick, quantity, coeff)
        
        # Штраф за разницу уровней
        lvl_diff = max(0, student.level - trick.trick_tier)
        p_res = await session.execute(select(XpPenalty).where(XpPenalty.level_difference == lvl_diff))
        penalty = p_res.scalar_one_or_none()
        xp_multiplier = penalty.xp_multiplier if penalty else 1.0
        final_xp = int(raw_xp * xp_multiplier)

        # Начисляем опыт ученику на лету
        student.xp += final_xp
        
        # Проверка Level Up
        level_up = False
        while True:
            nl_res = await session.execute(select(LevelExperience).where(LevelExperience.level == student.level + 1))
            next_lvl = nl_res.scalar_one_or_none()
            if next_lvl and student.xp >= next_lvl.required_xp:
                student.level += 1
                level_up = True
            else:
                break

        # Пишем в логи трюков
        new_log = TrickLog(workout_id=workout_id, student_id=student_db_id, trick_id=trick_id)
        session.add(new_log)

        # Обновляем регистрацию тренировки
        reg_res = await session.execute(select(WorkoutRegistration).where(
            WorkoutRegistration.workout_id == workout_id, WorkoutRegistration.student_id == student_db_id
        ))
        reg = reg_res.scalar_one()
        reg.training_xp += final_xp
        reg.training_points += calculated_points
        
        await session.commit()

    await state.clear()
    res_msg = f"✅ Трюк успешно записан ученику <b>{student.full_name}</b>!\nПолучено: +{final_xp} XP."
    if level_up:
        res_msg += f"\n🎉 Ученик поднял уровень! Новый левел: <b>{student.level}</b>!"
        
    await message.answer(res_msg, reply_markup=get_coach_main_menu())