import logging
from typing import List, Tuple
from sqlalchemy import select
from sqlalchemy.orm import Session
from models import Trick, LevelExperience, XpPenalty, TrickLog, User

import math

logger = logging.getLogger(__name__)


async def calculate_single_trick(
    trick: Trick,
    cone_line: str,
    quantity: int,
    execution_multiplier: float,
    skater_level: int,
    db_session,
) -> Tuple[float, int]:
    """
    Рассчитывает спортивные баллы (points) и игровой опыт (XP) за ОДИН подход трюка.
    """
    # 1. Выбираем коэффициент дорожки
    line_coeff = 1.0
    if cone_line == "50":
        line_coeff = trick.coeff_50
    elif cone_line == "80":
        line_coeff = trick.coeff_80
    elif cone_line == "120":
        line_coeff = trick.coeff_120

    # 2. Расчет спортивных баллов
    # Базовый расчет: база * коэф_линии  * коэф_качества
    raw_points = trick.base_tech_points * line_coeff * execution_multiplier

    # этот бонус и для спорта, и для игры
    # Бонус за превышение базового количества конусов: +extra_bonus_pct × √(конусов сверх базы)
    # (не линейно: каждый следующий конус дает меньше, чем предыдущий)
    # сознательно не делаем штравы на отклонения в меньшую сторону - там не все так просто.
    # в данный момент всё индивидуально. Как решит тренер, параметром  execution_multiplier
    bonus_multiplier = 1.0
    if quantity > trick.base_quantity:
        extra_cones = quantity - trick.base_quantity
        bonus_pct = (
            math.sqrt(extra_cones) * trick.extra_bonus_pct
        )  # По умолчанию 0.05 (5%)
        bonus_multiplier += bonus_pct

    calculated_points = raw_points * bonus_multiplier

    # 3. Расчет игрового опыта (XP)
    raw_xp = (
        trick.base_xp * line_coeff * quantity * execution_multiplier * bonus_multiplier
    )

    # Проверка разницы уровней (Спортсмен vs Тир трюка)
    # Если уровень спортсмена выше тира трюка — применяем множитель штрафа из БД
    level_diff = skater_level - trick.trick_tier
    xp_multiplier = 1.0

    if level_diff > 0:
        penalty = await db_session.scalar(
            select(XpPenalty).where(XpPenalty.level_difference == level_diff)
        )
        if penalty:
            xp_multiplier = penalty.xp_multiplier

    calculated_xp = int(raw_xp * xp_multiplier)

    return round(calculated_points, 2), max(0, calculated_xp)


