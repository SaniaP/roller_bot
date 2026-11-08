"""Пошаговый FSM-конструктор комбо-связок в одном сообщении.

Общий для тренера и ученика: различие только в флаге needs_review
(трюки, отправленные учеником самостоятельно, ждут подтверждения).

Математика (calculator.py):
  table_value = 10 - abs(clamp(round(Y - X)))   # кламп [-9; 10]
  Result      = ((X + Y) / 2) + table_value + (1.0 if Y >= X else 0.0)

Цикл:
  шаг 1: calculated_points = чистый балл трюка, combo_bonus = 0.0, X = points
  шаг 2+: Y = чистый балл нового трюка, в БД: calculated_points = Y,
          combo_bonus = Result - Y; X для следующего шага = Result
"""

import uuid
import logging
import math

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from models import User, Trick, TrickLog, Workout
from states.coach_states import ComboBuilderStates
from states import StudentStates
from keyboards.coach_kb import TrickAction, ComboAction
from calculator import calculate_single_trick, calculate_combo_step
from handlers.track_state import DEFAULT_CONE_LINE, cone_line_text, track_row_markup

logger = logging.getLogger(__name__)

router = Router()


async def _restore_menu_after_combo(
    message_or_callback,
    state: FSMContext,
    session: AsyncSession,
    cancelled: bool = False,
):
    data = await state.get_data()
    student_id = data.get("combo_student_id")
    workout_id = data.get("combo_workout_id")

    saver = (
        await session.execute(
            select(User).where(User.telegram_id == message_or_callback.from_user.id)
        )
    ).scalar_one_or_none()

    from keyboards.skater_kb import get_student_training_menu



    # ОПРЕДЕЛЯЕМ ЦЕЛЬ ДЛЯ ОТПРАВКИ (Message или CallbackQuery.message)
    target = message_or_callback.message if isinstance(message_or_callback, CallbackQuery) else message_or_callback

    # === ЛОГИКА УЧЕНИКА ===
    if saver and saver.id == student_id and saver.role == "student":
        items = data.get("combo_items") or []
        combo_points = 0.0
        combo_xp = 0
        if items:
            last = items[-1]
            combo_points = last["points"] + last.get("bonus", 0.0)
            combo_xp = data.get("combo_total_xp", 0)
        
        # Сброс и настройка состояний ученика
        await state.clear()
        await state.set_state(StudentStates.training_active)
        await state.update_data(
            current_workout_id=workout_id,
            cone_line=data.get("cone_line"),
        )
        
        msg_text = (
            "↩️ Действие отменено."
            if cancelled
            else "🧩 Связка отправлена тренеру на проверку!\n\n"
            f"📊 **Ожидаемое начисление:**\n"
            f"• Баллы: +{round(combo_points, 2)}\n"
            f"• Опыт: +{combo_xp} EXP\n\n"
            "⏳ Очки и EXP засчитаются после подтверждения тренером."
        )

            # === ПРОЖИГАЕМ / УДАЛЯЕМ СТАРЫЙ КОНСТРУКТОР Д для ученика
        if isinstance(message_or_callback, CallbackQuery):
            try:
                await message_or_callback.message.delete()
            except Exception:
                pass    


        # Отправляем ученику одно чистое сообщение
        await target.answer(
            msg_text,
            reply_markup=get_student_training_menu(),
        )

    # === ЛОГИКА ТРЕНЕРА ===
    else:
        await state.clear()
        
        # 2. ВОССТАНАВЛИВАЕМ FSM ТРЕНЕРА
        # Укажите здесь то состояние, в котором тренер находится, когда смотрит карточку ученика
        # Например, если у вас это CoachRegistrationStates.workout_active или WorkoutCreationStates:
        # await state.set_state(CoachRegistrationStates.workout_active) 
        
        # Записываем обратно ID тренировки и ученика, чтобы тренер мог дальше кликать по карточке
        await state.update_data(
            current_workout_id=workout_id,
            selected_student_id=student_id
        )

        from handlers.coach.workout import show_skater_card_or_main_list

        if isinstance(message_or_callback, CallbackQuery) and cancelled:
            try:
                await message_or_callback.answer("Действие отменено")
            except Exception:
                pass

        # Вызываем функцию: она выполнит .edit_text() и превратит конструктор обратно в карточку ученика
        await show_skater_card_or_main_list(
            message_or_callback,
            TrickAction(
                action="select_skater",
                workout_id=workout_id,
                student_id=student_id,
            ),
            session,
        )


