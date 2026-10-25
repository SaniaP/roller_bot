import logging
from typing import List, Tuple
from sqlalchemy import select
from sqlalchemy.orm import Session
from models import Trick, LevelExperience, XpPenalty, TrickLog, User

logger = logging.getLogger(__name__)

def calculate_single_trick(
    trick: Trick, 
    cone_line: str, 
    quantity: int, 
    execution_multiplier: float,
    skater_level: int,
    db_session: Session
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
    raw_points = trick.base_tech_points * line_coeff *  execution_multiplier

    # этот бонус и для спорта, и для игры
    # Бонус за превышение базового количества конусов (+5% за каждый конус сверху)
    bonus_multiplier = 1.0
    if quantity > trick.base_quantity:
        extra_cones = quantity - trick.base_quantity
        bonus_pct = extra_cones * trick.extra_bonus_pct  # По умолчанию 0.05 (5%)
        bonus_multiplier += bonus_pct
        
    calculated_points = raw_points * bonus_multiplier


    # 3. Расчет игрового опыта (XP)
    raw_xp = trick.base_xp * line_coeff * quantity * execution_multiplier * bonus_multiplier
    
    # Проверка разницы уровней (Спортсмен vs Тир трюка)
    # Если уровень спортсмена выше тира трюка — применяем множитель штрафа из БД
    level_diff = skater_level - trick.trick_tier
    xp_multiplier = 1.0
    
    if level_diff > 0:
        penalty = db_session.scalar(
            select(XpPenalty).where(XpPenalty.level_difference == level_diff)
        )
        if penalty:
            xp_multiplier = penalty.xp_multiplier

    calculated_xp = int(raw_xp * xp_multiplier)

    return round(calculated_points, 2), max(0, calculated_xp)


def calculate_current_workout_totals(
    trick_logs: List[TrickLog], 
    skater: User, 
    db_session: Session
) -> Tuple[float, int]:
    """
    Рассчитывает промежуточные или финальные итоги тренировки для конкретного спортсмена.
    Возвращает: (Средний спортивный балл, Суммарный игровой опыт за тренировку)
    """
    if not trick_logs:
        return 0.0, 0

    # Получаем требования под текущий уровень спортсмена из справочника уровней
    level_reqs = db_session.get(LevelExperience, skater.level)
    min_categories = level_reqs.min_trick_categories if level_reqs else 3
    max_logs_for_xp = level_reqs.max_workout_logs if level_reqs else 10

    # Собираем все уникальные категории, которые спортсмен УЖЕ сделал на этой тренировке
    # Для этого нам понадобятся ID трюков из логов
    trick_ids = [log.trick_id for log in trick_logs]
    tricks_in_workout = db_session.scalars(
        select(Trick).where(Trick.id.in_(trick_ids))
    ).all()
    
    trick_id_to_category = {t.id: t.category for t in tricks_in_workout}
    unique_categories = {trick_id_to_category[log.trick_id] for log in trick_logs if log.trick_id in trick_id_to_category}
    categories_count = len(unique_categories)

    # --- 1. СПОРТИВНЫЙ РЕЖИМ (Расчет баллов) ---
    # Сортируем логи по убыванию набранных баллов
    sorted_by_points = sorted(trick_logs, key=lambda x: x.calculated_points, reverse=True)
    
    # Выбираем до 8 лучших трюков
    top_8_logs = sorted_by_points[:8]
    tricks_count = len(top_8_logs)
    
    if tricks_count > 0:
        sum_points = sum(log.calculated_points for log in top_8_logs)
        avg_points = sum_points / tricks_count
        
        # Штраф 1: За нехватку трюков до 8 (по -3 балла за каждый отсутствующий)
        if tricks_count < 8:
            missing_tricks = 8 - tricks_count
            avg_points -= (missing_tricks * 3.0)
            
        # Штраф 2: За нехватку разнообразия категорий (-3 балла за каждую недостающую категорию)
        if categories_count < min_categories:
            missing_categories = min_categories - categories_count
            avg_points -= (missing_categories * 3.0)
            
        final_sport_score = max(0.0, round(avg_points, 2))
    else:
        final_sport_score = 0.0

    # --- 2. ИГРОВОЙ РЕЖИМ (Расчет опыта) ---
    # Сортируем логи по убыванию набранного опыта
    sorted_by_xp = sorted(trick_logs, key=lambda x: x.calculated_xp, reverse=True)
    
    # В зачет опыта идет не более max_workout_logs лучших трюков
    top_xp_logs = sorted_by_xp[:max_logs_for_xp]
    total_xp = sum(log.calculated_xp for log in top_xp_logs)
    
    # Штраф на опыт за невыполнение нормы по категориям трюков
    if categories_count < min_categories:
        # Если категорий меньше нормы, режем суммарный опыт пропорционально или штрафуем
        # Например, уменьшаем опыт на 20% за каждую недостающую категорию
        missing_categories = min_categories - categories_count
        penalty_multiplier = max(0.0, 1.0 - (missing_categories * 0.2))
        total_xp = int(total_xp * penalty_multiplier)

    return final_sport_score, total_xp


def check_level_up(current_level: int, current_xp: int, db_session: Session) -> Tuple[int, bool]:
    """
    Проверяет, набрал ли игрок достаточно опыта для повышения уровня.
    Возвращает: (Новый уровень, Флаг апнулся ли уровень)
    """
    level_upgraded = False
    new_level = current_level
    
    while True:
        # Ищем требования для СЛЕДУЮЩЕГО уровня
        next_level_reqs = db_session.get(LevelExperience, new_level + 1)
        if not next_level_reqs:
            break # Достигнут максимальный уровень (например, 80)
            
        if current_xp >= next_level_reqs.required_xp:
            new_level += 1
            level_upgraded = True
        else:
            break
            
    return new_level, level_upgraded