async def calculate_current_workout_totals(
    trick_logs: List[TrickLog], skater: User, db_session
) -> Tuple[float, int]:
    """
    Рассчитывает промежуточные или финальные итоги тренировки для конкретного спортсмена.
    Возвращает: (Средний спортивный балл, Суммарный игровой опыт за тренировку)
    """
    if not trick_logs:
        return 0.0, 0

    # Получаем требования под текущий уровень спортсмена из справочника уровней
    level_reqs = await db_session.get(LevelExperience, skater.level)
    min_categories = level_reqs.min_trick_categories if level_reqs else 3
    max_logs_for_xp = level_reqs.max_workout_logs if level_reqs else 10

    # Собираем все уникальные категории, которые спортсмен УЖЕ сделал на этой тренировке
    # Для этого нам понадобятся ID трюков из логов
    trick_ids = [log.trick_id for log in trick_logs]
    tricks_in_workout = (
        await db_session.scalars(select(Trick).where(Trick.id.in_(trick_ids)))
    ).all()

    trick_id_to_category = {t.id: t.category for t in tricks_in_workout}
    unique_categories = {
        trick_id_to_category[log.trick_id]
        for log in trick_logs
        if log.trick_id in trick_id_to_category
    }
    categories_count = len(unique_categories)

    # --- Группировка логов: связка (общий combo_id) дает ОДИН результат ---
    # Логи, входящие в связку (combo_id не None и не '-'), НЕ считаются одиночными
    # трюками и в results не попадают. Для каждой связки добавляется строго одна
    # запись: балл последнего шага (шаги НЕ суммируются) и XP, привязанный к
    # итоговому спортивному баллу связки.
    combo_steps = {}
    for log in trick_logs:
        if log.combo_id and log.combo_id != "-":
            combo_steps.setdefault(log.combo_id, []).append(log)

    trick_by_id = {t.id: t for t in tricks_in_workout}

    # Единый список результатов: (points, xp)
    results = [
        (log.calculated_points, log.calculated_xp)
        for log in trick_logs
        if not (log.combo_id and log.combo_id != "-")
    ]
    for steps in combo_steps.values():
        steps.sort(key=lambda l: l.combo_step or 0)
        # Балл связки = Result последнего шага (calculate_combo_step), шаги не суммируются
        combo_points = steps[0].calculated_points
        for next_log in steps[1:]:
            combo_points = calculate_combo_step(
                combo_points, next_log.calculated_points, db_session
            )
        combo_points = round(combo_points, 2)

        # XP связки привязан к ее итоговому спортивному баллу:
        # combo_xp = points * (sum(base_xp) / sum(base_tech_points)) * кол-во трюков
        sum_base_xp = sum(
            trick_by_id[s.trick_id].base_xp for s in steps if s.trick_id in trick_by_id
        )
        sum_base_tech = sum(
            trick_by_id[s.trick_id].base_tech_points
            for s in steps
            if s.trick_id in trick_by_id
        )
        raw_combo_xp = (
            combo_points
            * (sum_base_xp / sum_base_tech if sum_base_tech else 0.0)
            * len(steps)
        )

        # Штраф XP за уровень: используем тир самого "дорогого" (последнего) трюка связки
        last_trick = trick_by_id.get(steps[-1].trick_id)
        xp_multiplier = 1.0
        if last_trick:
            level_diff = skater.level - last_trick.trick_tier
            if level_diff > 0:
                penalty = await db_session.scalar(
                    select(XpPenalty).where(XpPenalty.level_difference == level_diff)
                )
                if penalty:
                    xp_multiplier = penalty.xp_multiplier

        combo_xp = int(raw_combo_xp * xp_multiplier)
        results.append((combo_points, max(0, combo_xp)))

    # --- 1. СПОРТИВНЫЙ РЕЖИМ (Расчет баллов) ---
    # Сортируем результаты по убыванию набранных баллов
    sorted_by_points = sorted(results, key=lambda r: r[0], reverse=True)

    # Выбираем до 8 лучших трюков/связок
    top_8 = sorted_by_points[:8]
    tricks_count = len(top_8)

    if tricks_count > 0:
        sum_points = sum(points for points, _ in top_8)
        avg_points = sum_points / tricks_count

        # Штраф 1: За нехватку трюков до 8 (по -3 балла за каждый отсутствующий)
        if tricks_count < 8:
            missing_tricks = 8 - tricks_count
            avg_points -= missing_tricks * 3.0

        # Штраф 2: За нехватку разнообразия категорий (-3 балла за каждую недостающую категорию)
        if categories_count < min_categories:
            missing_categories = min_categories - categories_count
            avg_points -= missing_categories * 3.0

        final_sport_score = round(avg_points, 2)
    else:
        final_sport_score = 0.0

    # --- 2. ИГРОВОЙ РЕЖИМ (Расчет опыта) ---
    # Сортируем результаты по убыванию набранного опыта
    sorted_by_xp = sorted(results, key=lambda r: r[1], reverse=True)

    # В зачет опыта идет не более max_workout_logs лучших трюков/связок
    top_xp = sorted_by_xp[:max_logs_for_xp]
    total_xp = sum(xp for _, xp in top_xp)

    # Штраф на опыт за невыполнение нормы по категориям трюков
    if categories_count < min_categories:
        # Если категорий меньше нормы, режем суммарный опыт на 10% за каждую недостающую категорию
        missing_categories = min_categories - categories_count
        penalty_multiplier = max(0.0, 1.0 - (missing_categories * 0.1))
        total_xp = int(total_xp * penalty_multiplier)

    return final_sport_score, total_xp


async def check_level_up(
    current_level: int, current_xp: int, db_session
) -> Tuple[int, bool]:
    """
    Проверяет, набрал ли игрок достаточно опыта для повышения уровня.
    Возвращает: (Новый уровень, Флаг апнулся ли уровень)
    """
    level_upgraded = False
    new_level = current_level

    while True:
        # Ищем требования для СЛЕДУЮЩЕГО уровня
        next_level_reqs = await db_session.get(LevelExperience, new_level + 1)
        if not next_level_reqs:
            break  # Достигнут максимальный уровень (например, 80)

        if current_xp >= next_level_reqs.required_xp:
            new_level += 1
            level_upgraded = True
        else:
            break

    return new_level, level_upgraded


# ==
# РАСЧЕТ КОМБО-СВЯЗОК
# =====================================================================


def combo_bonus_multiplier(diff_points: float) -> float:
    """Процентный множитель связки: table_value = 10 - |diff| трактуется как проценты.
    diff = 1 -> ×1.09, diff = 3 -> ×1.07 (7% как в исходной таблице).
    Если |diff| >= 10 — множитель 1.0 (бонуса нет). Знак не важен."""
    table_value = max(0.0, 10.0 - abs(diff_points))
    return 1.0 + table_value / 100.0


def calculate_combo_step(
    prev_result: float,
    next_trick_points: float,
    db_session=None,
) -> float:
    """
    Расчет Result следующего шага связки:
      diff        = Y - X (без округления, знак не важен)
      multiplier  = 1 + (10 - |diff|)%   # табличное значение как проценты: ×1.09 при diff=1
      Result      = (((X + Y) / 2) + (1.0 if Y >= X else 0.0)) * multiplier
    Если |diff| >= 10 — множитель равен 1 (бонуса нет).
    prev_result - полный Result предыдущего шага (для шага 2 это чистый балл 1-го трюка).
    Result последнего шага и есть стоимость связки - шаги НЕ суммируются.
    db_session оставлен для совместимости вызовов, больше не используется.
    """
    avg = (prev_result + next_trick_points) / 2
    bump = 1.0 if next_trick_points >= prev_result else 0.0
    return (avg + bump) * combo_bonus_multiplier(next_trick_points - prev_result)