# ---------------------------------------------------------------------
# Вспомогательные клавиатуры
# ---------------------------------------------------------------------


def _combo_menu_kb(workout_id: int, student_id: int) -> "InlineKeyboardBuilder":
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🧩 Добавить трюк в связку",
        callback_data=ComboAction(
            action="combo_add", workout_id=workout_id, student_id=student_id
        ),
    )
    builder.button(
        text="✅ Сохранить связку",
        callback_data=ComboAction(
            action="combo_save", workout_id=workout_id, student_id=student_id
        ),
    )
    builder.button(
        text="❌ Отмена",
        callback_data=ComboAction(
            action="combo_cancel", workout_id=workout_id, student_id=student_id
        ),
    )
    builder.adjust(1)
    return builder


def _combo_categories_kb(workout_id: int, student_id: int, categories: list[str]):
    builder = InlineKeyboardBuilder()
    for cat in categories:
        builder.button(
            text=str(cat).capitalize(),
            callback_data=ComboAction(
                action="combo_cat",
                workout_id=workout_id,
                student_id=student_id,
                category=cat,
            ),
        )
    builder.button(
        text="❌ Отмена связки",
        callback_data=ComboAction(
            action="combo_cancel", workout_id=workout_id, student_id=student_id
        ),
    )
    builder.adjust(2)
    return builder.as_markup()


def _combo_tricks_kb(workout_id, student_id, category, tricks):
    builder = InlineKeyboardBuilder()
    for t in tricks:
        builder.button(
            text=f"{t.name} (Tier {t.trick_tier})",
            callback_data=ComboAction(
                action="combo_pick",
                workout_id=workout_id,
                student_id=student_id,
                trick_id=t.id,
            ),
        )
    builder.button(
        text="⬅️ К категориям",
        callback_data=ComboAction(
            action="combo_back", workout_id=workout_id, student_id=student_id
        ),
    )
    builder.adjust(1)
    return builder.as_markup()


# ---------------------------------------------------------------------
# Расчет чистого балла трюка связки (без EXP — EXP считаем отдельно)
# ---------------------------------------------------------------------


async def _pure_trick_points(
    trick: Trick, cone_line: str, quantity: int, execution_multiplier: float
) -> float:
    """Чистый балл трюка для роли X/Y в связке: база × коэф.дорожки × качество × бонус за конусы"""
    line_coeff = 1.0
    if cone_line == "50":
        line_coeff = trick.coeff_50
    elif cone_line == "80":
        line_coeff = trick.coeff_80
    elif cone_line == "120":
        line_coeff = trick.coeff_120

    raw = trick.base_tech_points * line_coeff * execution_multiplier
    if quantity > trick.base_quantity:
        # бонус за конусы сверх базы: +extra_bonus_pct × √(конусов сверх базы) — как в calculator.py
        raw *= 1.0 + math.sqrt(quantity - trick.base_quantity) * trick.extra_bonus_pct
    return round(raw, 2)


# ---------------------------------------------------------------------
# Точка входа: старт связки (из карточки тренера или меню ученика)
# ---------------------------------------------------------------------


