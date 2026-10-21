import logging
from models import Trick, LevelExperience, XpPenalty
#заглушка
logger = logging.getLogger(__name__)

def calculate_trick_xp_and_points(trick: Trick, quantity: int, coeff: float) -> tuple[int, float]:
    """
    Рассчитывает финальный опыт (XP) и тех. баллы за один подход трюка.
    Учитывает базовые параметры трюка и коэффициент дорожки (конусов).
    """
    # Базовая заглушка, которую мы распишем в Главе 4
    final_points = trick.base_tech_points * quantity * coeff
    final_xp = int(trick.base_xp * quantity * coeff)
    
    return final_xp, final_points

def check_workout_diversity_penalty(skater_level: int, categories_used: int, base_xp: int) -> int:
    """
    Проверяет правило разнообразия категорий. 
    Если категорий меньше нормы для текущего уровня — режет опыт.
    """
    # Логику штрафов за отсутствие разнообразия прикрутим сюда
    return base_xp