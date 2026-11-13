"""Статистика: список учеников с сортировкой по уровню и профиль с разбивкой по тренировкам."""

import logging
from datetime import date, datetime

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from aiogram.filters.callback_data import CallbackData
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from models import (
    STATUS_ACTIVE,
    STATUS_CLOSED,
    User,
    Workout,
    WorkoutRegistration,
    TrickLog,
    Trick,
    LevelExperience,
    XpPenalty,
)
from handlers.coach.helpers import (
    get_coach_menu_for,
    draw_progress_bar,
    level_progress_percent,
)
from keyboards.skater_kb import get_student_main_menu

router = Router()

TELEGRAM_TEXT_LIMIT = 4000  # запас ниже официального лимита 4096
TELEGRAM_CAPTION_LIMIT = 1024  # лимит подписи к фото


def calculate_age(born: date) -> int:
    """Точный возраст с учетом месяца и дня рождения."""
    today = date.today()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


class ProfileStates(StatesGroup):
    waiting_for_birthdate = State()
    waiting_for_status = State()


logger = logging.getLogger(__name__)

CANCEL_PROFILE_INPUT_BTN = "❌ Отмена"


def _profile_input_kb() -> ReplyKeyboardMarkup:
    """Reply-клавиатура с кнопкой отмены для состояний ввода профиля"""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=CANCEL_PROFILE_INPUT_BTN)]],
        resize_keyboard=True,
    )


