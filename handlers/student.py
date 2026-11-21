import logging
import uuid
from typing import Optional
from aiogram import Router, F, types
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, distinct
from datetime import datetime, timezone

# Твои реальные модели из проекта
from models import (
    User,
    Workout,
    WorkoutRegistration,
    Trick,
    TrickLog,
    STATUS_ACTIVE,
    STATUS_CLOSED,
    CoachStudent,
)
from calculator import (  # noqa: F401 — используется в хендлерах ниже и внешними импортами
    calculate_single_trick,
    calculate_current_workout_totals,
    check_level_up,
)
from keyboards.skater_kb import (
    get_student_main_menu,
    get_student_training_menu,
    get_solo_main_menu,
)
from keyboards.common_kb import HANDBOOK_BTN
from handlers.coach.stats import (
    build_student_profile_text,
    _workout_details_kb,
    send_profile_message,
    fit_telegram_text,
)
from handlers.coach.helpers import get_trick_categories
from handlers.coach.handbook import _handbook_categories_kb
from handlers.track_state import DEFAULT_CONE_LINE, cone_line_text, track_row_markup
from states import StudentStates
from states.coach_states import ComboBuilderStates
from keyboards.coach_kb import ComboAction, get_categories_kb

logger = logging.getLogger(__name__)
router = Router()


# =====================================================================
# ХЭНДЛЕРЫ ПОДКЛЮЧЕНИЯ ПО PIN-КОДУ
# =====================================================================


# =====================================================================
# РЕЖИМ СОЛО: ЛИЧНАЯ ТРЕНИРОВКА АВТОНОМНОГО СПОРТСМЕНА
# =====================================================================


@router.message(F.text == "🔑 Подключиться к тренировке")
async def student_ask_for_pin(
    message: Message, state: FSMContext, session: AsyncSession
):
    """Проверяем профиль и запрашиваем PIN-код тренировки"""
    query = select(User).where(User.telegram_id == message.from_user.id)
    user = (await session.execute(query)).scalar_one_or_none()

    if not user or not user.is_approved:
        await message.answer("❌ Ваша учетная запись еще не одобрена тренером.")
        return

    # Соло-спортсмен не подключается к тренировкам тренера — у него личная кнопка
    if user.is_solo:
        await message.answer(
            "🚀 Вы в одиночном режиме — PIN не нужен.\n"
            "Нажмите «▶️ Начать личную тренировку».",
            reply_markup=get_student_main_menu(is_solo=True),
        )
        return

    await state.set_state(StudentStates.waiting_for_pin)
    await message.answer(
        "Введите 4-значный PIN-код тренировки, который вам дал тренер:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена войти")]], resize_keyboard=True
        ),
    )


@router.message(StudentStates.waiting_for_pin, F.text == "❌ Отмена войти")
async def cancel_pin_input(message: Message, state: FSMContext, session: AsyncSession):
    await state.clear()
    user = (
        await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
    ).scalar_one_or_none()
    await message.answer(
        "Вход отменен.",
        reply_markup=get_student_main_menu(is_solo=bool(user and user.is_solo)),
    )


