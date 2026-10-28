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
from states.coach_states import CoachRegistrationStates, WorkoutCreationStates, WorkoutVerificationStates
from states.workout_states import CoachWorkoutStates
from keyboards.coach_kb import (
    get_coach_main_menu_sync, get_cancel_keyboard,
    get_active_workout_kb, get_skater_workout_card_kb,
    get_categories_kb, get_tricks_by_cat_kb, get_track_coeff_kb,
    get_review_students_kb, get_review_student_card_kb,
    WorkoutAction, TrickAction, HandbookAction, ReviewAction
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
# 1. ЛОГИКА ТРЕНИРОВКИ 
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
        calculated_xp=calc_xp,
        needs_review=False  # Тренер вносит лично — подтверждение не требуется
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
# 5. ПРОВЕРКА САМОСТОЯТЕЛЬНЫХ ТРЮКОВ УЧЕНИКОВ (needs_review)
# =====================================================================

async def _get_pending_review(session: AsyncSession, workout_id: int):
    """Возвращает dict {student_id: [TrickLog]} — все неподтвержденные трюки активной тренировки"""
    stmt = select(TrickLog).where(
        TrickLog.workout_id == workout_id,
        TrickLog.needs_review == True  # noqa: E712
    )
    pending_logs = (await session.execute(stmt)).scalars().all()

    grouped = {}
    for log in pending_logs:
        grouped.setdefault(log.student_id, []).append(log)
    return grouped


async def _send_review_list(target: Message, session: AsyncSession, workout_id: int, edit: bool):
    """Рисует/отправляет экран списка самостоятельных учеников, ждущих подтверждения.
    edit=True — правит существующее сообщение (из callback), False — отправляет новое"""
    grouped = await _get_pending_review(session, workout_id)

    if not grouped:
        text = "✅ Все самостоятельные трюки подтверждены. Список пуст."
        kb = None
    else:
        students = []
        for sid in grouped:
            st = await session.get(User, sid)
            if st:
                students.append(st)
        text = (
            "🔔 <b>Проверка тренировок</b>\n\n"
            "Спортсмены, отправившие трюки самостоятельно и ждущие подтверждения:\n"
            "Нажмите на имя, чтобы проверить трюки по одному, либо подтвердите всех сразу."
        )
        kb = get_review_students_kb(workout_id, students)

    if edit:
        await target.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=kb, parse_mode="HTML")


@router.message(F.text == "🔔 Проверить тренировки учеников")
async def coach_review_workouts(message: Message, session: AsyncSession):
    """Вход в меню проверки самостоятельных трюков"""
    workout = (await session.execute(
        select(Workout).where(Workout.status == "active")
    )).scalar_one_or_none()

    if not workout:
        await message.answer("❌ Сейчас нет активной тренировки.", reply_markup=await get_coach_menu_for(session))
        return

    grouped = await _get_pending_review(session, workout.id)
    if not grouped:
        await message.answer(
            "✅ Новых самостоятельных трюков для подтверждения нет.",
            reply_markup=await get_coach_menu_for(session)
        )
        return

    students = [await session.get(User, sid) for sid in grouped]
    students = [s for s in students if s]

    await message.answer(
        "🔔 <b>Проверка тренировок</b>\n\n"
        "Спортсмены, отправившие трюки самостоятельно и ждущие подтверждения:\n"
        "Нажмите на имя, чтобы проверить трюки по одному, либо подтвердите всех сразу.",
        reply_markup=get_review_students_kb(workout.id, students),
        parse_mode="HTML"
    )


@router.callback_query(ReviewAction.filter(F.action == "rv_back"))
async def review_back_to_list(callback: CallbackQuery, callback_data: ReviewAction, session: AsyncSession):
    """Возврат к списку учеников, ждущих подтверждения"""
    await _send_review_list(callback.message, session, callback_data.workout_id, edit=True)
    await callback.answer()


async def _send_student_card(target: Message, session: AsyncSession, workout_id: int, student_id: int, edit: bool):
    """Рисует/отправляет карточку самостоятельного ученика с его неподтвержденными трюками.
    edit=True — правит существующее сообщение (из callback), False — отправляет новое"""
    stmt = select(TrickLog).where(
        TrickLog.workout_id == workout_id,
        TrickLog.student_id == student_id,
        TrickLog.needs_review == True  # noqa: E712
    )
    logs = (await session.execute(stmt)).scalars().all()

    if not logs:
        text = "✅ Все трюки этого ученика подтверждены!"
        kb = None
    else:
        student = await session.get(User, student_id)
        trick_ids = {log.trick_id for log in logs}
        tricks = {t.id: t for t in (await session.execute(
            select(Trick).where(Trick.id.in_(trick_ids))
        )).scalars().all()}
        text = (
            f"👤 <b>{student.full_name}</b> — самостоятельные трюки\n\n"
            f"Нажмите на трюк, чтобы задать поправку исполнения и подтвердить его, "
            f"или подтвердите все сразу."
        )
        kb = get_review_student_card_kb(workout_id, student_id, list(logs), tricks)

    if edit:
        await target.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(ReviewAction.filter(F.action == "rv_student"))
async def review_show_student(callback: CallbackQuery, callback_data: ReviewAction, session: AsyncSession):
    """Карточка самостоятельного ученика: его неподтвержденные трюки"""
    if not (await _get_pending_review(session, callback_data.workout_id)).get(callback_data.student_id):
        await callback.answer("Все трюки этого ученика уже подтверждены!", show_alert=True)
        await _send_review_list(callback.message, session, callback_data.workout_id, edit=True)
        return

    await _send_student_card(callback.message, session, callback_data.workout_id, callback_data.student_id, edit=True)
    await callback.answer()