def fit_telegram_text(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> str:
    """Обрезает текст до лимита Telegram по границе строк.
    Проблема: профиль ученика со всеми тренировками может превысить
    лимит сообщения (4096 симв.) и упасть с 'message is too long'.
    """
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # Режем по последнему переводу строки, чтобы не разорвать запись
    nl = cut.rfind("\n")
    if nl > 0:
        cut = cut[:nl]
    return cut + "\n… (показаны не все записи)"


# --- Фабрика callback-данных статистики ---
class StatsAction(CallbackData, prefix="stat"):
    action: str  # "stats_student" — профиль ученика, "stats_back" — назад к списку,
    # "stats_workout" — детальная разбивка одной тренировки
    student_id: int = 0
    workout_id: int = 0


def _students_kb(students: list[User]) -> InlineKeyboardMarkup:
    """Инлайн-кнопки с именами учеников"""
    builder = InlineKeyboardBuilder()
    for st in students:
        builder.button(
            text=f"{st.full_name} (Ур. {st.level})",
            callback_data=StatsAction(action="stats_student", student_id=st.id),
        )
    builder.adjust(1)
    return builder.as_markup()


@router.message(F.text == "📊 Статистика")
async def coach_stats(message: Message, session: AsyncSession):
    """Список всех учеников, отсортированный по уровню (по убыванию)"""
    students = (
        (
            await session.execute(
                select(User)
                # .where(User.role == "student") временно убрал
                .order_by(User.level.desc(), User.xp.desc())
            )
        )
        .scalars()
        .all()
    )

    if not students:
        await message.answer(
            "📊 <b>Статистика</b>\n\nУчеников пока нет.",
            reply_markup=await get_coach_menu_for(session),
            parse_mode="HTML",
        )
        return

    lines = ["📊 <b>Статистика учеников</b> (сортировка по уровню)\n"]

    for i, st in enumerate(students, start=1):
        best_points = await _best_training_points(session, st.id)
        bp = f"{best_points:.1f}" if best_points is not None else "—"
        next_reqs = await session.get(LevelExperience, st.level + 1)
        if next_reqs:
            cur_reqs = await session.get(LevelExperience, st.level)
            prev_xp = cur_reqs.required_xp if cur_reqs else 0
            percent = level_progress_percent(st.xp, prev_xp, next_reqs.required_xp)
            bar = draw_progress_bar(percent)
        else:
            bar = "🏆 Максимальный уровень достигнут!"
        lines.append(
            f"{i}. 👤 <b>{st.full_name}</b> - {st.level} LVL, BP : {bp}\n" f"{bar}"
        )

    await message.answer(
        "\n".join(lines),
        reply_markup=_students_kb(students),
        parse_mode="HTML",
    )


async def _workout_caused_level_up(
    session: AsyncSession, student: User, workout_id: int, training_xp: int
) -> bool:
    """Проверяет по логам: boosted ли уровень этой тренировкой.
    Уровень растёт, когда накопленный EXP (без этой тренировки) был ниже порога,
    а с этой тренировкой — достиг"""
    # EXP всех ДРУГИХ закрытых тренировок ученика
    other_regs = (
        (
            await session.execute(
                select(WorkoutRegistration.training_xp)
                .join(Workout, Workout.id == WorkoutRegistration.workout_id)
                .where(
                    WorkoutRegistration.student_id == student.id,
                    WorkoutRegistration.workout_id != workout_id,
                    Workout.status == STATUS_CLOSED,
                )
            )
        )
        .scalars()
        .all()
    )
    xp_before = sum(other_regs)
    xp_after = xp_before + training_xp

    next_reqs = await session.get(LevelExperience, student.level)
    if not next_reqs:
        return False

    threshold = next_reqs.required_xp
    # Ученик сейчас на уровне L: значит порог достижения уровня L был превышен.
    # Тренировка вызвала lvl up, если xp_before < threshold <= xp_after
    return xp_before < threshold <= xp_after


async def _best_training_points(session: AsyncSession, student_id: int) -> float | None:
    """Лучший (максимальный) результат training_points спортсмена по всем тренировкам"""
    return await session.scalar(
        select(func.max(WorkoutRegistration.training_points)).where(
            WorkoutRegistration.student_id == student_id
        )
    )


async def _progress_line(student: User, session: AsyncSession) -> str:
    """Строка: имя — уровень, Лучший результат; ниже голый бар прогресса опыта.
    Используется сквозным образом в интерфейсе тренера и студента."""
    best_points = await _best_training_points(session, student.id)
    bp = f"{best_points:.1f} очк." if best_points is not None else "—"

    # Строка 1: Имя и Уровень
    line_name_lvl = f"👤 <b>{student.full_name}</b> — {student.level} Уровень"
    # Строка 2: Лучший технический показатель (BP)
    line_bp = f"🏆 Лучший результат: {bp}"

    next_reqs = await session.get(LevelExperience, student.level + 1)
    if not next_reqs:
        # Если уровень максимальный, выводим имя, рекорд и плашку максимума
        return f"{line_name_lvl}\n{line_bp}\n✨ Максимальный уровень достигнут!"

    cur_reqs = await session.get(LevelExperience, student.level)
    prev_xp = cur_reqs.required_xp if cur_reqs else 0
    percent = level_progress_percent(student.xp, prev_xp, next_reqs.required_xp)

    # Строка 3: Прогресс-бар с процентами
    line_bar = draw_progress_bar(percent)

    # Собираем всё вместе строго через переносы строк
    return f"{line_name_lvl}\n{line_bp}\nПрогресс: {line_bar}"


def build_workout_log_text(
    workout: Workout,
    reg: WorkoutRegistration,
    student: User,
    logs: list[tuple[TrickLog, Trick]],
    level_reqs: LevelExperience | None,
    combo_exp: dict[str, int] | None = None,
) -> str:
    """Детальная разбивка одной тренировки: шапка + связки блоками + одиночные трюки."""
    date_str = workout.date.strftime("%d.%m.%Y %H:%M") if workout.date else "—"
    parts = [
        f"🛹 <b>Тренировка №{workout.id}</b> от {date_str} ({workout.status})",
        f"Итог: 🏅 {reg.training_points:.1f} очков | ✨ +{reg.training_xp} EXP",
    ]

    if not logs:
        parts.append("Трюки не записаны.")
        return "\n".join(parts)

    # Определяем зачет по правилам калькулятора (в т.ч. результат связки как единое целое)
    notes, penalties, combo_in_game = _workout_breakdown(logs, level_reqs, combo_exp)
    if penalties:
        parts.extend(f"<i>{p}</i>" for p in penalties)

    # Группируем: связки (combo_id) блоками, одиночные трюки — списком (порядок лога)
    # Жирным выделяются очки результатов, пошедших в зачет спорта (топ-8 по очкам).
    combos: dict[str, list[tuple[TrickLog, Trick]]] = {}
    singles: list[tuple[TrickLog, Trick]] = []
    for pair in logs:
        log, _t = pair
        if log.combo_id:
            combos.setdefault(log.combo_id, []).append(pair)
        else:
            singles.append(pair)

    if combos:
        parts.append("\n<b>🧩 Связки:</b>")
        for cid, steps in combos.items():
            parts.append(f"Связка (id {cid[:8]}…):")
            for log, trick in sorted(steps, key=lambda p: p[0].combo_step or 0):
                bonus = log.combo_bonus or 0.0
                pts = _points_str(notes.get(log.id), log.calculated_points, 2)[0]
                # У шагов связки пометки «не в зачет…» не выводим — зачет связки
                # оценивается целиком в строке «Итог связки»
                line = (
                    f"   • шаг {log.combo_step}: {trick.name} — {log.quantity} кон. "
                    f"({log.cone_line}) — {pts} очк. + бонус {bonus:.2f}"
                )
                parts.append(line)
            # Стоимость связки = Result последнего шага (шаги НЕ суммируются):
            # Result шага = calculated_points + combo_bonus этого шага.
            # EXP связки = сумма EXP всех шагов; 🚫 — если связка не вошла в зачет опыта.
            last = max(steps, key=lambda p: p[0].combo_step or 0)[0]
            total = last.calculated_points + (last.combo_bonus or 0.0)
            combo_xp = (combo_exp or {}).get(cid) or sum(
                log.calculated_xp for log, _t in steps
            )
            pts = _points_str(notes.get(last.id), total, 2)[0]
            xp_note = "🚫" if not combo_in_game.get(cid, False) else ""
            parts.append(
                f"   ↳ Итог связки : {pts} очк. " f"+{combo_xp} EXP{xp_note}\n"
            )

    if singles:
        parts.append("<b>🎯 Одиночные трюки:</b>")

        for log, trick in singles:
            mult_note = ""
            if log.execution_multiplier and log.execution_multiplier != 1.0:
                mult_note = f" (качество ×{log.execution_multiplier:.2f})"
            pts, suffix = _points_str(notes.get(log.id), log.calculated_points, 1)
            line = (
                f" • {trick.name}: {log.quantity} кон. (дорожка {log.cone_line}){mult_note} — "
                f"{pts} очк., +{log.calculated_xp} EXP{suffix}"
            )
            parts.append(line)

    return fit_telegram_text("\n".join(parts))


async def _combo_exp_map(
    pairs: list[tuple[TrickLog, Trick]], skater: User, session: AsyncSession
) -> dict[str, int]:
    """EXP каждой связки по формуле из calculator.py (calculate_current_workout_totals):
      combo_points = Result последнего шага (calculated_points + combo_bonus)
      base_ratio   = Σ(trick.base_xp) / Σ(trick.base_tech_points) по шагам связки
      avg_quantity = среднее количество конусов по шагам
      raw_xp       = combo_points * base_ratio * avg_quantity * 1.4 * length_bonus,
      length_bonus = 1.0 + 0.15 * (число_шагов - 1)  # бонус за длину связки
      xp           = int(raw_xp * штраф_за_уровень_последнего_шага)
    Возвращает {combo_id: xp}. Связки без шагов пропускаются."""
    combo_steps: dict[str, list[tuple[TrickLog, Trick]]] = {}
    for log, trick in pairs:
        if log.combo_id:
            combo_steps.setdefault(log.combo_id, []).append((log, trick))

    result: dict[str, int] = {}
    for cid, steps in combo_steps.items():
        steps.sort(key=lambda p: p[0].combo_step or 0)
        last_log, last_trick = steps[-1]

        combo_points = last_log.calculated_points + (last_log.combo_bonus or 0.0)

        sum_base_xp = sum(trick.base_xp for _l, trick in steps)
        sum_base_tech = sum(trick.base_tech_points for _l, trick in steps)
        base_ratio = sum_base_xp / sum_base_tech if sum_base_tech else 1.0

        avg_quantity = sum(log.quantity for log, _t in steps) / len(steps)

        length_bonus = 1.0 + 0.15 * max(0, len(steps) - 1)
        raw_xp = combo_points * base_ratio * avg_quantity * 1.4 * length_bonus

        xp_multiplier = 1.0
        if last_trick is not None:
            level_diff = skater.level - last_trick.trick_tier
            if level_diff > 0:
                penalty = await session.scalar(
                    select(XpPenalty).where(XpPenalty.level_difference == level_diff)
                )
                if penalty:
                    xp_multiplier = penalty.xp_multiplier

        result[cid] = max(0, int(raw_xp * xp_multiplier))
    return result


def _points_str(note: str | None, points: float, prec: int) -> tuple[str, str]:
    """Форматирует очки: жирным — если результат в зачете спорта (топ-8 по очкам).
    Возвращает (строка очков, суффикс-пометка).
    🚫 ставится только если результат НЕ в зачете опыта (note 'out' или 'sport').
    'game' означает: опыт в зачете, очков нет — 🚫 не нужен."""
    val = f"{points:.{prec}f}"
    if note in ("ok", "sport"):
        val = f"<b>{val}</b>"
    suffix = " 🚫" if note in ("out", "sport") else ""
    return val, suffix


def _workout_breakdown(
    pairs: list[tuple[TrickLog, Trick]],
    level_reqs: LevelExperience | None,
    combo_exp: dict[str, int] | None = None,
) -> tuple[dict[int, str], list[str], dict[str, bool]]:
    """Разбивка логов тренировки по правилам калькулятора.

    - Спортивный режим: в зачет идут 8 лучших трюков по calculated_points.
    - Игровой режим: в зачет идут не более max_workout_logs лучших трюков по calculated_xp.

    Возвращает:
        (log_notes, penalties, combo_in_game) — где log_notes: {log_id: "добавка к строке"},
        penalties: список текстов штрафов для вывода в разбивке,
        combo_in_game: {combo_id: bool} — вошла ли связка в зачет опыта.
    """
    max_logs = level_reqs.max_workout_logs if level_reqs else 10
    min_categories = level_reqs.min_trick_categories if level_reqs else 3

    # Полностью повторяем логику calculator.py: шаги связок идут в списке как
    # обычные логи, ПОМИМО этого добавляется ОДИН результат связки
    # (points = Result последнего шага, xp = сумма EXP всех шагов).
    # Топ-8 (очки) и топ-N (опыт) выбираются из этого расширенного списка.
    combo_steps: dict[str, list[TrickLog]] = {}
    for log, _t in pairs:
        if log.combo_id:
            combo_steps.setdefault(log.combo_id, []).append(log)

    # Запись расширенного списка: (points, xp, log_ids очков, log_ids опыта)
    results: list[tuple[float, int, list[int], list[int]]] = []
    for log, _t in pairs:
        # Шаги связок в зачете опыта не участвуют лично — их опыт идет
        # только через агрегат связки (как в calculator.py: xp_results
        # = одиночные трюки + одна запись на связку)
        xp_ids = [] if log.combo_id else [log.id]
        results.append((log.calculated_points, log.calculated_xp, [log.id], xp_ids))
    for cid, steps in combo_steps.items():
        steps.sort(key=lambda l: l.combo_step or 0)
        last = steps[-1]
        combo_points = last.calculated_points + (last.combo_bonus or 0.0)
        combo_xp = (combo_exp or {}).get(cid) or sum(s.calculated_xp for s in steps)
        # очки связки «сидят» на последнем шаге, опыт — на всех шагах
        results.append((combo_points, combo_xp, [last.id], [s.id for s in steps]))

    # Зачет спорта: топ-8 результатов по очкам (шаги связок участвуют в очках)
    in_sport_ids: set[int] = set()
    for _p, _x, p_ids, _xp_ids in sorted(results, key=lambda r: r[0], reverse=True)[:8]:
        in_sport_ids.update(p_ids)
    # Зачет игры: топ max_workout_logs результатов по EXP.
    # Шаги связок в конкурсе опыта не участвуют вовсе (xp_ids пуст) —
    # их опыт идет только через агрегат связки, они не должны занимать слоты.
    xp_ranking = [r for r in results if r[3]]
    in_game_ids: set[int] = set()
    for _p, _x, _p_ids, xp_ids in sorted(xp_ranking, key=lambda r: r[1], reverse=True)[
        :max_logs
    ]:
        in_game_ids.update(xp_ids)

    notes: dict[int, str] = {}
    for log, _trick in pairs:
        in_sport = log.id in in_sport_ids
        in_game = log.id in in_game_ids
        if in_sport and in_game:
            notes[log.id] = "ok"  # в зачете и очков, и опыта
        elif in_sport:
            notes[log.id] = "sport"  # в зачете очков, но не опыта
        elif in_game:
            notes[log.id] = "game"  # в зачете опыта, но не очков
        else:
            notes[log.id] = "out"  # полностью вне зачета

    penalties: list[str] = []
    if pairs:
        categories_count = len({trick.category for _log, trick in pairs})
        if len(results) < 8:
            missing_tricks = 8 - len(results)
            penalties.append(
                f"⚠️ Штраф за недобор трюков: -{missing_tricks * 3.0:.1f} очков ({missing_tricks} × 3.0)"
            )
        if categories_count < min_categories:
            missing_categories = min_categories - categories_count
            penalties.append(
                f"⚠️ Штраф за недобор категорий: -{missing_categories * 3.0:.1f} очков ({missing_categories} × 3.0)"
            )
            penalties.append(
                f"⚠️ Штраф за недобор категорий: -{missing_categories * 10}% EXP ({missing_categories} × 10%)"
            )

    # Связка считается вошедшей в зачет опыта, если в топе опыта находится
    # хотя бы один ее шаг (опыт связки «сидит» на всех шагах)
    combo_in_game: dict[str, bool] = {}
    for cid, steps in combo_steps.items():
        combo_in_game[cid] = any(s.id in in_game_ids for s in steps)

    return notes, penalties, combo_in_game


async def build_student_profile_text(student: User, session: AsyncSession) -> str:
    """Полный текст профиля ученика: шапка с BP и баром + разбивка по тренировкам.
    Используется и тренерской статистикой, и кнопкой «Мой профиль» у ученика"""
    # Получаем шапку, где содержатся Имя, Уровень, Рекорд и Прогресс-бар
    progress = await _progress_line(student, session)

    # Формируем строку параметров (Возраст и Статус)
    if student.birth_date:
        age_str = f"📅 <b>Возраст:</b> {calculate_age(student.birth_date)} лет"
    else:
        age_str = "📅 <b>Возраст:</b> не указан"

    if student.custom_status:
        status_str = f"💬 <b>Статус:</b> <i>«{student.custom_status}»</i>"
    else:
        status_str = f"💬 <b>Статус:</b> {student.title}"

    params_line = f"{age_str}\n{status_str}"

    # Все тренировки, где участвовал ученик (свежие сверху)
    regs = (
        await session.execute(
            select(WorkoutRegistration, Workout)
            .join(Workout, Workout.id == WorkoutRegistration.workout_id)
            .where(WorkoutRegistration.student_id == student.id)
            .order_by(Workout.date.desc())
        )
    ).all()

    # params_line идет аккуратно под прогресс-баром
    parts = [progress, params_line, ""]

    if not regs:
        parts.append("Тренировок пока не было.")
        return "\n".join(parts)

    parts.append("\n<b>Мои тренировки</b> (подробности — по кнопке 🔎):")

    for reg, workout in regs:
        date_str = workout.date.strftime("%d.%m.%Y") if workout.date else "—"
        lvl_up_note = ""
        # Если после этой тренировки у ученика вырос уровень — фиксируем это в разбивке
        if workout.status == STATUS_CLOSED and reg.training_xp:
            lvl_up_note = (
                " 🔥"
                if await _workout_caused_level_up(
                    session, student, workout.id, reg.training_xp
                )
                else ""
            )
        parts.append(
            f" • {date_str} (№{workout.id}, {workout.status}) — "
            f"🏅 {reg.training_points:.1f} очк., +{reg.training_xp} EXP{lvl_up_note}"
        )

    return fit_telegram_text("\n".join(parts))


async def get_student_workout_ids(
    student: User, session: AsyncSession
) -> list[tuple[int, Workout]]:
    """ID тренировок ученика (свежие сверху) для инлайн-кнопок деталей."""
    regs = (
        await session.execute(
            select(WorkoutRegistration, Workout)
            .join(Workout, Workout.id == WorkoutRegistration.workout_id)
            .where(WorkoutRegistration.student_id == student.id)
            .order_by(Workout.date.desc())
        )
    ).all()
    return [(reg.workout_id, workout) for reg, workout in regs]


async def _workout_details_kb(
    student: User, session: AsyncSession, is_own_profile: bool = False
) -> InlineKeyboardMarkup:
    """Инлайн-кнопки: детальная разбивка по каждой тренировке ученика + кнопка назад.
    «К списку учеников» — только тренеру (у ученика этот список недоступен).
    is_own_profile=True — добавляет кнопки управления профилем самого ученика."""
    builder = InlineKeyboardBuilder()
    for workout_id, workout in await get_student_workout_ids(student, session):
        date_str = workout.date.strftime("%d.%m.%Y") if workout.date else "—"
        builder.button(
            text=f"🔎 Тренировка №{workout.id} ({date_str})",
            callback_data=StatsAction(
                action="stats_workout",
                student_id=student.id,
                workout_id=workout_id,
            ),
        )
    if student.role == "coach":
        builder.button(
            text="⬅️ К списку учеников",
            callback_data=StatsAction(action="stats_back"),
        )

    # Кнопки управления собственным профилем (только для владельца)
    if is_own_profile:
        if not student.birth_date:
            builder.row(
                InlineKeyboardButton(
                    text="📅 Указать дату рождения",
                    callback_data="profile_set_birthdate",
                )
            )
        if student.level >= 10:
            builder.row(
                InlineKeyboardButton(
                    text="✍️ Изменить статус",
                    callback_data="profile_set_status",
                )
            )

    builder.adjust(1)
    return builder.as_markup()


async def _edit_profile_message(message: Message, text: str, reply_markup):
    """Редактирует сообщение профиля: у фото-сообщения меняем caption, у текстового — text."""
    if message.photo:
        await message.edit_caption(
            caption=text, reply_markup=reply_markup, parse_mode="HTML"
        )
    else:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")


@router.callback_query(StatsAction.filter(F.action == "stats_student"))
async def stats_student_profile(
    callback: CallbackQuery, callback_data: StatsAction, session: AsyncSession
):
    """Профиль ученика: краткие итоги тренировок + кнопки деталей"""
    student = await session.get(User, callback_data.student_id)
    if not student:
        await callback.answer("Ученик не найден", show_alert=True)
        return

    text = await build_student_profile_text(student, session)
    await _edit_profile_message(
        callback.message,
        text,
        await _workout_details_kb(student, session),
    )
    await callback.answer()


@router.callback_query(StatsAction.filter(F.action == "stats_workout"))
async def stats_workout_details(
    callback: CallbackQuery, callback_data: StatsAction, session: AsyncSession
):
    """Детальная разбивка одной тренировки: трюки, зачет, штрафы"""
    student = await session.get(User, callback_data.student_id)
    workout = await session.get(Workout, callback_data.workout_id)
    if not student or not workout:
        await callback.answer("Данные не найдены", show_alert=True)
        return

    reg = (
        await session.execute(
            select(WorkoutRegistration).where(
                WorkoutRegistration.workout_id == workout.id,
                WorkoutRegistration.student_id == student.id,
            )
        )
    ).scalar_one_or_none()
    if not reg:
        await callback.answer("Регистрация не найдена", show_alert=True)
        return

    logs = (
        await session.execute(
            select(TrickLog, Trick)
            .join(Trick, Trick.id == TrickLog.trick_id)
            .where(
                TrickLog.workout_id == workout.id,
                TrickLog.student_id == student.id,
            )
            .order_by(TrickLog.id)
        )
    ).all()

    level_reqs = await session.get(LevelExperience, student.level)
    combo_exp = await _combo_exp_map(logs, student, session)
    text = build_workout_log_text(workout, reg, student, logs, level_reqs, combo_exp)

    # Добавляем пометку о повышении уровня, если оно было на этой тренировке
    if (
        workout.status == STATUS_CLOSED
        and reg.training_xp
        and await _workout_caused_level_up(
            session, student, workout.id, reg.training_xp
        )
    ):
        text = fit_telegram_text(text + "\n🔥 Уровень повышен на этой тренировке!")

    builder = InlineKeyboardBuilder()
    builder.button(
        text="⬅️ К профилю ученика",
        callback_data=StatsAction(action="stats_student", student_id=student.id),
    )
    await _edit_profile_message(callback.message, text, builder.as_markup())
    await callback.answer()


@router.callback_query(StatsAction.filter(F.action == "stats_back"))
async def stats_back_to_list(callback: CallbackQuery, session: AsyncSession):
    """Возврат к списку учеников"""
    students = (
        (
            await session.execute(
                select(User).order_by(User.level.desc(), User.xp.desc())
            )
        )
        .scalars()
        .all()
    )

    text = "📊 <b>Статистика учеников</b> (сортировка по уровню)\n\n"
    if students:
        text += "\n".join(
            f"{i}. 👤 <b>{st.full_name}</b> — Ур. {st.level}, {st.xp} EXP ({st.title})"
            for i, st in enumerate(students, start=1)
        )
    else:
        text += "Учеников пока нет."

    await callback.message.edit_text(
        text, reply_markup=_students_kb(list(students)), parse_mode="HTML"
    )
    await callback.answer()


async def send_profile_message(bot, chat_id: int, user: User, session: AsyncSession):
    """Отправляет профиль с аватаркой (кэшируется в user.photo_file_id).
    Текст <= 1024 симв. уходит в caption к фото, иначе — отдельным сообщением."""
    text = await build_student_profile_text(user, session)
    kb = await _workout_details_kb(user, session, is_own_profile=True)

    file_id = user.photo_file_id

    # Пробуем достать аватарку из Telegram, если она еще не закэширована
    if not file_id:
        try:
            photos = await bot.get_user_profile_photos(
                user_id=user.telegram_id, limit=1
            )
            if photos.total_count > 0:
                photo = photos.photos[0][0]  # самый низкое разрешение из первой пачки
                file_id = photo.file_id
                user.photo_file_id = file_id
                await session.commit()
        except Exception as e:
            logger.error(f"Не удалось получить аватарку: {e}")

    if file_id:
        # Лимит caption — 1024 символа: длинный текст отправляем вторым сообщением
        if len(text) <= TELEGRAM_CAPTION_LIMIT:
            await bot.send_photo(
                chat_id=chat_id,
                photo=file_id,
                caption=text,
                reply_markup=kb,
                parse_mode="HTML",
            )
        else:
            await bot.send_photo(chat_id=chat_id, photo=file_id)
            await bot.send_message(
                chat_id=chat_id, text=text, reply_markup=kb, parse_mode="HTML"
            )
    else:
        await bot.send_message(
            chat_id=chat_id, text=text, reply_markup=kb, parse_mode="HTML"
        )


# =====================================================================
# FSM: дата рождения и кастомный статус профиля
# =====================================================================


@router.callback_query(F.data == "profile_set_birthdate")
async def profile_set_birthdate(callback: CallbackQuery, state: FSMContext):
    """Запрашиваем дату рождения ученика"""
    await state.set_state(ProfileStates.waiting_for_birthdate)
    await callback.message.answer(
        "📅 Напиши дату своего рождения в формате: ДД.ММ.ГГГГ (например, 15.03.2015)",
        reply_markup=_profile_input_kb(),
    )
    await callback.answer()


@router.message(ProfileStates.waiting_for_birthdate, F.text == CANCEL_PROFILE_INPUT_BTN)
async def profile_cancel_birthdate(
    message: Message, state: FSMContext, session: AsyncSession
):
    """Отмена ввода даты рождения — возврат к профилю"""
    await state.clear()
    user = (
        await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
    ).scalar_one_or_none()
    await message.answer("🚫 Ввод отменен.")
    if user:
        await send_profile_message(message.bot, message.chat.id, user, session)


@router.message(ProfileStates.waiting_for_birthdate)
async def profile_process_birthdate(
    message: Message, state: FSMContext, session: AsyncSession
):
    """Валидация и сохранение даты рождения"""
    text = (message.text or "").strip()
    try:
        born = datetime.strptime(text, "%d.%m.%Y").date()
        today = date.today()
        age = calculate_age(born)
        if born > today or age > 100 or age < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Неверный формат даты. Попробуй еще раз (ДД.ММ.ГГГГ):")
        return

    user = (
        await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
    ).scalar_one_or_none()
    if not user:
        await state.clear()
        await message.answer("❌ Профиль не найден.")
        return

    user.birth_date = born
    await session.commit()
    await state.clear()
    await message.answer(
        "✅ Дата рождения сохранена!",
        reply_markup=get_student_main_menu(),
    )

    # Показываем обновленный профиль (с фото / каптионом / текстом)
    await send_profile_message(message.bot, message.chat.id, user, session)


@router.callback_query(F.data == "profile_set_status")
async def profile_set_status(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
):
    """Кастомный статус — только с 10 уровня"""
    user = (
        await session.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
    ).scalar_one_or_none()

    if not user or user.level < 10:
        await callback.answer(
            text="🔒 Кастомный статус доступен строго с 10-го уровня!",
            show_alert=True,
        )
        return

    await state.set_state(ProfileStates.waiting_for_status)
    await callback.message.answer(
        "Введите ваш новый статус (не более 45 символов):",
        reply_markup=_profile_input_kb(),
    )
    await callback.answer()


@router.message(ProfileStates.waiting_for_status, F.text == CANCEL_PROFILE_INPUT_BTN)
async def profile_cancel_status(
    message: Message, state: FSMContext, session: AsyncSession
):
    """Отмена ввода статуса — возврат к профилю"""
    await state.clear()
    user = (
        await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
    ).scalar_one_or_none()
    await message.answer("🚫 Ввод отменен.")
    if user:
        await send_profile_message(message.bot, message.chat.id, user, session)


@router.message(ProfileStates.waiting_for_status)
async def profile_process_status(
    message: Message, state: FSMContext, session: AsyncSession
):
    """Валидация длины и сохранение кастомного статуса"""
    text = (message.text or "").strip()
    if len(text) > 45:
        await message.answer(
            "⚠️ Слишком длинно! Введите статус короче (не более 45 символов):"
        )
        return

    user = (
        await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
    ).scalar_one_or_none()
    if not user:
        await state.clear()
        await message.answer("❌ Профиль не найден.")
        return

    user.custom_status = text
    await session.commit()
    await state.clear()
    await message.answer("✅ Статус обновлен!")

    await send_profile_message(message.bot, message.chat.id, user, session)
