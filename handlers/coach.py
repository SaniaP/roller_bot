import logging
import re
import random
from datetime import datetime
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from models import User, Workout, WorkoutRegistration, TrickLog, Trick
from states.coach_states import CoachRegistrationStates, WorkoutCreationStates
from states.workout_states import CoachWorkoutStates
from keyboards.coach_kb import (
    get_coach_main_menu, get_coach_main_menu_sync, get_cancel_keyboard,
    get_active_workout_kb, get_skater_workout_card_kb,
    get_categories_kb, get_tricks_by_cat_kb, get_track_coeff_kb,
    WorkoutAction, TrickAction, HandbookAction
)
from calculator import calculate_single_trick, calculate_current_workout_totals, check_level_up


async def get_coach_menu_for(session: AsyncSession):
    """Возвращает главное меню тренера с учётом наличия активной тренировки"""
    active_w = (await session.execute(
        select(Workout).where(Workout.status == "active")
    )).scalar_one_or_none()
    return get_coach_main_menu_sync(has_active_workout=active_w is not None)

logger = logging.getLogger(__name__)
router = Router()

# ==========================================
# 0. СИСТЕМНЫЕ КОМАНДЫ И ОБРАБОТКА ОБРАТНОЙ СОВМЕСТИМОСТИ
# ==========================================

@router.message(F.text == "❌ Отмена")
async def cmd_cancel(message: Message, state: FSMContext, session: AsyncSession):
    await state.clear()
    await message.answer("❌ Действие отменено. Возвращаю вас в главное меню.", reply_markup=await get_coach_menu_for(session))