@router.callback_query(ComboAction.filter(F.action == "combo_start"))
async def combo_start(
    callback: CallbackQuery,
    callback_data: ComboAction,
    state: FSMContext,
    session: AsyncSession,
):
    await state.update_data(
        combo_workout_id=callback_data.workout_id,
        combo_student_id=callback_data.student_id,
        combo_items=[],  # [{trick_id, cone_line, quantity, mult, points}]
        combo_prev_result=None,
        combo_id=str(uuid.uuid4()),
        combo_needs_review=False,  # тренер вносит лично - подтверждение не требуется
    )
    await state.set_state(ComboBuilderStates.choosing_first_trick)

    # Текущая дорожка — общая с вводом трюков (по умолчанию 80, меняется кнопками)
    data = await state.get_data()
    cone_line = data.get("cone_line", DEFAULT_CONE_LINE)

    # Сразу показываем категории первого трюка + переключатель дорожки
    cats = (await session.execute(select(Trick.category).distinct())).scalars().all()
    kb = InlineKeyboardBuilder()
    for c in cats:
        if c:
            kb.button(text=str(c), callback_data=f"cmb_cat:{c}")
    kb.button(text="❌ Отмена связки", callback_data="cmb_cancel")
    kb.adjust(2)
    rows = [track_row_markup(cone_line, "cmb_coneset:{line}")]
    rows += list(kb.as_markup().inline_keyboard)

    await callback.message.edit_text(
        f"🧩 <b>Конструктор связки</b>\n\n{cone_line_text(cone_line)}\n\n"
        "Шаг 1: выберите категорию первого трюка:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )
    await callback.answer()


# ---------------------------------------------------------------------
# Выбор первого трюка (категории -> трюк -> дорожка -> конусы)
# ---------------------------------------------------------------------


@router.callback_query(
    ComboBuilderStates.choosing_first_trick, F.data.startswith("cmb_coneset:")
)
async def combo_switch_cone_line(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
):
    """Смена дорожки в конструкторе связки (общая с вводом трюков, запоминается)."""
    cone_line = callback.data.split(":")[1]
    await state.update_data(cone_line=cone_line)

    cats = (await session.execute(select(Trick.category).distinct())).scalars().all()
    kb = InlineKeyboardBuilder()
    for c in cats:
        if c:
            kb.button(text=str(c), callback_data=f"cmb_cat:{c}")
    kb.button(text="❌ Отмена связки", callback_data="cmb_cancel")
    kb.adjust(2)
    rows = [track_row_markup(cone_line, "cmb_coneset:{line}")]
    rows += list(kb.as_markup().inline_keyboard)

    await callback.message.edit_text(
        f"🧩 <b>Конструктор связки</b>\n\n{cone_line_text(cone_line)}\n\n"
        "Шаг 1: выберите категорию первого трюка:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )
    await callback.answer(f"Дорожка: {cone_line} см")


@router.callback_query(
    ComboBuilderStates.choosing_first_trick, F.data.startswith("cmb_cat:")
)
async def combo_first_cat(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
):
    cat = callback.data.split(":", 1)[1]
    await state.update_data(combo_cat=cat)
    tricks = (
        (await session.execute(select(Trick).where(Trick.category == cat)))
        .scalars()
        .all()
    )
    kb = InlineKeyboardBuilder()
    for t in tricks:
        kb.button(
            text=f"{t.name} (Tier {t.trick_tier})", callback_data=f"cmb_trick:{t.id}"
        )
    kb.button(text="❌ Отмена связки", callback_data="cmb_cancel")
    kb.adjust(1)
    await callback.message.edit_text(
        "Выберите первый трюк связки:", reply_markup=kb.as_markup()
    )
    await callback.answer()


@router.callback_query(
    ComboBuilderStates.choosing_first_trick, F.data.startswith("cmb_trick:")
)
async def combo_first_trick(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
):
    trick_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    # Дорожка — общая с вводом трюков (по умолчанию 80, меняется на экране категорий)
    cone_line = data.get("cone_line", DEFAULT_CONE_LINE)
    await state.update_data(combo_trick_id=trick_id, combo_cone=cone_line)
    await state.set_state(ComboBuilderStates.waiting_first_quantity)

    await callback.message.edit_text(
        _combo_cones_prompt(cone_line),
        parse_mode="Markdown",
        reply_markup=_combo_cancel_kb(),
    )
    await callback.answer()


@router.message(ComboBuilderStates.waiting_first_quantity)
async def combo_first_quantity(
    message: Message, state: FSMContext, session: AsyncSession
):
    from handlers.coach.helpers import parse_quantity_input, percentage_to_multiplier

    parsed = parse_quantity_input(message.text.strip().replace(" ", ""))
    if not parsed:
        await message.answer("⚠️ Формат: `9` или `8,-10` / `11,20`")
        return
    quantity, pct = parsed
    mult = percentage_to_multiplier(int(pct)) if pct else 1.0

    data = await state.get_data()
    trick = await session.get(Trick, data["combo_trick_id"])
    student = await session.get(User, data["combo_student_id"])

    # EXP первого шага считаем тем же механизмом, что и при сохранении связки
    _, xp = await calculate_single_trick(
        trick=trick,
        cone_line=data["combo_cone"],
        quantity=quantity,
        execution_multiplier=mult,
        skater_level=student.level,
        db_session=session,
    )

    points = await _pure_trick_points(trick, data["combo_cone"], quantity, mult)

    await state.update_data(
        combo_items=[
            {
                "trick_id": trick.id,
                "name": trick.name,
                "cone_line": data["combo_cone"],
                "quantity": quantity,
                "mult": mult,
                "points": points,
            }
        ],
        combo_prev_result=points,
        combo_total_xp=xp,
    )
    await state.set_state(ComboBuilderStates.menu)

    await _show_combo_menu(message, state, session, first_calc_points=points)


# ---------------------------------------------------------------------
# Меню связки: [ + Добавить ] [ ✅ Сохранить ] [ ❌ Отмена ]
# ---------------------------------------------------------------------


async def _show_combo_menu(
    message_or_cb,
    state: FSMContext,
    session: AsyncSession,
    first_calc_points: float | None = None,
):
    data = await state.get_data()
    items = data["combo_items"]
    workout_id = data["combo_workout_id"]
    student_id = data["combo_student_id"]

    lines = [f"🧩 <b>Связка</b> (id {data['combo_id'][:8]}…)\n"]
    for i, it in enumerate(items, start=1):
        bonus_part = f", бонус +{it.get('bonus', 0.0):.2f}" if "bonus" in it else ""
        lines.append(
            f"  {i}. {it['name']} — {it['quantity']} кон. ({it['cone_line']}) — "
            f"{it['points']:.2f} очк.{bonus_part}"
        )
    if data.get("combo_prev_result") is not None and len(items) > 1:
        lines.append(f"\n📊 Текущий Result: <b>{data['combo_prev_result']:.2f}</b>")

    text = "\n".join(lines)
    kb = _combo_menu_kb(workout_id, student_id).as_markup()

    if isinstance(message_or_cb, CallbackQuery):
        await message_or_cb.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await message_or_cb.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(
    ComboBuilderStates.menu, ComboAction.filter(F.action == "combo_cancel")
)
async def combo_cancel(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
):
    # Возврат: тренеру — в карточку спортсмена, ученику — в тренировочное меню
    await _restore_menu_after_combo(callback, state, session, cancelled=True)
    await callback.answer("Действие отменено")


@router.callback_query(
    ComboBuilderStates.menu, ComboAction.filter(F.action == "combo_add")
)
async def combo_add_step(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
):
    """Шаг 2+: выбор следующего трюка (Y)"""
    data = await state.get_data()
    cats = (await session.execute(select(Trick.category).distinct())).scalars().all()
    await state.set_state(ComboBuilderStates.choosing_next_trick)

    # Ряд выбора дорожки — как при добавлении одиночного трюка / первого трюка связки
    cone_line = data.get("cone_line", DEFAULT_CONE_LINE)
    kb = _combo_categories_kb(
        data["combo_workout_id"], data["combo_student_id"], list(cats)
    )
    rows = [track_row_markup(cone_line, "cmb_coneset:{line}")]
    rows += list(kb.inline_keyboard)

    await callback.message.edit_text(
        f"🧩 <b>Конструктор связки</b>\n\n{cone_line_text(cone_line)}\n\n"
        "Выберите категорию следующего трюка связки:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(
    ComboBuilderStates.choosing_next_trick, ComboAction.filter(F.action == "combo_cat")
)
async def combo_next_cat(
    callback: CallbackQuery, callback_data: ComboAction, session: AsyncSession
):
    tricks = (
        (
            await session.execute(
                select(Trick).where(Trick.category == callback_data.category)
            )
        )
        .scalars()
        .all()
    )
    await callback.message.edit_text(
        f"Категория: {callback_data.category.upper()}. Выберите трюк:",
        reply_markup=_combo_tricks_kb(
            callback_data.workout_id,
            callback_data.student_id,
            callback_data.category,
            list(tricks),
        ),
    )
    await callback.answer()


@router.callback_query(
    ComboBuilderStates.choosing_next_trick, ComboAction.filter(F.action == "combo_back")
)
async def combo_back_to_categories(
    callback: CallbackQuery,
    callback_data: ComboAction,
    state: FSMContext,
    session: AsyncSession,
):
    """Кнопка «⬅️ К категориям» на этапе выбора следующего трюка связки"""
    cats = (await session.execute(select(Trick.category).distinct())).scalars().all()
    cone_line = (await state.get_data()).get("cone_line", DEFAULT_CONE_LINE)
    kb = _combo_categories_kb(
        callback_data.workout_id, callback_data.student_id, list(cats)
    )
    rows = [track_row_markup(cone_line, "cmb_coneset:{line}")]
    rows += list(kb.inline_keyboard)
    await callback.message.edit_text(
        f"🧩 <b>Конструктор связки</b>\n\n{cone_line_text(cone_line)}\n\n"
        "Выберите категорию следующего трюка связки:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(
    ComboBuilderStates.choosing_next_trick, F.data.startswith("cmb_coneset:")
)
async def combo_next_switch_cone_line(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
):
    """Смена дорожки на этапе выбора следующего трюка (шаг 2+) связки."""
    cone_line = callback.data.split(":")[1]
    await state.update_data(cone_line=cone_line)

    cats = (await session.execute(select(Trick.category).distinct())).scalars().all()
    data = await state.get_data()
    kb = _combo_categories_kb(
        data.get("combo_workout_id", 0), data.get("combo_student_id", 0), list(cats)
    )
    rows = [track_row_markup(cone_line, "cmb_coneset:{line}")]
    rows += list(kb.inline_keyboard)

    await callback.message.edit_text(
        f"🧩 <b>Конструктор связки</b>\n\n{cone_line_text(cone_line)}\n\n"
        "Выберите категорию следующего трюка связки:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )
    await callback.answer(f"Дорожка: {cone_line} см")


@router.callback_query(
    ComboBuilderStates.choosing_next_trick, ComboAction.filter(F.action == "combo_pick")
)
async def combo_next_pick(
    callback: CallbackQuery, callback_data: ComboAction, state: FSMContext
):
    data = await state.get_data()
    cone_line = data.get("cone_line", DEFAULT_CONE_LINE)
    await state.update_data(combo_trick_id=callback_data.trick_id, combo_cone=cone_line)
    await state.set_state(ComboBuilderStates.waiting_next_quantity)

    await callback.message.edit_text(
        _combo_cones_prompt(cone_line),
        parse_mode="Markdown",
        reply_markup=_combo_cancel_kb(),
    )
    await callback.answer()


@router.message(ComboBuilderStates.waiting_next_quantity)
async def combo_next_quantity(
    message: Message, state: FSMContext, session: AsyncSession
):
    """Расчет шага: Y = чистый балл нового трюка, Result по формуле, бонус = Result - Y"""
    from handlers.coach.helpers import parse_quantity_input, percentage_to_multiplier

    parsed = parse_quantity_input(message.text.strip().replace(" ", ""))
    if not parsed:
        await message.answer("⚠️ Формат: `9` или `8,-10` / `11,20`")
        return
    quantity, pct = parsed
    mult = percentage_to_multiplier(int(pct)) if pct else 1.0

    data = await state.get_data()
    trick = await session.get(Trick, data["combo_trick_id"])
    student = await session.get(User, data["combo_student_id"])
    prev_result = data["combo_prev_result"]

    # EXP шага считаем тем же механизмом, что и при сохранении связки
    _, xp = await calculate_single_trick(
        trick=trick,
        cone_line=data["combo_cone"],
        quantity=quantity,
        execution_multiplier=mult,
        skater_level=student.level,
        db_session=session,
    )

    y_points = await _pure_trick_points(trick, data["combo_cone"], quantity, mult)

    result = calculate_combo_step(prev_result, y_points, session)
    combo_bonus = round(result - y_points, 2)

    items = data["combo_items"]
    items.append(
        {
            "trick_id": trick.id,
            "name": trick.name,
            "cone_line": data["combo_cone"],
            "quantity": quantity,
            "mult": mult,
            "points": y_points,
            "bonus": combo_bonus,
        }
    )
    await state.update_data(
        combo_items=items,
        combo_prev_result=result,
        combo_total_xp=data.get("combo_total_xp", 0) + xp,
    )
    await state.set_state(ComboBuilderStates.menu)

    await _show_combo_menu(message, state, session)


# ---------------------------------------------------------------------
# Сохранение связки: пишем TrickLog по каждому шагу
# ---------------------------------------------------------------------


@router.callback_query(
    ComboBuilderStates.menu, ComboAction.filter(F.action == "combo_save")
)
async def combo_save(
    callback: CallbackQuery,
    callback_data: ComboAction,
    state: FSMContext,
    session: AsyncSession,
):
    from handlers.coach.helpers import parse_quantity_input  # noqa

    data = await state.get_data()
    items = data["combo_items"]
    workout_id = data["combo_workout_id"]
    student_id = data["combo_student_id"]
    combo_id = data["combo_id"]

    if len(items) < 2:
        await callback.answer("В связке должен быть минимум 2 трюка!", show_alert=True)
        return

    student = await session.get(User, student_id)

    # needs_review определяем надежно - по тому, КТО ФАКТИЧЕСКИ нажал «Сохранить»:
    # если сохранил сам ученик со своего аккаунта - связка самостоятельная и ждет
    # подтверждения; если тренер (со своего аккаунта, в т.ч. от имени ученика) - нет.
    # FSM-данные не используем: состояние могло сброситься, и флаг терялся,
    # из-за чего связки учеников не попадали в меню проверки.
    saver = (
        await session.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
    ).scalar_one_or_none()
    needs_review = bool(saver and saver.id == student.id and saver.role == "student")

    saved = 0
    for step, it in enumerate(items, start=1):
        # EXP считаем обычным механизмом одиночного трюка
        _, xp = await calculate_single_trick(
            trick=await session.get(Trick, it["trick_id"]),
            cone_line=it["cone_line"],
            quantity=it["quantity"],
            execution_multiplier=it["mult"],
            skater_level=student.level,
            db_session=session,
        )
        session.add(
            TrickLog(
                workout_id=workout_id,
                student_id=student_id,
                trick_id=it["trick_id"],
                combo_id=combo_id,
                combo_step=step,
                cone_line=it["cone_line"],
                quantity=it["quantity"],
                execution_multiplier=it["mult"],
                calculated_points=it["points"],
                calculated_xp=xp,
                combo_bonus=it.get("bonus", 0.0) if step > 1 else 0.0,
                needs_review=needs_review,
            )
        )
        saved += 1

    await session.commit()

    await callback.message.edit_text(
        f"✅ Связка из {saved} трюков сохранена!\n"
        f"combo_id: <code>{combo_id[:8]}…</code>",
        parse_mode="HTML",
    )
    await callback.answer()

    # Возврат: тренеру — в карточку спортсмена, ученику — в тренировочное меню
    await _restore_menu_after_combo(callback, state, session)


def _combo_cones_prompt(cone_line: str) -> str:
    """Единый текст экрана ввода конусов в конструкторе связки."""
    return (
        f"{cone_line_text(cone_line)}\n\n"
        "🔢 Отправьте количество конусов (можно с поправкой качества: `8,-10` или `11,20`):"
    )


def _combo_cancel_kb() -> InlineKeyboardMarkup:
    """Кнопка отмены на экране ввода количества конусов в связке."""
    return (
        InlineKeyboardBuilder()
        .button(text="❌ Отмена", callback_data="cmb_cancel")
        .as_markup()
    )


# Универсальный обработчик отмены на любых стадиях (cmb_cancel из inline-клавиатур)
@router.callback_query(F.data == "cmb_cancel")
async def combo_cancel_universal(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
):
    # Возврат: тренеру — в карточку спортсмена, ученику — в тренировочное меню.
    # Раньше здесь был state.clear() без восстановления training_active,
    # из-за чего у ученика после отмены переставали работать все кнопки меню.
    await _restore_menu_after_combo(callback, state, session, cancelled=True)
    await callback.answer("Действие отменено")