@router.message(F.text == "▶️ Начать личную тренировку")
async def solo_start_workout(
    message: Message, state: FSMContext, session: AsyncSession
):
    """Соло-спортсмен начинает личную тренировку без тренера и PIN-кода."""
    user = (
        await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
    ).scalar_one_or_none()

    if not user or not user.is_approved:
        await message.answer("❌ Ваша учетная запись еще не одобрена.")
        return
    if not user.is_solo:
        await message.answer(
            "⛔ Личная тренировка доступна только в одиночном режиме.",
            reply_markup=get_student_main_menu(),
        )
        return

    # Уже есть активная личная тренировка — продолжаем её
    active_w = (
        (
            await session.execute(
                select(Workout).where(
                    Workout.status == STATUS_ACTIVE,
                    Workout.is_solo.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    regs = (
        (
            await session.execute(
                select(WorkoutRegistration).where(
                    WorkoutRegistration.student_id == user.id
                )
            )
        )
        .scalars()
        .all()
    )
    my_workout_ids = {r.workout_id for r in regs}
    solo_active = next(
        (w for w in active_w if w.id in my_workout_ids),
        None,
    )

    workout = solo_active
    if workout is None:
        workout = Workout(
            date=datetime.now(timezone.utc),
            status=STATUS_ACTIVE,
            pin_code=None,
            coach_id=None,
            is_solo=True,
        )
        session.add(workout)
        await session.flush()
        session.add(WorkoutRegistration(workout_id=workout.id, student_id=user.id))
        await session.commit()
        await message.answer(
            f"🚀 <b>Личная тренировка №{workout.id} начата!</b>\n\n"
            "Трюки засчитываются сразу, без судейства.",
            reply_markup=get_student_training_menu(is_solo=True),
            parse_mode="HTML",
        )
    else:
        await message.answer(
            f"↩️ Продолжаем личную тренировку №{workout.id}.",
            reply_markup=get_student_training_menu(is_solo=True),
        )

    await state.update_data(current_workout_id=workout.id)
    await state.set_state(StudentStates.training_active)


@router.message(StudentStates.training_active, F.text == "🏁 Завершить тренировку")
async def solo_finish_workout(
    message: Message, state: FSMContext, session: AsyncSession
):
    """Соло-спортсмен завершает личную тренировку: закрытие, начисление XP,
    пересчет уровня и сохранение отчета."""
    user = (
        await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
    ).scalar_one_or_none()
    data = await state.get_data()
    workout_id = data.get("current_workout_id")

    workout = await session.get(Workout, workout_id) if workout_id else None
    if not user or not user.is_solo:
        # Обычную (тренерскую) тренировку завершает только тренер
        await message.answer(
            "⛔ Завершить тренировку может только соло-спортсмен на своей "
            "личной тренировке. Тренерскую тренировку закрывает тренер.",
            reply_markup=get_student_training_menu(is_solo=True),
        )
        return
    if not workout or not workout.is_solo:
        await message.answer(
            "❌ Активной личной тренировки не найдено.",
            reply_markup=get_student_main_menu(is_solo=True),
        )
        await state.clear()
        return

    reg = (
        await session.execute(
            select(WorkoutRegistration).where(
                WorkoutRegistration.workout_id == workout.id,
                WorkoutRegistration.student_id == user.id,
            )
        )
    ).scalar_one_or_none()
    logs = (
        (
            await session.execute(
                select(TrickLog).where(
                    TrickLog.workout_id == workout.id,
                    TrickLog.student_id == user.id,
                )
            )
        )
        .scalars()
        .all()
    )

    final_points, final_xp = await calculate_current_workout_totals(
        list(logs), user, session
    )
    if reg:
        reg.training_points = final_points
        reg.training_xp = final_xp
    user.xp += final_xp

    new_level, level_upgraded = await check_level_up(user.level, user.xp, session)
    lvl_text = ""
    if level_upgraded:
        user.level = new_level
        if reg:
            reg.caused_level_up = True
        lvl_text = f"\n🔥 <b>LEVEL UP! Новый уровень: {new_level}!</b>"

    workout.status = STATUS_CLOSED
    await session.commit()
    await state.clear()

    # Детальный отчет — та же разбивка, что в журнале тренировок тренера
    # (сначала сообщение с итогом, затем полная детализация)
    await message.answer(
        f"🏁 <b>Личная тренировка завершена!</b>\n\n"
        f"🛹 Итог: {final_points:.1f} очков, +{final_xp} EXP{lvl_text}\n"
        f"Отчет сохранен в вашем архиве («👤 Мой персонаж»).",
        reply_markup=get_student_main_menu(is_solo=True),
        parse_mode="HTML",
    )

    if logs:
        from handlers.coach.stats import _combo_exp_map, build_workout_log_text
        from models import LevelExperience

        logs_pairs = [(log, None) for log in logs]
        trick_map = {
            t.id: t
            for t in (
                await session.scalars(
                    select(Trick).where(
                        Trick.id.in_([l.trick_id for l, _t in logs_pairs])
                    )
                )
            ).all()
        }
        pairs = [(log, trick_map.get(log.trick_id)) for log, _t in logs_pairs]
        combo_exp = await _combo_exp_map(
            [(log, t) for log, t in pairs if log.combo_id], user, session
        )
        level_reqs = await session.get(LevelExperience, user.level)
        detail = build_workout_log_text(
            workout, reg, user, pairs, level_reqs, combo_exp
        )
        lvl_note = f"\n🔥 Уровень повышен на этой тренировке!" if level_upgraded else ""
        await message.answer(
            fit_telegram_text(detail + lvl_note),
            parse_mode="HTML",
        )


@router.message(StudentStates.waiting_for_pin)
async def student_process_pin(
    message: Message, state: FSMContext, session: AsyncSession
):
    """Проверяем PIN, регистрируем связь в WorkoutRegistration"""
    pin_input = message.text.strip()

    if not pin_input.isdigit() or len(pin_input) != 4:
        await message.answer(
            "⚠️ PIN-код должен состоять ровно из 4 цифр. Попробуйте еще раз:"
        )
        return

    # Ищем активную тренировку по PIN
    query = select(Workout).where(
        Workout.status == STATUS_ACTIVE, Workout.pin_code == pin_input
    )
    active_workout = (await session.execute(query)).scalar_one_or_none()

    if not active_workout:
        await message.answer(
            "❌ Тренировка с таким PIN-кодом не найдена. Уточните код у тренера:"
        )
        return

    # Находим внутренний ID пользователя в СУБД
    user_query = select(User).where(User.telegram_id == message.from_user.id)
    user = (await session.execute(user_query)).scalar_one_or_none()

    # Проверяем, не зарегистрирован ли он уже (на случай дублей)
    reg_check = select(WorkoutRegistration).where(
        WorkoutRegistration.workout_id == active_workout.id,
        WorkoutRegistration.student_id == user.id,
    )
    existing_reg = (await session.execute(reg_check)).scalar_one_or_none()

    if not existing_reg:
        # Создаем запись в workout_registrations
        registration = WorkoutRegistration(
            workout_id=active_workout.id,
            student_id=user.id,
            training_points=0.0,
            training_xp=0,
        )
        session.add(registration)
        await session.commit()

        # Уведомляем тренера и обновляем ему список спортсменов на занятии:
        # без этого самостоятельные подключения по PIN не появляются
        # в уже отправленном сообщении «ИДЕТ ТРЕНИРОВКА».
        from handlers.coach.helpers import get_workout_students
        from keyboards.coach_kb import get_active_workout_kb

        coach = (
            await session.execute(select(User).where(User.role == "coach").limit(1))
        ).scalar_one_or_none()
        if coach and coach.telegram_id:
            current_students = await get_workout_students(session, active_workout.id)
            try:
                await message.bot.send_message(
                    chat_id=coach.telegram_id,
                    text=(
                        f"🛼 <b>{user.full_name}</b> подключился к тренировке "
                        "по PIN-коду!\n\nСписок спортсменов на занятии:"
                    ),
                    reply_markup=get_active_workout_kb(
                        active_workout.id, current_students
                    ),
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.warning(
                    f"Не удалось уведомить тренера о подключении ученика: {e}"
                )

    # Сохраняем ID тренировки в контекст FSM ученика
    await state.update_data(current_workout_id=active_workout.id)
    await state.set_state(StudentStates.training_active)

    await message.answer(
        "✅ Вы успешно подключились к тренировке!\n\n"
        "Теперь вы можете самостоятельно отправлять выполненные трюки.\n"
        "Шнурки завязаны, бакли затянуты — погнали! 🚀",
        reply_markup=get_student_training_menu(is_solo=bool(user.is_solo)),
    )


# =====================================================================
# ХЭНДЛЕРЫ ВЫБОРА КАТЕГОРИЙ И ТРЮКОВ
# =====================================================================


@router.message(
    StudentStates.training_active, F.text == "🛼 Отправить выполненный трюк"
)
async def show_categories_dynamic(
    message: Message, state: FSMContext, session: AsyncSession
):
    """Ученик открывает общий экран выбора категории (единый поток с тренером).

    Все дальнейшие шаги (категория -> трюк -> конусы -> сохранение/отмена)
    обрабатываются общими хэндлерами handlers.coach.trick_flow.
    """
    data = await state.get_data()
    cone_line = data.get("cone_line", DEFAULT_CONE_LINE)

    categories = (
        (await session.execute(select(distinct(Trick.category)))).scalars().all()
    )
    categories = [c for c in categories if c]
    if not categories:
        await message.answer("В базе данных пока не созданы трюки.")
        return

    workout_id = data.get("current_workout_id") or 0
    user = (
        await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
    ).scalar_one_or_none()
    if not workout_id:
        # FSM мог быть сброшен (например, тренер закрыл тренировку, а студент
        # продолжил ввод) — привязываемся к текущей активной тренировке,
        # иначе логи уйдут с workout_id=0 и пропадут из зачета
        if not user:
            await message.answer("❌ Профиль не найден.")
            return
        active_w = (
            await session.execute(
                select(Workout)
                .join(WorkoutRegistration, WorkoutRegistration.workout_id == Workout.id)
                .where(
                    WorkoutRegistration.student_id == user.id,
                    Workout.status == STATUS_ACTIVE,
                )
                .order_by(Workout.date.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if active_w:
            workout_id = active_w.id
            await state.update_data(current_workout_id=workout_id)
        else:
            await message.answer(
                "❌ Активной тренировки нет — тренер её закрыл.\n"
                "Трюк записать некуда. Попросите тренера открыть новую тренировку.",
                reply_markup=get_student_main_menu(),
            )
            await state.clear()
            return
    if not user:
        await message.answer("❌ Профиль не найден.")
        return

    # Запоминаем контекст ученика: по нему работает общий поток (by_student=True)
    await state.update_data(workout_id=workout_id, student_id=user.id, by_student=True)

    await message.answer(
        f"Выберите категорию трюка.\n{cone_line_text(cone_line)}",
        reply_markup=get_categories_kb(
            workout_id,
            user.id,
            categories,
            by_student=True,
            cone_line=cone_line,
        ),
    )


# =====================================================================
# СТАТИСТИКА ЗА СЕССИЮ И ВЫХОД
# =====================================================================


@router.message(StudentStates.training_active, F.text == "🧩 Отправить связку")
async def student_start_combo(
    message: Message, state: FSMContext, session: AsyncSession
):
    """Ученик начинает конструктор связки (общий FSM с тренером)."""
    user = (
        await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
    ).scalar_one_or_none()
    if not user:
        await message.answer("❌ Профиль не найден.")
        return

    # Ищем активную тренировку ученика (через записи на тренировку)
    workout = (
        await session.execute(
            select(Workout)
            .join(WorkoutRegistration, WorkoutRegistration.workout_id == Workout.id)
            .where(
                WorkoutRegistration.student_id == user.id,
                Workout.status == STATUS_ACTIVE,
            )
            .order_by(Workout.date.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if not workout:
        await message.answer("❌ Нет активной тренировки.")
        return

    await state.update_data(
        combo_workout_id=workout.id,
        combo_student_id=user.id,
        combo_items=[],
        combo_prev_result=None,
        combo_id=str(uuid.uuid4()),
        combo_needs_review=True,  # ученик отправляет самостоятьно - ждет подтверждения
    )

    cats = (await session.execute(select(Trick.category).distinct())).scalars().all()
    kb = InlineKeyboardBuilder()
    for c in cats:
        if c:
            kb.button(text=str(c), callback_data=f"cmb_cat:{c}")
    kb.button(text="❌ Отмена связки", callback_data="cmb_cancel")
    kb.adjust(2)

    # Ряд выбора дорожки — как при добавлении одиночного трюка и у тренера
    cone_line = (await state.get_data()).get("cone_line", DEFAULT_CONE_LINE)
    await state.update_data(cone_line=cone_line)
    rows = [track_row_markup(cone_line, "cmb_coneset:{line}")]
    rows += list(kb.as_markup().inline_keyboard)

    await message.answer(
        f"🧩 <b>Конструктор связки</b>\n\n{cone_line_text(cone_line)}\n\n"
        "Шаг 1: выберите категорию первого трюка:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )
    # Переводим ученика в общий FSM конструктора связки
    await state.set_state(ComboBuilderStates.choosing_first_trick)


@router.message(StudentStates.training_active, F.text == "↩️ Отменить последнее")
async def student_undo_last(message: Message, state: FSMContext, session: AsyncSession):
    """Отмена последнего действия ученика: одиночный трюк или связка
    (по последнему TrickLog). Соло-спортсмену начисленные очки/EXP уже
    выданы — при отмене они вычитаются из профиля."""
    user = (
        await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
    ).scalar_one_or_none()
    if not user:
        await message.answer("❌ Профиль не найден.")
        return

    data = await state.get_data()
    workout_id = data.get("current_workout_id")
    workout = await session.get(Workout, workout_id) if workout_id else None
    if not workout:
        await message.answer(
            "❌ Активная тренировка не найдена.",
            reply_markup=get_student_main_menu(is_solo=bool(user.is_solo)),
        )
        await state.clear()
        return

    last_log = await session.scalar(
        select(TrickLog)
        .where(TrickLog.workout_id == workout.id, TrickLog.student_id == user.id)
        .order_by(TrickLog.id.desc())
        .limit(1)
    )
    if not last_log:
        await message.answer(
            "📭 Нечего отменять: вы пока не отправляли трюки на этой тренировке.",
            reply_markup=get_student_training_menu(is_solo=bool(user.is_solo)),
        )
        return

    is_combo = last_log.combo_id is not None
    if is_combo:
        # Удаляем всю связку целиком
        combo_logs = (
            (
                await session.execute(
                    select(TrickLog).where(
                        TrickLog.workout_id == workout.id,
                        TrickLog.student_id == user.id,
                        TrickLog.combo_id == last_log.combo_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        removed_points = sum(l.calculated_points or 0 for l in combo_logs)
        removed_xp = sum(l.calculated_xp or 0 for l in combo_logs)
        for l in combo_logs:
            await session.delete(l)
        label = f"связка ({len(combo_logs)} трюков)"
    else:
        removed_points = last_log.calculated_points or 0
        removed_xp = last_log.calculated_xp or 0
        await session.delete(last_log)
        label = "последний трюк"

    # Соло-спортсмену очки/EXP начислялись сразу — вычитаем их обратно
    if user.is_solo:
        user.xp = max(0, (user.xp or 0) - removed_xp)
        # Если уровень вырос за счет отмененных очков — оставляем уровень,
        # но XP не опускаем ниже нуля (пересчет уровня делается при закрытии)

    await session.commit()
    await message.answer(
        f"🗑 Отменено: {label}.\n"
        + (
            "Запись удалена, на проверку тренеру не попадет.\n"
            if not user.is_solo
            else ""
        )
        + "Можете продолжать тренировку.",
        reply_markup=get_student_training_menu(is_solo=bool(user.is_solo)),
    )


@router.message(StudentStates.training_active, F.text == "📊 Мои очки за сегодня")
async def student_today_stats(
    message: Message, state: FSMContext, session: AsyncSession
):
    """Разбивка тренировки — та же, что в журнале тренировок тренера
    (build_workout_log_text): шапка с живым пересчетом, зачет, штрафы,
    связки блоками и одиночные трюки."""
    from handlers.coach.stats import _combo_exp_map, build_workout_log_text
    from models import LevelExperience

    user = (
        await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
    ).scalar_one_or_none()
    if not user:
        await message.answer("❌ Профиль не найден.")
        return

    # Ищем активную тренировку ученика (через записи на тренировку)
    workout_query = (
        select(Workout)
        .join(WorkoutRegistration, WorkoutRegistration.workout_id == Workout.id)
        .where(
            WorkoutRegistration.student_id == user.id,
            Workout.status == STATUS_ACTIVE,
        )
        .order_by(Workout.date.desc())
        .limit(1)
    )
    active_workout = (await session.execute(workout_query)).scalar_one_or_none()

    if not active_workout:
        await message.answer("Тренировка уже завершена.")
        return

    reg = (
        await session.execute(
            select(WorkoutRegistration).where(
                WorkoutRegistration.workout_id == active_workout.id,
                WorkoutRegistration.student_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if not reg:
        await message.answer("❌ Регистрация на тренировку не найдена.")
        return

    logs = (
        (
            await session.execute(
                select(TrickLog)
                .join(Trick, Trick.id == TrickLog.trick_id)
                .where(
                    TrickLog.workout_id == active_workout.id,
                    TrickLog.student_id == user.id,
                )
                .order_by(TrickLog.id)
            )
        )
        .scalars()
        .all()
    )

    if not logs:
        await message.answer(
            "📭 Вы пока не отправляли трюки на этой тренировке.",
            reply_markup=get_student_training_menu(is_solo=bool(user.is_solo)),
        )
        return

    trick_map = {
        t.id: t
        for t in (
            await session.scalars(
                select(Trick).where(Trick.id.in_([l.trick_id for l in logs]))
            )
        ).all()
    }

    pairs = [(log, trick_map.get(log.trick_id)) for log in logs]
    combo_exp = await _combo_exp_map(
        [(log, t) for log, t in pairs if log.combo_id], user, session
    )
    level_reqs = await session.get(LevelExperience, user.level)

    # Тот же текст, что тренер видит в журнале тренировок
    text = build_workout_log_text(
        active_workout, reg, user, pairs, level_reqs, combo_exp
    )
    await message.answer(
        text,
        reply_markup=get_student_training_menu(is_solo=bool(user.is_solo)),
        parse_mode="HTML",
    )


@router.message(
    StudentStates.training_active,
    F.text.in_({"⬅️ Назад", "🚪 Покинуть тренировку"}),
)
async def student_leave_workout(
    message: Message, state: FSMContext, session: AsyncSession
):
    """Сбрасывает состояние и возвращает ученика в главное меню"""
    await state.clear()
    user = (
        await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
    ).scalar_one_or_none()
    await message.answer(
        "⬅️ Вы вернулись в главное меню. Тренировка продолжается — "
        "подключитесь к ней снова по PIN-коду или начните личную.",
        reply_markup=get_student_main_menu(is_solo=bool(user and user.is_solo)),
    )


# =====================================================================
# БАЗА ТРЮКОВ (дублирует справочник из coach/handbook.py через те же callback'и hb_*)
# =====================================================================


@router.message(F.text == HANDBOOK_BTN)
async def student_show_handbook(message: Message, session: AsyncSession):
    """База трюков для ученика — тот же справочник, что у тренера.
    Дальнейшая навигация (hb_cat / hb_trick / hb_back) обрабатывается
    хендлерами handlers/coach/handbook.py — они зарегистрированы глобально."""
    categories = await get_trick_categories(session)
    if not categories:
        await message.answer("🤖 Справочник трюков пока пуст. Наполните базу данных.")
        return
    await message.answer(
        "📚 Справочник трюков. Выберите категорию:",
        reply_markup=_handbook_categories_kb(categories),
    )


# =====================================================================
# МОЙ ПРОФИЛЬ (тот же экран, что тренерская статистика по ученику)
# =====================================================================


@router.message(F.text == "👤 Мой персонаж")
async def student_my_profile(message: Message, session: AsyncSession):
    """Профиль ученика: BP, прогресс-бар, разбивка по тренировкам + аватарка"""
    user = (
        await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
    ).scalar_one_or_none()

    if not user or not user.is_approved:
        await message.answer("❌ Ваша учетная запись еще не одобрена тренером.")
        return

    await send_profile_message(message.bot, message.chat.id, user, session)