async def _confirm_logs(session: AsyncSession, logs: list[TrickLog], execution_multiplier: float = 1.0):
    """Подтверждает логи: execution_multiplier = переданному значению и пересчет очков/XP"""
    for log in logs:
        trick = await session.get(Trick, log.trick_id)
        student = await session.get(User, log.student_id)
        if not trick or not student:
            continue

        new_points, new_xp = await calculate_single_trick(
            trick=trick,
            cone_line=log.cone_line,
            quantity=log.quantity,
            execution_multiplier=execution_multiplier,
            skater_level=student.level,
            db_session=session
        )
        log.execution_multiplier = execution_multiplier
        old_points, old_xp = log.calculated_points, log.calculated_xp
        log.calculated_points = new_points
        log.calculated_xp = new_xp
        log.needs_review = False

        # Пересчитываем накопленные результаты в регистрации на тренировку
        reg = (await session.execute(
            select(WorkoutRegistration).where(
                WorkoutRegistration.workout_id == log.workout_id,
                WorkoutRegistration.student_id == log.student_id
            )
        )).scalar_one_or_none()
        if reg:
            reg.training_points += (new_points - old_points)
            reg.training_xp += (new_xp - old_xp)
    await session.commit()


@router.callback_query(ReviewAction.filter(F.action == "rv_edit_log"))
async def review_edit_log(callback: CallbackQuery, callback_data: ReviewAction, state: FSMContext):
    """Тренер нажал на трюк: запрашиваем поправку исполнения в процентах"""
    await state.update_data(
        review_workout_id=callback_data.workout_id,
        review_student_id=callback_data.student_id,
        review_log_id=callback_data.log_id,
    )
    await callback.message.edit_text(
        "🎯 <b>Подтверждение трюка</b>\n\n"
        "Введите поправку за качество исполнения в процентах:\n"
        "• <code>0</code> — чисто (исполнение 1.0)\n"
        "• <code>-10</code> — штраф 10% за помарки\n"
        "• <code>10</code> или <code>+10</code> — бонус 10% за идеальное исполнение",
        parse_mode="HTML"
    )
    await state.set_state(WorkoutVerificationStates.waiting_for_multiplier)
    await callback.answer()


@router.message(WorkoutVerificationStates.waiting_for_multiplier)
async def review_process_multiplier(message: Message, state: FSMContext, session: AsyncSession):
    """Принимаем поправку в %, подтверждаем трюк с вычисленным множителем"""
    text_input = (message.text or "").strip().replace(" ", "").replace("%", "")

    if not re.match(r"^[+-]?\d+$", text_input):
        await message.answer("⚠️ Введите целое число в процентах: например 0, -10 или 15")
        return

    pct = int(text_input)
    if pct < -100:
        pct = -100
    execution_multiplier = 1.0 + (pct / 100.0)
    if execution_multiplier < 0:
        execution_multiplier = 0.0

    data = await state.get_data()
    await state.clear()

    workout_id = data["review_workout_id"]
    student_id = data["review_student_id"]
    log_id = data["review_log_id"]

    log = await session.get(TrickLog, log_id)
    if not log or not log.needs_review:
        await message.answer("⚠️ Этот трюк уже подтвержден.", reply_markup=await get_coach_menu_for(session))
        return

    await _confirm_logs(session, [log], execution_multiplier=execution_multiplier)

    # Если у ученика еще остались неподтвержденные трюки — возвращаемся к его карточке,
    # иначе он исчезает из списка и возвращаемся к списку учеников
    remaining = (await session.execute(
        select(TrickLog).where(
            TrickLog.workout_id == workout_id,
            TrickLog.student_id == student_id,
            TrickLog.needs_review == True  # noqa: E712
        )
    )).scalars().all()

    if remaining:
        await _send_student_card(message, session, workout_id, student_id, edit=False)
    else:
        await message.answer("✅ Все трюки этого ученика подтверждены!")
        await _send_review_list(message, session, workout_id, edit=False)


@router.callback_query(ReviewAction.filter(F.action == "rv_confirm_all_student"))
async def review_confirm_all_student(callback: CallbackQuery, callback_data: ReviewAction, session: AsyncSession):
    """Подтвердить все трюки конкретного ученика"""
    logs = (await session.execute(
        select(TrickLog).where(
            TrickLog.workout_id == callback_data.workout_id,
            TrickLog.student_id == callback_data.student_id,
            TrickLog.needs_review == True  # noqa: E712
        )
    )).scalars().all()

    if not logs:
        await callback.answer("У этого ученика нет трюков на подтверждение", show_alert=True)
        await _send_review_list(callback.message, session, callback_data.workout_id, edit=True)
        return

    await _confirm_logs(session, list(logs))
    await callback.answer(f"Подтверждено трюков: {len(logs)}")
    await _send_review_list(callback.message, session, callback_data.workout_id, edit=True)


@router.callback_query(ReviewAction.filter(F.action == "rv_confirm_all"))
async def review_confirm_all(callback: CallbackQuery, callback_data: ReviewAction, session: AsyncSession):
    """Подтвердить все трюки всех ждущих учеников текущей тренировки"""
    grouped = await _get_pending_review(session, callback_data.workout_id)
    all_logs = [log for logs in grouped.values() for log in logs]

    if not all_logs:
        await callback.answer("Нет трюков на подтверждение", show_alert=True)
        return

    await _confirm_logs(session, all_logs)
    await callback.answer(f"Подтверждено трюков: {len(all_logs)}")
    await _send_review_list(callback.message, session, callback_data.workout_id, edit=True)


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