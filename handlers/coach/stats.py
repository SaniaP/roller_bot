"""Статистика: список учеников с сортировкой по уровню и профиль с разбивкой по тренировкам."""

import logging
from datetime import date, datetime
from html import escape

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
from sqlalchemy import select, func, or_, and_

from config import CHIEF_COACH_ID
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
    CoachStudent,
    Community,
    CommunityCoach,
)
from handlers.coach.helpers import (
    get_coach_menu_for,
    get_coach_students,
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


# =====================================================================
# ОБЛАСТИ ВИДИМОСТИ ТОПА (ЭТАП 5)
# =====================================================================

RATING_SCOPE_MY = "my"  # только ученики текущего тренера
RATING_SCOPE_COMMUNITY = "community"  # ученики всех тренеров его комьюнити
RATING_SCOPE_GLOBAL = "global"  # все подтвержденные спортсмены с тренерами


async def _global_students(session: AsyncSession) -> list[User]:
    """Фолбэк для списка профилей под рейтингом: все одобренные
    не-соло спортсмены (если у тренера пока нет своих учеников)."""
    return (
        (
            await session.execute(
                select(User)
                .where(
                    User.role == "student",
                    User.is_approved.is_(True),
                    User.is_solo.is_(False),
                )
                .order_by(User.level.desc(), User.xp.desc())
            )
        )
        .scalars()
        .all()
    )


async def _coach_id_by_tg(session: AsyncSession, telegram_id: int) -> int | None:
    """id тренера по telegram_id (или None)."""
    user = (
        await session.execute(select(User).where(User.telegram_id == telegram_id))
    ).scalar_one_or_none()
    if not user:
        return None
    if user.role == "coach":
        return user.id
    # Зритель: смотрим за студентом — берем тренера этого студента.
    if user.role == "viewer" and user.viewer_for_id:
        return await session.scalar(
            select(CoachStudent.coach_id).where(
                CoachStudent.student_id == user.viewer_for_id
            )
        )
    # Студент: берем его тренера из привязки CoachStudent.
    if user.role == "student":
        return await session.scalar(
            select(CoachStudent.coach_id).where(CoachStudent.student_id == user.id)
        )
    return None


async def _rating_students_for_scope(
    session: AsyncSession,
    scope: str,
    telegram_id: int,
) -> list[int] | None:
    """Возвращает список user_id спортсменов для выбранной области ТОПа.

    scope:
      my        — ученики текущего тренера (CoachStudent.coach_id);
      community — ученики всех тренеров одного Community с тренером;
      global    — все подтвержденные спортсмены с тренерами по всей базе.
    None — область неприменима (например, у зрителя нет тренера).
    """
    coach_id = await _coach_id_by_tg(session, telegram_id)

    if scope == RATING_SCOPE_MY:
        if not coach_id:
            return None
        # Только ученики текущего тренера. Соло-тренировки самого тренера
        # отсекаются на уровне Workout (is_solo / community_id) в build_rating_text,
        # поэтому в список рейтинга они не попадают вообще.
        ids = (
            (
                await session.execute(
                    select(CoachStudent.student_id).where(
                        CoachStudent.coach_id == coach_id
                    )
                )
            )
            .scalars()
            .all()
        )
        return ids or [0]

    if scope == RATING_SCOPE_COMMUNITY:
        if not coach_id:
            return None
        community_ids = (
            (
                await session.execute(
                    select(CommunityCoach.community_id).where(
                        CommunityCoach.coach_id == coach_id
                    )
                )
            )
            .scalars()
            .all()
        )
        if not community_ids:
            return [0]
        coach_ids = (
            (
                await session.execute(
                    select(CommunityCoach.coach_id).where(
                        CommunityCoach.community_id.in_(community_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        # Ученики всех тренеров комьюнити + сами тренеры, выступающие
        # в роли спортсменов за это комьюнити (is_athlete_mode).
        ids = (
            (
                await session.execute(
                    select(CoachStudent.student_id).where(
                        CoachStudent.coach_id.in_(coach_ids or [0])
                    )
                )
            )
            .scalars()
            .all()
        )
        athlete_coach_ids = (
            (
                await session.execute(
                    select(User.id).where(
                        User.role == "coach",
                        User.athlete_community_id.in_(community_ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        ids = list(set(ids) | set(athlete_coach_ids))
        return ids or [0]

    if scope == RATING_SCOPE_GLOBAL:
        # Все ученики с тренером + все тренеры-спортсмены (athlete mode).
        ids = (
            (
                await session.execute(
                    select(CoachStudent.student_id).distinct()
                )
            )
            .scalars()
            .all()
        )
        athlete_coach_ids = (
            (
                await session.execute(
                    select(User.id).where(
                        User.role == "coach",
                        User.athlete_community_id.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        ids = list(set(ids) | set(athlete_coach_ids))
        return ids or [0]

    return None


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


def _stats_keyboard(students: list[User]) -> InlineKeyboardMarkup:
    """Клавиатура 'Статистики': кнопки учеников."""
    builder = InlineKeyboardBuilder()
    for st in students:
        builder.button(
            text=f"{st.full_name} (Ур. {st.level})",
            callback_data=StatsAction(action="stats_student", student_id=st.id),
        )
    builder.adjust(1)
    return builder.as_markup()


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


# =====================================================================
# 📈 Рейтинги: универсальный рейтинг с интерактивными фильтрами
# =====================================================================


def _years_ago(base: date, years: int) -> date:
    """Дата 'base' минус N лет (с поправкой на 29 февраля)."""
    try:
        return base.replace(year=base.year - years)
    except ValueError:  # 29 февраля -> 28 февраля
        return base.replace(year=base.year - years, day=28)


async def build_rating_text(
    session: AsyncSession,
    sort: str,
    age: str,
    rank: str,
    limit: int | None = None,
    scope: str | None = None,
    telegram_id: int | None = None,
) -> str:
    """Универсальный текст рейтинга учеников с фильтрами сортировки,
    возраста и ранга. limit (например 10) — жесткое ограничение для гостей.

    scope (ЭТАП 5): my / community / global — область видимости ТОПа.
    Соло-спортсмены (is_solo=True) и соло-тренировки исключены всегда:
    в рейтинг попадают только спортсмены, имеющие привязку в CoachStudent."""
    query = select(User).where(
        User.is_approved == True,  # noqa: E712
        # Соло-игроки не участвуют в общих/клубных рейтингах
        User.is_solo.is_(False),
        # Спортсмены с тренером (привязка в CoachStudent) ИЛИ тренеры,
        # выступающие в роли спортсменов за комьюнити (athlete mode)
        or_(
            and_(
                User.role == "student",
                User.id.in_(select(CoachStudent.student_id).distinct()),
            ),
            and_(
                User.role == "coach",
                User.athlete_community_id.is_not(None),
            ),
        ),
    )

    # Область видимости ТОПа
    if scope and telegram_id:
        scope_ids = await _rating_students_for_scope(session, scope, telegram_id)
        if scope_ids is not None:
            query = query.where(User.id.in_(scope_ids or [0]))

    # Фильтр ранга
    if rank == "novice":
        query = query.where(User.title == "Новичок")

    # Фильтр возраста (birth_date == None попадает только в "all")
    if age != "all":
        today = date.today()
        if age == "kids":
            # Строго младше 8 лет (подпись «До 7 лет» — условная, чтобы не терять
            # детей 7..8 лет: раньше они выпадали из kids и teens одновременно)
            query = query.where(User.birth_date > _years_ago(today, 8))
        elif age == "teens":
            # От 8 до 17 лет включительно, без зазора с kids
            query = query.where(
                User.birth_date.between(_years_ago(today, 18), _years_ago(today, 8))
            )
        elif age == "adults":
            # 18+: строго старше 18 лет
            query = query.where(User.birth_date < _years_ago(today, 18))

    # Сортировка
    if sort == "exp":
        query = query.order_by(User.level.desc(), User.xp.desc())

    if limit:
        query = query.limit(limit)

    students = (await session.execute(query)).scalars().all()

    if not students:
        return "📈 <b>Рейтинг спортсменов</b>\n\nПока никого нет по выбранным фильтрам."

    # Для сортировки по очкам пересортировываем средствами Python по BP
    if sort == "score":
        scored = [
            (st, await _best_training_points(session, st.id) or 0.0) for st in students
        ]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        students = [st for st, _bp in scored]

    titles = {
        "exp": "по опыту",
        "score": "по очкам",
    }
    scope_titles = {
        RATING_SCOPE_MY: "👤 мой ТОП",
        RATING_SCOPE_COMMUNITY: "🏘 сообщество",
        RATING_SCOPE_GLOBAL: "🌍 общий рейтинг",
    }
    scope_part = f" • {scope_titles[scope]}" if scope in scope_titles else ""
    lines = [f"📈 <b>Рейтинг спортсменов</b> ({titles.get(sort, sort)}{scope_part})\n"]

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
        safe_name = escape(st.full_name or "Спортсмен")
        lines.append(f"{i}. 👤 <b>{safe_name}</b> - {st.level} LVL, BS : {bp}\n{bar}")

    if limit:
        lines.append(
            f"\n🔒 <i>Гостям и в режиме соло доступна только первая {limit}-ка. "
            "Зарегистрируйся, чтобы открыть полный рейтинг принять участие!</i>"
        )

    return "\n".join(lines)


def build_rating_keyboard(
    sort: str,
    age: str,
    rank: str,
    is_coach: bool = False,
    students_list: list[User] | None = None,
    scope: str | None = None,
) -> InlineKeyboardMarkup:
    """Клавиатура фильтров рейтинга (паттерн выбора дорожек: активный
    параметр помечается ✅). Для тренера снизу прикрепляются кнопки
    провала в профили учеников (_students_kb).
    scope — активная область видимости ТОПа (my/community/global)."""

    def mark(active: bool) -> str:
        return "✅ " if active else ""

    builder = InlineKeyboardBuilder()

    # Ряд 0 — область видимости ТОПа (только для тренера/ученика с тренером)
    if scope is not None:
        scopes = [
            (RATING_SCOPE_MY, "👤 Группа"),
            (RATING_SCOPE_COMMUNITY, "👥 Клуб"),
            (RATING_SCOPE_GLOBAL, "🌍 Общий"),
        ]
        builder.row(
            *[
                InlineKeyboardButton(
                    text=f"{mark(scope == val)}{label}",
                    callback_data=f"rating:{sort}:{age}:{rank}:{val}",
                )
                for val, label in scopes
            ]
        )
    # Ряд 1 — сортировка
    builder.row(
        InlineKeyboardButton(
            text=f"{mark(sort == 'exp')}🔥 По EXP",
            callback_data=f"rating:{'score' if sort == 'exp' else 'exp'}:{age}:{rank}",
        ),
        InlineKeyboardButton(
            text=f"{mark(sort == 'score')}🎯 По Очкам",
            callback_data=f"rating:{'exp' if sort == 'score' else 'score'}:{age}:{rank}",
        ),
    )
    # Ряд 2 — возраст
    ages = [("all", "Все"), ("kids", "До 8 лет"), ("teens", "8-17"), ("adults", "18+")]
    builder.row(
        *[
            InlineKeyboardButton(
                text=f"{mark(age == val)}{label}",
                callback_data=f"rating:{sort}:{val}:{rank}",
            )
            for val, label in ages
        ]
    )
    # Ряд 3 — ранг (временно отключен)
    # builder.row(
    #     InlineKeyboardButton(
    #         text=f"{mark(rank == 'all')}Все ранги",
    #         callback_data=f"rating:{sort}:{age}:all",
    #     ),
    #     InlineKeyboardButton(
    #         text=f"{mark(rank == 'novice')}Новички",
    #         callback_data=f"rating:{sort}:{age}:novice",
    #     ),
    # )

    kb = builder.as_markup()

    # Мульти-интерфейс тренера: кнопки провала в профили учеников
    if is_coach and students_list:
        student_kb = _students_kb(students_list)
        extra_rows = [
            (b.text, b.callback_data) for row in student_kb.inline_keyboard for b in row
        ]
        for text, cd in extra_rows:
            builder.row(InlineKeyboardButton(text=text, callback_data=cd))
        kb = builder.as_markup()
    return kb


@router.message(F.text == "📈 Рейтинги")
async def ratings(message: Message, session: AsyncSession):
    """Единая точка входа в рейтинги с разделением прав:
    гость — текстовый ТОП-10 без кнопок; ученик/зритель — фильтры;
    главный тренер — фильтры + кнопки профилей учеников."""
    tg_id = message.from_user.id
    user = (
        await session.execute(select(User).where(User.telegram_id == tg_id))
    ).scalar_one_or_none()

    # Сценарий А: абсолютный гость ИЛИ неодобренный пользователь
    # (заявка спортсмена/зрителя на рассмотрении) — только ТОП-10 текстом
    if user is None or not user.is_approved:
        text = await build_rating_text(session, "exp", "all", "all", limit=10)
        await message.answer(text, parse_mode="HTML")
        return

    # Соло-спортсмен видит ТОП-10 текстом (как гость) — общие рейтинги
    # с фильтрами ему недоступны, но ориентироваться в топе он может.
    if user.is_solo:
        text = await build_rating_text(session, "exp", "all", "all", limit=10)
        await message.answer(text, parse_mode="HTML")
        return

    # Сценарий В: главный тренер (по ТЕКУЩЕЙ роли в базе — в режиме
    # тестирования ученика он видит рейтинги как обычный пользователь)
    if user.role == "coach" and tg_id == CHIEF_COACH_ID:
        students = await get_coach_students(session, user.id) or await _global_students(
            session
        )
        text = await build_rating_text(
            session, "exp", "all", "all", scope=RATING_SCOPE_MY, telegram_id=tg_id
        )
        await message.answer(
            text,
            reply_markup=build_rating_keyboard(
                "exp",
                "all",
                "all",
                is_coach=True,
                students_list=students,
                scope=RATING_SCOPE_MY,
            ),
            parse_mode="HTML",
        )
        return

    # Сценарий Б: ученик или зритель — фильтры без кнопок профилей.
    # У одобренного пользователя доступен переключатель области ТОПа
    # (Группа/Клуб/Общий): у ученика с тренером — полная логика scope,
    # у остальных — клуб/общий через их же обработчики.
    # Для зрителя проверяем привязку его подопечного студента, а не самого зрителя
    student_id = user.viewer_for_id if user.role == "viewer" else user.id
    has_coach = (
        await session.scalar(
            select(CoachStudent.id)
            .where(CoachStudent.student_id == student_id)
            .limit(1)
        )
    ) is not None
    scope = RATING_SCOPE_MY if has_coach else RATING_SCOPE_GLOBAL
    text = await build_rating_text(
        session, "exp", "all", "all", scope=scope, telegram_id=tg_id
    )
    await message.answer(
        text,
        reply_markup=build_rating_keyboard(
            "exp", "all", "all", is_coach=False, scope=scope
        ),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("rating:"))
async def rating_filters(callback: CallbackQuery, session: AsyncSession):
    """Смена фильтров рейтинга «на лету» через edit_text.
    Формат callback: rating:<sort>:<age>:<rank>[:<scope>]"""
    parts = callback.data.split(":")
    if len(parts) == 4:
        _, sort, age, rank = parts
        scope = None
    elif len(parts) == 5:
        _, sort, age, rank, scope = parts
    else:
        await callback.answer("Некорректный фильтр", show_alert=True)
        return

    tg_id = callback.from_user.id
    user = (
        await session.execute(select(User).where(User.telegram_id == tg_id))
    ).scalar_one_or_none()
    is_coach = user is not None and user.role == "coach" and tg_id == CHIEF_COACH_ID
    # Неодобренный пользователь не должен уметь раскрывать полный рейтинг фильтрами
    if user is not None and not user.is_approved and not is_coach:
        text = await build_rating_text(session, sort, age, rank, limit=10)
        await callback.message.edit_text(
            text + "\n\n🔒 Полный рейтинг откроется после подтверждения тренером.",
            parse_mode="HTML",
        )
        await callback.answer()
        return

    # Область видимости: тренеру — my/community/global; ученику с тренером — тоже
    if is_coach:
        scope = scope or RATING_SCOPE_MY
    elif user is not None and user.is_solo:
        await callback.answer(
            "🚀 В одиночном режиме общие рейтинги недоступны.", show_alert=True
        )
        return
    else:
        # Для зрителя проверяем привязку его подопечного студента
        student_id = (
            user.viewer_for_id
            if user and user.role == "viewer"
            else (user.id if user else None)
        )
        has_coach = bool(
            student_id
            and (
                await session.execute(
                    select(CoachStudent)
                    .where(CoachStudent.student_id == student_id)
                    .limit(1)
                )
            ).scalar_one_or_none()
        )
        if scope and not has_coach:
            scope = None

    students_list = None
    if is_coach:
        students_list = await get_coach_students(
            session, user.id
        ) or await _global_students(session)

    text = await build_rating_text(
        session, sort, age, rank, scope=scope, telegram_id=tg_id
    )
    new_kb = build_rating_keyboard(
        sort, age, rank, is_coach=is_coach, students_list=students_list, scope=scope
    )
    await callback.message.edit_text(text, reply_markup=new_kb, parse_mode="HTML")
    await callback.answer()


@router.message(F.text == "📊 Статистика")
async def coach_stats(message: Message, session: AsyncSession):
    """Статистика — выборка ТОЛЬКО учеников текущего тренера (CoachStudent)."""
    coach_id = await _coach_id_by_tg(session, message.from_user.id)
    if not coach_id:
        await message.answer("⛔ Статистика доступна только тренерам.")
        return

    students = await get_coach_students(session, coach_id)

    if not students:
        await message.answer(
            "📊 <b>Статистика</b>\n\nУ вас пока нет учеников.",
            reply_markup=await get_coach_menu_for(session, coach_id),
            parse_mode="HTML",
        )
        return

    students.sort(key=lambda st: (st.level, st.xp), reverse=True)

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
        reply_markup=_stats_keyboard(students),
        parse_mode="HTML",
    )


@router.message(F.text == "🏆 Рейтинг")
async def coach_rating_entry(message: Message, session: AsyncSession):
    """Reply-кнопка '🏆 Рейтинг' у тренера: ТОП его учеников
    с переключателем области видимости (мой / сообщество / общий)."""
    coach_id = await _coach_id_by_tg(session, message.from_user.id)
    if not coach_id:
        await message.answer("⛔ Рейтинг доступен только тренерам.")
        return
    students = await get_coach_students(session, coach_id)
    text = await build_rating_text(
        session,
        "exp",
        "all",
        "all",
        scope=RATING_SCOPE_MY,
        telegram_id=message.from_user.id,
    )
    await message.answer(
        text,
        reply_markup=build_rating_keyboard(
            "exp",
            "all",
            "all",
            is_coach=True,
            students_list=students,
            scope=RATING_SCOPE_MY,
        ),
        parse_mode="HTML",
    )


async def _best_training_points(session: AsyncSession, student_id: int) -> float | None:
    """Лучший (максимальный) результат training_points спортсмена.

    В зачет идут только закрытые тренировки (STATUS_CLOSED):
      - обычные групповые (is_solo=False), в т.ч. тренировки любого тренера;
      - соло-тренировки тренеров-спортсменов (is_solo=True, но
        community_id заполнен историческим комьюнити выступления).
    Обычные соло-тренировки спортсменов (community_id IS NULL) не учитываются.
    """
    return await session.scalar(
        select(func.max(WorkoutRegistration.training_points))
        .where(
            WorkoutRegistration.student_id == student_id,
            Workout.status == STATUS_CLOSED,
            or_(
                Workout.is_solo.is_(False),
                Workout.community_id.is_not(None),
            ),
        )
        .join(Workout, Workout.id == WorkoutRegistration.workout_id)
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


def _result_mark(note: str | None) -> str:
    """Пометка в конце строки результата:
    🏅✨ — в зачете очков и опыта; 🏅 — только очки; ✨ — только опыт."""
    if note in ("ok",):
        return " 🏅✨"
    if note == "sport":
        return " 🏅"
    if note == "game":
        return " ✨"
    return ""


def _mult_note(multiplier: float | None) -> str:
    """Добавка про качество исполнения: (-30%) при снижении, кач. X при повышении."""
    if not multiplier or multiplier == 1.0:
        return ""
    if multiplier < 1.0:
        return f" • (-{(1.0 - multiplier) * 100:.0f}%)"
    return f" • кач. {multiplier:.2f}"


async def _level_reqs_at_workout(
    student: User, workout: Workout, session: AsyncSession
) -> LevelExperience | None:
    """LevelExperience на момент тренировки.

    Уровень на дату тренировки восстанавливается ретроспективно:
    суммируем experience закрытых тренировок, ПРЕДШЕСТВУЮЩИХ данной
    (без самой тренировки — её XP начисляется в момент закрытия,
    а нормы действуют на старт тренировки), и находим уровень из
    справочника. Для активной тренировки — требования текущего уровня.
    """
    if workout.status != STATUS_CLOSED:
        return await session.get(LevelExperience, student.level)

    cum_xp = (
        await session.scalar(
            select(func.coalesce(func.sum(WorkoutRegistration.training_xp), 0))
            .join(Workout, Workout.id == WorkoutRegistration.workout_id)
            .where(
                WorkoutRegistration.student_id == student.id,
                Workout.status == STATUS_CLOSED,
                or_(
                    Workout.date < workout.date,
                    and_(Workout.date == workout.date, Workout.id < workout.id),
                ),
            )
        )
    ) or 0

    level = 1
    reqs_by_level: dict[int, LevelExperience] = {}
    for le in (
        (await session.execute(select(LevelExperience).order_by(LevelExperience.level)))
        .scalars()
        .all()
    ):
        reqs_by_level[le.level] = le
        if le.required_xp <= cum_xp:
            level = max(level, le.level)

    # Фолбэк на текущий уровень, если справочник неполный
    return reqs_by_level.get(level) or await session.get(LevelExperience, student.level)


def build_workout_log_text(
    workout: Workout,
    reg: WorkoutRegistration,
    student: User,
    logs: list[tuple[TrickLog, Trick]],
    level_reqs: LevelExperience | None,
    combo_exp: dict[str, int] | None = None,
) -> str:
    """Детальная разбивка одной тренировки: шапка + зачет + штрафы +
    связки блоками + одиночные трюки."""
    date_str = workout.date.strftime("%d.%m.%Y") if workout.date else "—"
    status_str = "🟢 Идет сейчас" if workout.status == STATUS_ACTIVE else "🏁 Завершена"
    parts = [f"🛹 <b>Тренировка №{workout.id}</b> • {date_str} ({status_str})"]

    if not logs:
        parts.append("Трюки не записаны.")
        return "\n".join(parts)

    # Определяем зачет по правилам калькулятора (в т.ч. результат связки как единое целое)
    notes, penalties, combo_in_game, combo_in_sport, penalty_info = _workout_breakdown(
        logs, level_reqs, combo_exp
    )

    # --- Строка очков: «база − штраф» показывается только при штрафе по очкам
    points_penalty = penalty_info.get("points_penalty", 0.0) if penalty_info else 0.0
    if workout.status == STATUS_ACTIVE:
        # Активная тренировка: живой пересчет по формулам калькулятора.
        # База = сумма лучших 8 очков / 8 (недостающие слоты дают 0),
        # штраф = 3.0 за каждую недостающую категорию,
        # опыт = сумма лучших 5 трюков/связок по EXP.
        sport_results: list[float] = []
        xp_results: list[int] = []
        combo_steps: dict[str, list[TrickLog]] = {}
        for log, _t in logs:
            if log.combo_id:
                combo_steps.setdefault(log.combo_id, []).append(log)
            else:
                sport_results.append(log.calculated_points)
                xp_results.append(log.calculated_xp)
        for cid, steps in combo_steps.items():
            steps.sort(key=lambda l: l.combo_step or 0)
            last = steps[-1]
            combo_points = last.calculated_points + (last.combo_bonus or 0.0)
            combo_xp = (combo_exp or {}).get(cid) or sum(s.calculated_xp for s in steps)
            # Как в калькуляторе: КАЖДЫЙ шаг связки independently участвует
            # в топ-8 по очкам, плюс итог связки отдельной записью;
            # в опыте шаги не участвуют лично — только агрегат связки.
            for s in steps:
                sport_results.append(s.calculated_points)
            sport_results.append(combo_points)
            xp_results.append(combo_xp)

        sport_results.sort(reverse=True)
        base_points = sum(sport_results[:8]) / 8.0
        live_penalty = 0.0
        categories_count = len({t.category for _l, t in logs})
        if level_reqs:
            missing = max(0, level_reqs.min_trick_categories - categories_count)
            live_penalty = missing * 3.0
        live_score = round(base_points - live_penalty, 2)

        xp_results.sort(reverse=True)
        live_xp = sum(xp_results[: (level_reqs.max_workout_logs if level_reqs else 10)])

        pts_line = f"🏅 {live_score:.2f}"
        if live_penalty:
            pts_line += f" (- штраф {live_penalty:.1f})"
        parts.append(f"{pts_line} | ✨ +{live_xp} EXP")
    else:
        pts_line = f"🏅 {reg.training_points:.2f} очк."
        if points_penalty:
            pts_line += f" (база {reg.training_points + points_penalty:.2f} − штраф {points_penalty:.1f})"
        parts.append(f"{pts_line} | ✨ +{reg.training_xp} EXP")

    # --- Строка зачета: слоты опыта, слоты очков, категории
    max_logs = level_reqs.max_workout_logs if level_reqs else 10
    min_categories = level_reqs.min_trick_categories if level_reqs else 3
    xp_used = penalty_info.get("xp_used", 0) if penalty_info else 0
    sport_used = penalty_info.get("sport_used", 0) if penalty_info else 0
    categories_count = penalty_info.get("categories_count", 0) if penalty_info else 0

    def check(done: int, total: int) -> str:
        return "✅" if done >= total else "⚠️"

    parts.append(
        f"Зачет: ✨ {xp_used}/{max_logs} {check(xp_used, max_logs)} • "
        f"🏅 {sport_used}/8 {check(sport_used, 8)} • "
        f"Категории: {categories_count}/{min_categories} {check(categories_count, min_categories)}"
    )

    # --- Блок штрафов: только при наличии нарушений
    if penalties:
        parts.append("⚠️ Штрафы:")
        parts.extend(f"• {p}" for p in penalties)

    # Группируем: связки (combo_id) блоками, одиночные трюки — списком (порядок лога)
    combos: dict[str, list[tuple[TrickLog, Trick]]] = {}
    singles: list[tuple[TrickLog, Trick]] = []
    for pair in logs:
        log, _t = pair
        if log.combo_id:
            combos.setdefault(log.combo_id, []).append(pair)
        else:
            singles.append(pair)

    # --- Связки: порядковый номер вместо технического id
    for num, (cid, steps) in enumerate(combos.items(), start=1):
        steps_sorted = sorted(steps, key=lambda p: p[0].combo_step or 0)
        last_log, _last_trick = steps_sorted[-1]
        total = last_log.calculated_points + (last_log.combo_bonus or 0.0)
        combo_xp = (combo_exp or {}).get(cid) or sum(
            log.calculated_xp for log, _t in steps_sorted
        )
        # Пометка связки: очки сидят на последнем шаге, опыт — на всех шагах
        in_combo_sport = combo_in_sport.get(cid, False)
        in_combo_game = combo_in_game.get(cid, False)
        if in_combo_sport and in_combo_game:
            mark = " 🏅✨"
        elif in_combo_sport:
            mark = " 🏅"
        elif in_combo_game:
            mark = " ✨"
        else:
            mark = ""
        parts.append(
            f"\n🧩 <b>Связка №{num}</b> — {total:.2f} очк. | +{combo_xp} EXP{mark}"
        )
        for i, (log, trick) in enumerate(steps_sorted, start=1):
            bonus = log.combo_bonus or 0.0
            delta = f" (+{bonus:.2f})" if bonus > 0 else ""
            pts = f"{log.calculated_points:.2f}"
            # Первая строка: номер + название трюка + зачетная пометка
            # (шаг связки может самостоятельно попасть в топ-8 по очкам)
            parts.append(f"{i}. {trick.name}{_result_mark(notes.get(log.id))}")
            # Вторая строка: параметры исполнения и результат
            parts.append(
                f"{log.quantity} кон. ({log.cone_line})"
                f"{_mult_note(log.execution_multiplier)} — {pts}{delta} очк."
            )

    # --- Одиночные трюки
    if singles:
        parts.append("\n🎯 <b>Одиночные трюки:</b>")
        for log, trick in singles:
            pts = f"{log.calculated_points:.1f}"
            # Первая строка: маркер + название трюка + зачетная пометка
            parts.append(f"• {trick.name}{_result_mark(notes.get(log.id))}")
            # Вторая строка: параметры исполнения и результат
            parts.append(
                f"{log.quantity} кон. ({log.cone_line})"
                f"{_mult_note(log.execution_multiplier)} — {pts} очк. "
                f"| +{log.calculated_xp} EXP"
            )

    parts.append("\n🏅 — в зачет очков | ✨ — в зачет опыта")
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


def _workout_breakdown(
    pairs: list[tuple[TrickLog, Trick]],
    level_reqs: LevelExperience | None,
    combo_exp: dict[str, int] | None = None,
) -> tuple[dict[int, str], list[str], dict[str, bool]]:
    """Разбивка логов тренировки по правилам калькулятора.

    - Спортивный режим: в зачет идут 8 лучших трюков по calculated_points.
    - Игровой режим: в зачет идут не более max_workout_logs лучших трюков по calculated_xp.

    Возвращает:
        (log_notes, penalties, combo_in_game, combo_in_sport, penalty_info) — где
        log_notes: {log_id: "добавка к строке"}, penalties: список текстов штрафов,
        combo_in_game: {combo_id: bool} — вошла ли связка в зачет опыта,
        combo_in_sport: {combo_id: bool} — вошла ли связка (агрегат) в зачет очков,
        penalty_info: агрегаты для шапки отчета (штраф по очкам, занятые слоты,
        категории) или None, если логов нет.
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

    # Запись расширенного списка: (points, xp, p_ids, xp_ids, kind, combo_id)
    results: list[tuple[float, int, list[int], list[int], str, object]] = []
    for log, _t in pairs:
        # Шаги связок в зачете опыта не участвуют лично — их опыт идет
        # только через агрегат связки (как в calculator.py: xp_results
        # = одиночные трюки + одна запись на связку)
        xp_ids = [] if log.combo_id else [log.id]
        kind = "step" if log.combo_id else "single"
        cid = log.combo_id if log.combo_id else None
        results.append(
            (log.calculated_points, log.calculated_xp, [log.id], xp_ids, kind, cid)
        )
    for cid, steps in combo_steps.items():
        steps.sort(key=lambda l: l.combo_step or 0)
        last = steps[-1]
        combo_points = last.calculated_points + (last.combo_bonus or 0.0)
        combo_xp = (combo_exp or {}).get(cid) or sum(s.calculated_xp for s in steps)
        # очки связки «сидят» на последнем шаге, опыт — на всех шагах
        results.append(
            (combo_points, combo_xp, [last.id], [s.id for s in steps], "combo", cid)
        )

    # Зачет спорта: топ-8 результатов по очкам (шаги связок участвуют в очках)
    step_sport_ids: set[int] = set()  # шаги/одиночные, зачётные СВОИМИ очками
    combo_sport_ids: set[str] = set()  # combo_id, чей АГРЕГАТ в топ-8
    for _p, _x, p_ids, _xp_ids, kind, cid in sorted(
        results, key=lambda r: r[0], reverse=True
    )[:8]:
        if kind == "combo":
            combo_sport_ids.add(cid)
        else:
            step_sport_ids.update(p_ids)
    # Зачет игры: топ max_workout_logs результатов по EXP.
    # Шаги связок в конкурсе опыта не участвуют вовсе (xp_ids пуст) —
    # их опыт идет только через агрегат связки, они не должны занимать слоты.
    xp_ranking = [r for r in results if r[3]]
    in_game_ids: set[int] = set()
    for _p, _x, _p_ids, xp_ids, _kind, _cid in sorted(
        xp_ranking, key=lambda r: r[1], reverse=True
    )[:max_logs]:
        in_game_ids.update(xp_ids)

    notes: dict[int, str] = {}
    for log, _trick in pairs:
        in_sport = log.id in step_sport_ids
        in_game = log.id in in_game_ids
        # Элементы связки не попадают в зачет опыта лично — только через агрегат связки.
        if log.combo_id:
            in_game = False
        if in_sport and in_game:
            notes[log.id] = "ok"  # в зачете и очков, и опыта
        elif in_sport:
            notes[log.id] = "sport"  # в зачете очков, но не опыта
        elif in_game:
            notes[log.id] = "game"  # в зачете опыта, но не очков
        else:
            notes[log.id] = "out"  # полностью вне зачета

    penalties: list[str] = []
    penalty_info: dict | None = None
    if pairs:
        categories_count = len({trick.category for _log, trick in pairs})
        missing_categories = max(0, min_categories - categories_count)
        points_penalty = missing_categories * 3.0

        if missing_categories:
            cat_points = missing_categories * 3.0
            penalties.append(
                f"Недобор категорий: -{cat_points:.1f} очк. (сделано {categories_count} из {min_categories} обязательных)"
            )
            penalties.append(
                f"Недобор категорий: -{missing_categories * 10}% EXP (сделано {categories_count} из {min_categories} обязательных)"
            )

        penalty_info = {
            "points_penalty": points_penalty,
            "missing_categories": missing_categories,
            "categories_count": categories_count,
            "xp_used": 0,
            "sport_used": 0,
        }

    # Связка считается вошедшей в зачет опыта, если в топе опыта находится
    # хотя бы один ее шаг (опыт связки «сидит» на всех шагах)
    combo_in_game: dict[str, bool] = {}
    for cid, steps in combo_steps.items():
        combo_in_game[cid] = any(s.id in in_game_ids for s in steps)

    # Связка считается вошедшей в зачет очков, если её агрегат (Result последнего шага)
    # попал в топ-8 по очкам. Агрегат «сидит» на последнем шаге (p_ids = [last.id]).
    combo_in_sport: dict[str, bool] = {}
    for cid, steps in combo_steps.items():
        combo_in_sport[cid] = cid in combo_sport_ids

    # Заполняем занятые слоты зачета.
    if penalty_info is not None:
        penalty_info["xp_used"] = min(max_logs, len(xp_ranking))
        penalty_info["sport_used"] = min(8, len(results))

    return notes, penalties, combo_in_game, combo_in_sport, penalty_info


async def build_student_profile_text(student: User, session: AsyncSession) -> str:
    """Полный текст профиля ученика: шапка с BP и баром + разбивка по тренировкам.
    Используется и тренерской статистикой, и кнопкой «Мой профиль» у ученика"""
    # Получаем шапку, где содержатся Имя, Уровень, Рекорд и Прогресс-бар
    progress = await _progress_line(student, session)

    # Формируем строку параметров (Возраст и Статус)
    if student.birth_date:
        age = calculate_age(student.birth_date)
        if age > 18:
            age_str = "📅 <b>Возраст:</b> 18+"
        else:
            age_str = f"📅 <b>Возраст:</b> {age} лет"
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
        # 🔥 ставится по флагу, зафиксированному при закрытии тренировки
        lvl_up_note = (
            " 🔥" if workout.status == STATUS_CLOSED and reg.caused_level_up else ""
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


TELEGRAM_CAPTION_LIMIT = 1024


async def _edit_profile_message(message: Message, text: str, reply_markup):
    """Редактирует сообщение профиля: у фото-сообщения меняем caption, у текстового — text."""
    if message.photo:
        caption = fit_telegram_text(text, TELEGRAM_CAPTION_LIMIT)
        await message.edit_caption(
            caption=caption, reply_markup=reply_markup, parse_mode="HTML"
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

    # Требования уровня на момент тренировки, а не текущие
    level_reqs = await _level_reqs_at_workout(student, workout, session)
    combo_exp = await _combo_exp_map(logs, student, session)
    text = build_workout_log_text(workout, reg, student, logs, level_reqs, combo_exp)

    # 🔥 — по флагу, зафиксированному при закрытии тренировки
    if workout.status == STATUS_CLOSED and reg.caused_level_up:
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
    """Возврат к списку учеников (только ученики текущего тренера)"""
    coach_id = await _coach_id_by_tg(session, callback.from_user.id)
    students = (
        await get_coach_students(session, coach_id)
        if coach_id
        else await _global_students(session)
    )
    students.sort(key=lambda st: (st.level, st.xp), reverse=True)

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

    # file_id в Telegram валиден только для того бота, который его получил.
    # Если профиль шлет другой бот (основной/тестовый), кэш чужой и Telegram
    # отвечает «wrong file identifier» — сбрасываем и кэшируем заново текущим ботом.
    if file_id:
        try:
            await bot.send_photo(chat_id=chat_id, photo=file_id)
        except Exception as e:
            logger.warning(
                f"Кэш аватарки невалиден для этого бота (user {user.id}): {e}. "
                "Пересоздаю кэш текущим ботом."
            )
            file_id = None
            user.photo_file_id = None
            await session.commit()

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
    # Зрителю не меняли меню на студенческое: возвращаем его собственное
    if user.role == "viewer":
        from keyboards.common_kb import get_guest_menu

        await message.answer(
            "✅ Дата рождения сохранена!",
            reply_markup=get_guest_menu(is_viewer=True),
        )
    else:
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