# Перехватываем старый роутинг из меню выбора ролей (если он прилетает строкой)
@router.callback_query(F.data == "select_role_coach")
async def process_select_role_coach(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await callback.message.edit_text(
        text="🔑 Вы вошли в панель тренера. Используйте нижнее меню бота для управления.",
        reply_markup=None
    )
    await callback.message.answer("Главное меню тренера загружено:", reply_markup=await get_coach_menu_for(session))

# ==========================================
# 1. РУЧНОЕ ДОБАВЛЕНИЕ УЧЕНИКА ТРЕНЕРОМ (ОСТАВЛЯЕМ КАК ЕСТЬ)
# ==========================================
@router.message(F.text == "👤 Добавить ученика")
async def coach_add_student_start(message: Message, state: FSMContext):
    await message.answer(
        "📝 Вы инициировали ручное добавление ученика.\nВведите его <b>Фамилию и Имя</b>:",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(CoachRegistrationStates.waiting_for_name)

@router.message(CoachRegistrationStates.waiting_for_name)
async def coach_add_student_name(message: Message, state: FSMContext):
    full_name = message.text.strip()
    if len(full_name) < 3 or " " not in full_name:
        await message.answer("⚠️ Введите корректно Фамилию и Имя через пробел:")
        return
    await state.update_data(full_name=full_name)
    await message.answer(f"Имя принято: <b>{full_name}</b>.\nТеперь введите его <b>дату рождения</b> (ДД.ММ.ГГГГ):")
    await state.set_state(CoachRegistrationStates.waiting_for_birth_date)

@router.message(CoachRegistrationStates.waiting_for_birth_date)
async def coach_add_student_finish(message: Message, state: FSMContext, session: AsyncSession):
    date_text = message.text.strip()
    try:
        birth_date = datetime.strptime(date_text, "%d.%m.%Y").date()
    except ValueError:
        await message.answer("⚠️ Неверный формат даты. Используйте ДД.ММ.ГГГГ:")
        return

    data = await state.get_data()
    new_student = User(
        telegram_id=None,
        full_name=data.get("full_name"),
        birth_date=birth_date,
        role="student",
        is_approved=True
    )
    session.add(new_student)
    await session.commit()
    await state.clear()
    await message.answer(f"🎉 Профиль ученика успешно создан!", reply_markup=await get_coach_menu_for(session))

# ==========================================
# 2. НОВАЯ ЛОГИКА ТРЕНИРОВКИ (ОБНОВЛЕННЫЙ ПУНКТ 1.1)
# ==========================================

def generate_workout_pin() -> str:
    """Генерация уникального 4-значного PIN-кода"""
    return str(random.randint(1000, 9999))


@router.message(F.text.in_({"🏋️ Начать тренировку", "🏋️ Продолжить тренировку"}))
async def coach_start_workout(message: Message, state: FSMContext, session: AsyncSession):
    """Открытие/продолжение тренировки. Если активная есть — открываем её меню"""
    query = select(Workout).where(Workout.status == "active")
    active_workout = (await session.execute(query)).scalar_one_or_none()

    if active_workout:
        # Продолжаем: собираем текущий список спортсменов на занятии
        reg_stmt = select(WorkoutRegistration).where(WorkoutRegistration.workout_id == active_workout.id)
        regs = (await session.execute(reg_stmt)).scalars().all()

        current_students = []
        for r in regs:
            st = await session.get(User, r.student_id)
            if st: current_students.append(st)

        # Выводим сообщение о продолжении тренировки и показываем сохраненный ранее пин-код
        await message.answer(
            f"🏋️‍♂️ **ИДЕТ ТРЕНИРОВКА**\n\n"
            f"PIN-код для подключения учеников: `{active_workout.pin_code}`\n\n"
            f"Список спортсменов на занятии:",
            reply_markup=get_active_workout_kb(active_workout.id, current_students),
            parse_mode="Markdown"
        )
        return

    # --- ЕСЛИ ТРЕНИРОВКИ НЕТ, СОЗДАЕМ НОВУЮ С PIN-КОДОМ ---
    pin = generate_workout_pin()
    
    # Создаем тренировку в БД со статусом active и сгенерированным pin_code
    new_workout = Workout(
        date=datetime.now(), 
        status="active",
        pin_code=pin
    )
    session.add(new_workout)
    await session.commit()
    
    # Переводим тренера в состояние активности тренировки
    await state.set_state(CoachWorkoutStates.training_active)
    
    await message.answer(
        f"🏋️‍♂️ **Тренировка открыта!**\n\n"
        f"Сообщите ученикам этот PIN-код для подключения:\n"
        f"👉 `{pin}` 👈 (нажмите, чтобы скопировать)\n\n"
        f"Ученики будут появляться в вашем списке автоматически по мере ввода кода.",
        reply_markup=get_active_workout_kb(new_workout.id, []),
        parse_mode="Markdown"
    )

# ==========================================
# 2. СПРАВОЧНИК ТРЮКОВ (ТОЛЬКО ПРОСМОТР)
# ==========================================

@router.message(F.text == "📚 Справочник трюков")
async def show_tricks_handbook(message: Message, session: AsyncSession):
    """Просмотр справочника трюков: выбор категории"""
    cats_stmt = select(Trick.category).distinct()
    categories = (await session.execute(cats_stmt)).scalars().all()

    if not categories:
        await message.answer("🤖 Справочник трюков пока пуст. Наполните базу данных.")
        return

    builder = InlineKeyboardBuilder()
    for cat in categories:
        builder.button(
            text=str(cat).capitalize(),
            callback_data=HandbookAction(action="hb_cat", category=str(cat))
        )
    builder.adjust(2)
    await message.answer("📚 Справочник трюков. Выберите категорию:", reply_markup=builder.as_markup())


@router.callback_query(HandbookAction.filter(F.action == "hb_cat"))
async def show_handbook_tricks(callback: CallbackQuery, callback_data: HandbookAction, session: AsyncSession):
    """Список трюков выбранной категории с их параметрами"""
    tricks_stmt = select(Trick).where(Trick.category == callback_data.category)
    tricks = (await session.execute(tricks_stmt)).scalars().all()

    builder = InlineKeyboardBuilder()
    for trick in tricks:
        builder.button(
            text=f"{trick.name} (Tier {trick.trick_tier})",
            callback_data=HandbookAction(action="hb_trick", category=callback_data.category, trick_id=trick.id)
        )
    builder.button(text="⬅️ К категориям", callback_data=HandbookAction(action="hb_back"))
    builder.adjust(1)

    await callback.message.edit_text(
        f"📚 Категория: {callback_data.category.upper()}. Выберите трюк:",
        reply_markup=builder.as_markup()
    )


@router.callback_query(HandbookAction.filter(F.action == "hb_trick"))
async def show_handbook_trick_details(callback: CallbackQuery, callback_data: HandbookAction, session: AsyncSession):
    """Карточка трюка с коэффициентами и наградами"""
    trick = await session.get(Trick, callback_data.trick_id)
    if not trick:
        await callback.answer("Трюк не найден", show_alert=True)
        return

    text = (
        f"🎯 **{trick.name}**\n\n"
        f"📦 Категория: {trick.category}\n"
        f"🏅 Тир (сложность): {trick.trick_tier}\n"
        f"📐 База: {trick.base_quantity} конусов\n\n"
        f"📏 Коэфф. дорожки 50 см: {trick.coeff_50}\n"
        f"📏 Коэфф. дорожки 80 см: {trick.coeff_80}\n"
        f"📏 Коэфф. дорожки 120 см: {trick.coeff_120}\n\n"
        f"⭐ Базовые баллы: {trick.base_tech_points}\n"
        f"✨ Базовый опыт: {trick.base_xp} XP\n"
        f"➕ Бонус за конус свыше базы: +{int(trick.extra_bonus_pct * 100)}%"
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text="⬅️ К списку трюков",
        callback_data=HandbookAction(action="hb_cat", category=callback_data.category)
    )
    builder.adjust(1)

    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")


@router.callback_query(HandbookAction.filter(F.action == "hb_back"))
async def show_handbook_back_to_categories(callback: CallbackQuery, session: AsyncSession):
    cats_stmt = select(Trick.category).distinct()
    categories = (await session.execute(cats_stmt)).scalars().all()

    builder = InlineKeyboardBuilder()
    for cat in categories:
        builder.button(
            text=str(cat).capitalize(),
            callback_data=HandbookAction(action="hb_cat", category=str(cat))
        )
    builder.adjust(2)
    await callback.message.edit_text("📚 Справочник трюков. Выберите категорию:", reply_markup=builder.as_markup())


@router.callback_query(WorkoutAction.filter(F.action == "add_skater_to_w"))
async def add_skater_from_db_list(callback: CallbackQuery, callback_data: WorkoutAction, session: AsyncSession):
    """Вывод списка спортсменов из БД, которых еще нет в текущей сессии"""
    workout_id = callback_data.workout_id
    
    # Субквери: кого уже добавили
    sub_q = select(WorkoutRegistration.student_id).where(WorkoutRegistration.workout_id == workout_id)
    sub_res = await session.execute(sub_q)
    added_ids = sub_res.scalars().all()
    
    # Достаем остальных
    stmt = select(User).where(User.role == "student")
    if added_ids:
        stmt = stmt.where(User.id.not_in(added_ids))
        
    students = (await session.execute(stmt)).scalars().all()
    
    if not students:
        await callback.answer("Все доступные ученики из базы данных уже на тренировке!", show_alert=True)
        return
        
    builder = InlineKeyboardBuilder()
    for skater in students:
        builder.button(
            text=skater.full_name, 
            callback_data=WorkoutAction(action="select_skater", workout_id=workout_id, student_id=skater.id)
        )
    builder.button(text="⬅️ Назад", callback_data=WorkoutAction(action="select_skater", workout_id=workout_id))
    builder.adjust(1)
    
    await callback.message.edit_text("Выберите ученика для добавления в тренировку:", reply_markup=builder.as_markup())

@router.callback_query(WorkoutAction.filter(F.action == "select_skater"))
async def show_skater_card_or_main_list(callback: CallbackQuery, callback_data: WorkoutAction, session: AsyncSession):
    """Ярус карточки спортсмена: показывает текущий лвл, опыт и предварительный балл"""
    workout_id = callback_data.workout_id
    student_id = callback_data.student_id
    
    # Если student_id пустой — возвращаемся к главному списку тренировки
    if not student_id:
        reg_stmt = select(WorkoutRegistration).where(WorkoutRegistration.workout_id == workout_id)
        regs = (await session.execute(reg_stmt)).scalars().all()
        
        current_students = []
        for r in regs:
            st = await session.get(User, r.student_id)
            if st: current_students.append(st)
            
        await callback.message.edit_text(
            "🏋️‍♂️ **ИДЕТ ТРЕНИРОВКА**\n\nСписок спортсменов на занятии:",
            reply_markup=get_active_workout_kb(workout_id, current_students)
        )
        return

    # Если передан student_id — проверяем/создаем регистрацию на тренировку
    reg_stmt = select(WorkoutRegistration).where(
        WorkoutRegistration.workout_id == workout_id, 
        WorkoutRegistration.student_id == student_id
    )
    reg = (await session.execute(reg_stmt)).scalar_one_or_none()
    
    if not reg:
        reg = WorkoutRegistration(workout_id=workout_id, student_id=student_id)
        session.add(reg)
        await session.commit()

    student = await session.get(User, student_id)
    
    # Тянем все логи трюков этого ученика за текущую сессию
    logs_stmt = select(TrickLog).where(TrickLog.workout_id == workout_id, TrickLog.student_id == student_id)
    logs = (await session.execute(logs_stmt)).scalars().all()
    
    # Калькуляция результатов «на лету»
    current_sport_score, current_xp_gain = await calculate_current_workout_totals(list(logs), student, session)
    
    card_text = (
        f"👤 **Спортсмен:** {student.full_name}\n"
        f"🎮 **Ранг:** {student.title} (Уровень: {student.level})\n"
        f"✨ **Текущий опыт:** {student.xp} XP\n\n"
        f"📊 **Предварительные итоги сессии:**\n"
        f" — Средний спортивный балл: <b>{current_sport_score}</b>\n"
        f" — Набрано опыта за сегодня: <b>+{current_xp_gain} XP</b>"
    )
    
    await callback.message.edit_text(text=card_text, reply_markup=get_skater_workout_card_kb(workout_id, student_id, list(logs)))

# ==========================================
# 3. ПОШАГОВЫЙ ИНЛАЙН-ВВОД ТРЮКОВ ТРЕНЕРОМ
# ==========================================

@router.callback_query(TrickAction.filter(F.action == "choose_cat"))
async def process_choose_category(callback: CallbackQuery, callback_data: TrickAction, session: AsyncSession):
    cats_stmt = select(Trick.category).distinct()
    categories = (await session.execute(cats_stmt)).scalars().all()
    
    await callback.message.edit_text(
        "Выберите категорию трюка:",
        reply_markup=get_categories_kb(callback_data.workout_id, callback_data.student_id, list(categories))
    )

@router.callback_query(TrickAction.filter(F.action == "choose_trick"))
async def process_choose_trick(callback: CallbackQuery, callback_data: TrickAction, session: AsyncSession):
    tricks_stmt = select(Trick).where(Trick.category == callback_data.category)
    tricks = (await session.execute(tricks_stmt)).scalars().all()
    
    await callback.message.edit_text(
        f"Категория: {callback_data.category.upper()}. Выберите трюк:",
        reply_markup=get_tricks_by_cat_kb(callback_data.workout_id, callback_data.student_id, list(tricks))
    )

@router.callback_query(TrickAction.filter(F.action == "log_trick"))
async def start_fsm_trick_params(callback: CallbackQuery, callback_data: TrickAction, state: FSMContext):
    """Переходим в FSM, сохраняем контекст и запрашиваем дорожку"""
    await state.update_data(
        workout_id=callback_data.workout_id,student_id=callback_data.student_id,
trick_id=callback_data.trick_id
)

    await callback.message.edit_text("Выберите размер дорожки (конусов):", reply_markup=get_track_coeff_kb())
    await state.set_state(WorkoutCreationStates.waiting_for_track_coeff)

@router.callback_query(WorkoutCreationStates.waiting_for_track_coeff, F.data.startswith("cone_"))
async def process_fsm_track_coeff(callback: CallbackQuery, state: FSMContext):
    cone_size = callback.data.split("_")[1] # Извлекаем "50", "80" или "120"
    await state.update_data(cone_line=cone_size)
    
    await callback.message.delete()
    await callback.message.answer(
        "🔢 **Введите результаты выполнения**\n\n"
        "Отправьте количество конусов. Если нужно учесть чистоту/качество исполнения трюка, "
        "укажите через запятую поправку в процентах.\n\n"
        "**Примеры ввода:**\n"
        "• `9` [хорошее исполнение]\n"
        "• `8,-10` — штраф 10% за помарки и недачеты в технике\n"
        "• `11,20` — 20% бонус за идеальное исполнение трюка",
        reply_markup=get_cancel_keyboard(),
        parse_mode="Markdown"
    )
    await state.set_state(WorkoutCreationStates.waiting_for_quantity)


@router.message(WorkoutCreationStates.waiting_for_quantity)
async def process_fsm_quantity_and_save(message: Message, state: FSMContext, session: AsyncSession):
    text_input = message.text.strip().replace(" ", "") # Убираем пробелы
    
    # Регулярное выражение для поиска форматов: "8", "8,-10", "11,+20", "11,20"
    match = re.match(r"^(\d+)(?:,([+-]?\d+))?$", text_input)
    
    if not match:
        await message.answer(
            "⚠️ **Неверный формат ввода!**\n\n"
            "Пожалуйста, введите данные строго по шаблону:\n"
            "• Просто число конусов: `9` (без поправок)\n"
            "• Со штрафом: `8,-10` (минус 10% к качеству)\n"
            "• С бонусом: `11,20` (плюс 20% к качеству)",
            parse_mode="Markdown"
        )
        return
        
    # Извлекаем количество конусов
    quantity = int(match.group(1))
    
    # Извлекаем поправку в % (если она введена)
    percentage_mod = match.group(2)
    if percentage_mod:
        # Переводим проценты в множитель качества (например, -10 -> 0.9, +20 -> 1.2)
        execution_multiplier = 1.0 + (int(percentage_mod) / 100.0)
        # Защита от дурака: множитель качества выполнения не должен быть отрицательным
        if execution_multiplier < 0:
            execution_multiplier = 0.0
    else:
        execution_multiplier = 1.0

    data = await state.get_data()
    await state.clear() # Очищаем стейт FSM, возвращаемся в инлайн-меню
    
    workout_id = data["workout_id"]
    student_id = data["student_id"]
    trick_id = data["trick_id"]
    cone_line = data["cone_line"]
    
    student = await session.get(User, student_id)
    trick = await session.get(Trick, trick_id)
    
    # 💥 Вызываем твой calculator.py, передавая ТЕПЕРЬ ДИНАМИЧЕСКИЙ execution_multiplier!
    calc_points, calc_xp = await calculate_single_trick(
        trick=trick,
        cone_line=cone_line,
        quantity=quantity,
        execution_multiplier=execution_multiplier,
        skater_level=student.level,
        db_session=session
    )
    
    # Сохраняем в лог подходов с учетом введенной точности
    new_log = TrickLog(
        workout_id=workout_id,
        student_id=student_id,
        trick_id=trick_id,
        cone_line=cone_line,
        quantity=quantity,
        execution_multiplier=execution_multiplier, # Записываем точный коэфф
        calculated_points=calc_points,
        calculated_xp=calc_xp
    )
    session.add(new_log)
    await session.commit()
    
    # Генерируем кнопку возврата
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🔄 Вернуться в карточку спортсмена", 
        callback_data=WorkoutAction(action="select_skater", workout_id=workout_id, student_id=student_id)
    )
    
    # Формируем красивый отчет о добавлении
    accuracy_text = f"{percentage_mod}%" if percentage_mod else "без изменений"
    if percentage_mod and not percentage_mod.startswith(("-", "+")):
        accuracy_text = f"+{percentage_mod}%"

    await message.answer(
        f"✅ Трюк <b>{trick.name}</b> успешно добавлен!\n\n"
        f"📊 **Параметры подхода:**\n"
        f"• Количество конусов: <code>{quantity}</code>\n"
        f"• Точность выполнения: <code>{accuracy_text}</code> (коэфф. {execution_multiplier:.2f})\n\n"
        f"✨ **Начислено:** +{calc_points} баллов | +{calc_xp} XP",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(TrickAction.filter(F.action == "delete_log"))
async def delete_last_trick_log(
    callback: CallbackQuery,
    callback_data: TrickAction,
    session: AsyncSession,
):
    """Отмена ошибочно внесенного трюка"""
    stmt = delete(TrickLog).where(TrickLog.id == callback_data.log_id)
    await session.execute(stmt)
    await session.commit()

    await callback.answer("Последний трюк успешно отменен!", show_alert=True)

    # Перерисовываем карточку
    redirect_action = WorkoutAction(
        action="select_skater",
        workout_id=callback_data.workout_id,
        student_id=callback_data.student_id,
    )
    await show_skater_card_or_main_list(callback, redirect_action, session)


# =====================================================================
# 4. ФИНАЛИЗАЦИЯ ТРЕНИРОВКИ И LEVEL UP
# =====================================================================


@router.callback_query(WorkoutAction.filter(F.action == "finish_workout"))
async def process_finish_workout(
    callback: CallbackQuery,
    callback_data: WorkoutAction,
    session: AsyncSession,
):
    workout_id = callback_data.workout_id
    workout = await session.get(Workout, workout_id)

    if not workout:
        await callback.answer("Ошибка: Тренировка не найдена", show_alert=True)
        return

    # Извлекаем все регистрации на эту тренировку
    reg_stmt = select(WorkoutRegistration).where(
        WorkoutRegistration.workout_id == workout_id
    )
    registrations = (await session.execute(reg_stmt)).scalars().all()

    summary_report = "🏁 ТРЕНИРОВКА ЗАВЕРШЕНА И ПОСЧИТАНА!\n\n"

    for reg in registrations:
        student = await session.get(User, reg.student_id)

        logs_stmt = select(TrickLog).where(
            TrickLog.workout_id == workout_id,
            TrickLog.student_id == student.id,
        )
        logs = (await session.execute(logs_stmt)).scalars().all()

        # Считаем финальные итоги через калькулятор
        final_sport_score, final_xp_gain = await calculate_current_workout_totals(
            list(logs), student, session
        )

        # Обновляем регистрационную запись тренировки
        reg.training_points = final_sport_score
        reg.training_xp = final_xp_gain

        # Начисляем опыт спортсмену глобально
        student.xp += final_xp_gain

        # Проверяем Level Up
        new_level, level_upgraded = await check_level_up(
            student.level, student.xp, session
        )
        lvl_up_text = ""

        if level_upgraded:
            student.level = new_level
            lvl_up_text = f" 🔥 LEVEL UP! Новый уровень: {new_level}!"

        summary_report += (
            f"🏃‍♂️ {student.full_name}:\n"
            f"Score: {final_sport_score} | XP: +{final_xp_gain}{lvl_up_text}\n\n"
        )

    # Закрываем тренировку
    workout.status = "closed"
    await session.commit()

    await callback.message.delete()
    await callback.message.answer(
        summary_report, reply_markup=await get_coach_menu_for(session)
    )