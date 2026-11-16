"""
Скрипт для симуляции N будущих тренировок ученика.
Берёт ученика из БД, применяет заданные тренировочные шаблоны,
показывает TrickLog и прогрессию LEVEL/XP.

LevelExperience и XpPenalty загружаются из БД (с фолбэком на словари).
"""

import os
import sys
import math
from dataclasses import dataclass
from typing import List, Optional

# --- Настройка путей ---
_here = os.path.dirname(os.path.abspath(__file__))
_root = _here
while _root != os.path.dirname(_root):
    if os.path.exists(os.path.join(_root, "models.py")):
        break
    _root = os.path.dirname(_root)
if _root not in sys.path:
    sys.path.insert(0, _root)
os.chdir(_root)

import asyncio
from sqlalchemy import select
from database import async_session
import models

# ============================================================
# === СПРАВОЧНИКИ (загружаются из БД, словари — фолбэк) =======
# ============================================================

# Фолбэк LevelExperience: level -> (required_xp, max_workout_logs, min_trick_categories)
LEVEL_EXPERIENCE_FALLBACK = {
    1:   (0,      5,  1),
    2:   (200,    5,  1),
    3:   (600,    5,  1),
    4:   (1200,   5,  1),
    5:   (2000,   5,  2),
    6:   (3000,   5,  2),
    7:   (4200,   5,  2),
    8:   (5600,   5,  2),
    9:   (8400,   5,  2),
    10:  (12200,   7, 4),
    11:  (17000,  7, 4),
    12:  (22800,  8, 4),
    13:  (30000,  8, 4),
    14:  (38000,  9, 4),
    15:  (46200,  9, 4),
    16:  (24000,  10, 4),
    17:  (27200,  10, 4),
    18:  (30600,  12, 4),
    19:  (34200,  12, 4),
    20:  (38000,  15, 4),
    21:  (42000,  15, 5),
    22:  (46200,  15, 5),
    23:  (50600,  15, 5),
    24:  (55200,  15, 5),
    25:  (60000,  15, 5),
    26:  (65000,  15, 5),
    27:  (70200,  15, 5),
    28:  (75600,  15, 5),
    29:  (81200,  15, 5),
    30:  (87000,  15, 5),
    31:  (93000,  15, 5),
    32:  (99200,  15, 5),
    33:  (105600, 15, 5),
    34:  (112200, 15, 5),
    35:  (119000, 15, 5),
    36:  (126000, 15, 5),
    37:  (133200, 15, 5),
    38:  (140600, 15, 5),
    39:  (148200, 15, 5),
    40:  (156000, 15, 5),
    41:  (164000, 15, 5),
    42:  (172200, 15, 5),
    43:  (180600, 15, 5),
    44:  (189200, 15, 5),
    45:  (198000, 15, 5),
    46:  (207000, 15, 5),
    47:  (216200, 15, 5),
    48:  (225600, 15, 5),
    49:  (235200, 15, 5),
    50:  (245000, 15, 5),
}

# Фолбэк XpPenalty: level_difference -> xp_multiplier
XP_PENALTIES_FALLBACK = {
    1:  0.90,
    2:  0.80,
    3:  0.70,
    4:  0.60,
    5:  0.50,
    6:  0.40,
    7:  0.30,
    8:  0.20,
    9:  0.10,
    # >9 -> 0.01 (захардкожено в логике)
}

# Глобальные кэши, заполняются при старте из БД
LEVEL_EXPERIENCE: dict[int, tuple[int, int, int]] = {}
XP_PENALTIES: dict[int, float] = {}

# ============================================================
# === ПЕРЕОПРЕДЕЛЕНИЕ ПАРАМЕТРОВ ТРЮКОВ (для тестов) =========
# ============================================================
# Словарь: trick_id -> {"trick_tier": int, "base_xp": int, "extra_bonus_pct": float}
# Если trick_id отсутствует — берутся значения из БД.
# Позволяет менять TIER, BASE_XP и EXTRA_BONUS_PCT без изменения БД.
TRICK_OVERRIDES = {
#     # ID: {"trick_tier": TIER, "base_xp": XP, "extra_bonus_pct": PCT}
#     2:   {"trick_tier": 45, "base_xp": 46*3},      # BUTTERFLY (ультра-сложный технический)
#     4:   {"trick_tier": 38, "base_xp": 39*2.5},      # BACK COBRA
#     5:   {"trick_tier": 36, "base_xp": 54},      # COBRA
#     8:   {"trick_tier": 25, "base_xp": 31},          # Z-EAGLE (базовый накат, без изменений)
#     9:   {"trick_tier": 23, "base_xp": 29},          # TT SPECIAL (без изменений)
#     12:  {"trick_tier": 19, "base_xp": 25},          # EAGLE / кораблик (линейная база, без изменений)
#     36:  {"trick_tier": 26, "base_xp": 32*2 },      # FOOTGUN (сидячий трюк средней сложности)
#     41:  {"trick_tier": 8,  "base_xp": 18},          # 5 WHEELS SITTING / м. машинка (без изменений)
#     54:  {"trick_tier": 27, "base_xp": 32},      # WIPER
#     55:  {"trick_tier": 23, "base_xp": 29},      # FOOTSPIN JUMP
#     66:  {"trick_tier": 37, "base_xp": 39*2},      # BACK Т one wheel (сложный ванвилл спиной)
#     67:  {"trick_tier": 31, "base_xp": 36},      # FLAT SHIFTS / COUNTERSHIFTS SERIES
#     68:  {"trick_tier": 30, "base_xp": 62},      # H one wheel / одно колесо (ванвилл, мощный буст)
#     71:  {"trick_tier": 21, "base_xp": 24},          # TT BACK CROSS (ваш линейный ориентир, без изменений!)
#     73:  {"trick_tier": 19, "base_xp": 23},          # TT BACK SNAKE (без изменений)
#     75:  {"trick_tier": 17, "base_xp": 23},          # TT CROSS (без изменений)
#     78:  {"trick_tier": 15, "base_xp": 21},          # HT SNAKE / 2 колеса (линейная база, без изменений)
#     79:  {"trick_tier": 11, "base_xp": 18},          # BACK ONE FOOT (без изменений)
#     80:  {"trick_tier": 6,  "base_xp": 15},          # ONE FOOT (без изменений)
#     88:  {"trick_tier": 48, "base_xp": 49*3},      # ONE CONE SEVEN int/ext (ультра-топ, высокий буст)
#     94:  {"trick_tier": 35, "base_xp": 37*2},    # KOREAN SPIN (вращение, отличный баланс к длине)
#     97:  {"trick_tier": 20, "base_xp": 26},          # BACK ТТ/ НТ пицца-фри (без изменений)
#     101: {"trick_tier": 17, "base_xp": 23},          # ТТ/ НТ пицца-фри (без изменений)
}
#TRICK_OVERRIDES = {}

# ============================================================
# === ТРЕНИРОВОЧНЫЕ ШАБЛОНЫ ==================================
# ============================================================
# Определите список трюков, которые ученик делает на КАЖДОЙ тренировке.
# Каждый элемент: (trick_id, cone_line, quantity, execution_multiplier, combo_id?, combo_step?)
# combo_id=None -> одиночный трюк; combo_id="combo1" -> шаг связки

YULIA_MINI_WORKOUT_TEMPLATE = [
    # (trick_id, cone_line, quantity, execution_multiplier, combo_id, combo_step)
    (2, "80", 4, 1.0, None, None),    # BUTTERFLY
    (4, "120", 14, 1.0, None, None),    # BACK COBRA
    (5, "80", 20, 1.0, None, None),    # COBRA
    (8, "80", 20, 1.0, None, None),   # Z-EAGLE
    (9, "80", 20, 1.0, None, None),   # TT SPECIAL
    (12, "80", 20, 1.0, None, None),  # EAGLE / кораблик
    (36, "80", 10, 1.0, None, None),   # FOOTGUN
    (41, "80", 20, 1.0, None, None),  # 5 WHEELS SITTING / м. машинка
    (54, "80", 20, 1.0, None, None),   # WIPER
    (55, "80", 4, 1.0, None, None),   # FOOTSPIN JUMP
    (66, "80", 11, 1.0, None, None),   # BACK Т one wheel
    (67, "80", 20, 1.0, None, None),   # FLAT SHIFTS / COUNTERSHIFTS SERIES
    (68, "80", 20, 1.0, None, None),   # H one wheel / одно колесо
    (71, "80", 20, 1.0, None, None),  # TT BACK CROSS
    (73, "80", 20, 1.0, None, None),  # TT BACK SNAKE
    (75, "80", 20, 1.0, None, None),  # TT CROSS
    (78, "80", 20, 1.0, None, None),  # HT SNAKE / 2 колеса
    (79, "80", 20, 1.0, None, None),  # BACK ONE FOOT
    (80, "80", 20, 1.0, None, None),  # ONE FOOT
    (88, "80", 6, 1.0, None, None),   # ONE CONE SEVEN int/ext
    (88, "80", 3, 1.0, None, None),   # ONE CONE SEVEN int/ext
    (94, "80", 3, 1.0, None, None),   # KOREAN SPIN
    (94, "80", 8, 1.0, None, None), 
    (97, "80", 20, 1.0, None, None),  # BACK ТТ/ НТ пицца-фри
    (101, "80", 20, 1.0, None, None), # ТТ/ НТ пицца-фри
    (74, "80", 20, 1.0, None, None),       # HT BACK SNAKE
]


AMELI_DATA = [
    # --- Связка 1 (id c4ad6759…) ---
    (5, "80", 14, 1.0, "combo_c4ad", 1),
    (94, "80", 3, 1.0, "combo_c4ad", 2),

    # --- Связка 2 (id 2985a50a…) ---
    (5, "80", 15, 1.0, "combo_2985", 1),
    (91, "80", 5, 1.0, "combo_2985", 2),
    (69, "80", 4, 1.0, "combo_2985", 3),
    (67, "80", 5, 1.0, "combo_2985", 4),
    (96, "80", 3, 1.0, "combo_2985", 5),

    # --- Одиночные трюки ---
    (78, "80", 20, 1.0, None, None),
    (74, "80", 20, 1.0, None, None),
    (71, "80", 15, 1.0, None, None),
    (5, "80", 20, 0.99, None, None),
    (69, "80", 5, 1.0, None, None),
    (67, "80", 10, 1.0, None, None),
    (94, "80", 10, 0.90, None, None),
]

ULIA_DATA = [
    (68, "80", 14, 1.0, None, None),   # H one wheel — ванвилл на пятке (одно колесо)
    (69, "80", 15, 1.0, None, None),    # Т one wheel — ванвилл на носке (одно колесо)
    (67, "80", 20, 0.85, None, None),  # FLAT SHIFTS — линейные переходы/сдвиги колес (качество 0.85)
    (71, "80", 20, 1.0, None, None),   # TT BACK CROSS — кресты спиной на носках
    (72, "80", 19, 1.0, None, None),   # HT BACK CROSS — кресты спиной пятка-носок
    (5, "120", 12, 1.0, None, None),   # COBRA — кобра лицом (элемент категории others)
    (93, "120", 6, 1.0, None, None),   # ONE CONE KOREAN SPIN — корейский спин на одном конусе
    (93, "120", 4, 1.0, None, None),   # ONE CONE KOREAN SPIN — корейский спин на одном конусе (повторный подход)
    (92, "120", 4, 1.0, None, None),   # BACK KOREAN SPIN — корейский спин спиной
    (4, "80", 8, 0.50, None, None),    # BACK COBRA — кобра спиной (качество 0.50)
    (41, "120", 14, 1.0, None, None),  # 5 WHEELS SITTING / м. машинка
]
VERONIKA_DATA = [
    (68, "80", 14, 1.0, None, None),   # H one wheel — ванвилл на пятке (одно колесо)
    (69, "80", 9, 1.0, None, None),    # Т one wheel — ванвилл на носке (одно колесо)
    (67, "80", 20, 0.85, None, None),  # FLAT SHIFTS — линейные переходы/сдвиги колес (качество 0.85)
    (71, "80", 20, 1.0, None, None),   # TT BACK CROSS — кресты спиной на носках
    (72, "80", 19, 1.0, None, None),   # HT BACK CROSS — кресты спиной пятка-носок
    (5, "120", 12, 1.0, None, None),   # COBRA — кобра лицом (элемент категории others)
    (93, "120", 6, 1.0, None, None),   # ONE CONE KOREAN SPIN — корейский спин на одном конусе
    (93, "120", 4, 1.0, None, None),   # ONE CONE KOREAN SPIN — корейский спин на одном конусе (повторный подход)
    (92, "120", 4, 1.0, None, None),   # BACK KOREAN SPIN — корейский спин спиной
    (4, "80", 8, 0.50, None, None),    # BACK COBRA — кобра спиной (качество 0.50)
    (41, "120", 14, 1.0, None, None),  # 5 WHEELS SITTING / м. машинка
]

WORKOUT_TEMPLATE = ULIA_DATA
'''#[
    # Пример: 3 одиночных трюка + связка из 2 шагов
    (83, "80", 8, 1.0, None, None),      # trick_id=1, 8 конусов, дорожка 80 ВОЛНА
    (61, "80", 6, 1.0, None, None),      # trick_id=2   FLAT SHIFT
    (68, "80", 7, 1.0, None, None),      # trick_id=3 HEAL ONE WHEEL
    (4, "80", 5, 1.0, "combo1", 1),     # шаг 1 связки
    (5, "80", 5, 1.0, "combo1", 2),     # шаг 2 связки
]'''
# Если хотите разные тренировки -- сделайте список списков: WORKOUT_TEMPLATES = [[...], [...], ...]

# ============================================================
# === КОНФИГУРАЦИЯ ЗАПУСКА ===================================
# ============================================================
STUDENT_ID = 50         # ID ученика в БД (Liudmila Rutkevich)
NUM_WORKOUTS = 30       # сколько тренировок вперед симулировать
START_WORKOUT_ID = 100  # с какого ID начинать новые тренировки (для вывода)
START_LEVEL = 1      # начальный LEVEL для симуляции (None = взять из БД)
START_XP = 0         # начальный XP для симуляции (None = взять из БД)


# ============================================================
# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ РАСЧЁТА ========================
# ============================================================

def get_level_reqs(level: int):
    """Возвращает (required_xp, max_workout_logs, min_trick_categories) для уровня."""
    return LEVEL_EXPERIENCE.get(level, (999999999, 10, 3))

def get_xp_penalty(level_diff: int) -> float:
    if level_diff > 9:
        return 0.01
    if level_diff > 0:
        return XP_PENALTIES.get(level_diff, 1.0)
    return 1.0

def calc_line_coeff(trick: models.Trick, cone_line: str) -> float:
    if cone_line == "50":
        return trick.coeff_50
    elif cone_line == "80":
        return trick.coeff_80
    elif cone_line == "120":
        return trick.coeff_120
    return 1.0

def calc_bonus_multiplier(trick: models.Trick, quantity: int) -> float:
    bonus = 1.0
    if quantity > trick.base_quantity:
        extra = quantity - trick.base_quantity
        bonus += math.sqrt(extra) * trick.extra_bonus_pct
    elif trick.base_quantity > 0:
        bonus = max(0.0, quantity / trick.base_quantity)
    return bonus

def calculate_single_trick_points_xp(
    trick: models.Trick,
    cone_line: str,
    quantity: int,
    execution_multiplier: float,
    skater_level: int,
) -> tuple[float, int]:
    """Синхронная версия calculate_single_trick."""
    if quantity is None or quantity <= 0:
        return 0.0, 0
    line_coeff = calc_line_coeff(trick, cone_line)
    bonus_mult = calc_bonus_multiplier(trick, quantity)
    raw_points = trick.base_tech_points * line_coeff * execution_multiplier
    calculated_points = round(raw_points * bonus_mult, 2)
    raw_xp = trick.base_xp * line_coeff * quantity * execution_multiplier * bonus_mult
    level_diff = skater_level - trick.trick_tier
    xp_mult = get_xp_penalty(level_diff)
    calculated_xp = max(0, int(raw_xp * xp_mult))
    return calculated_points, calculated_xp

def calculate_combo_step(prev_result: float, next_trick_points: float) -> float:
    avg = (prev_result + next_trick_points) / 2
    bump = 1.0 if next_trick_points >= prev_result else 0.0
    diff = next_trick_points - prev_result
    table_value = max(0.0, 10.0 - abs(diff))
    multiplier = 1.0 + table_value / 100.0
    return (avg + bump) * multiplier

def process_workout_logs(
    logs: List["WorkoutLogEntry"],
    tricks: dict[int, models.Trick],
    skater_level: int,
) -> tuple[float, int]:
    """
    Полный расчёт итогов тренировки (аналог calculate_current_workout_totals из calculator.py),
    но синхронный и с локальными справочниками LEVEL_EXPERIENCE / XP_PENALTIES.
    Возвращает: (sport_score_avg, total_xp)
    """
    if not logs:
        return 0.0, 0

    # --- Уникальные категории трюков на тренировке ---
    categories = set()
    for lg in logs:
        t = tricks.get(lg.trick_id)
        if t:
            categories.add(t.category)
    categories_count = len(categories)

    # --- Группировка шагов связок по combo_id ---
    combo_steps: dict[str, List["WorkoutLogEntry"]] = {}
    for lg in logs:
        if lg.combo_id:
            combo_steps.setdefault(lg.combo_id, []).append(lg)

    # --- 1. СПОРТИВНЫЙ РЕЗУЛЬТАТ: все шаги (включая шаги связок) участвуют ---
    sport_results = [(lg.calculated_points, lg.calculated_xp) for lg in logs]

    # --- 2. ИГРОВОЙ ОПЫТ: только одиночные трюки (шаги связок ИСКЛЮЧАЕМ, их XP пойдёт через агрегат) ---
    xp_results = [
        (lg.calculated_points, lg.calculated_xp)
        for lg in logs if not lg.combo_id
    ]

    # --- Обработка каждой связки ---
    for cid, steps in combo_steps.items():
        # 1. Сортируем шаги по combo_step
        steps.sort(key=lambda s: s.combo_step or 0)

        # 2. Рассчитываем суммарный спортивный балл связки (combo_points)
        #    первый шаг = его чистый балл, последующие = calculate_combo_step от предыдущего результата
        combo_points = steps[0].calculated_points
        for nxt in steps[1:]:
            combo_points = calculate_combo_step(combo_points, nxt.calculated_points)
        combo_points = round(combo_points, 2)

        # 3. Динамическая пропорция опыта к баллам (base_ratio) для этой связки
        sum_base_xp = 0
        sum_base_tech = 0
        for s in steps:
            tr = tricks.get(s.trick_id)
            if tr:
                sum_base_xp += tr.base_xp
                sum_base_tech += tr.base_tech_points
            else:
                # Фолбэк: если трюка нет в кэше, берём соотношение из самого лога
                sum_base_xp += s.calculated_xp
                sum_base_tech += s.calculated_points
        base_ratio = sum_base_xp / sum_base_tech if sum_base_tech else 1.0

        # 4. Среднее количество конусов/повторений по шагам связки
        avg_qty = sum(s.quantity for s in steps) / len(steps)

        # 5. Базовый игровой опыт связки с бонусом за длину
        length_bonus = 1.0 + 0.15 * max(0, len(steps) - 1)
        raw_combo_xp = combo_points * base_ratio * avg_qty * 1.4 * length_bonus

        # 6. Штраф за уровень на основе СРЕДНЕГО тира (округлённого до целого)
        combo_tiers = [tricks[s.trick_id].trick_tier for s in steps if s.trick_id in tricks]
        xp_mult = 1.0
        if combo_tiers:
            avg_tier = round(sum(combo_tiers) / len(combo_tiers))
            level_diff = skater_level - avg_tier
            xp_mult = get_xp_penalty(level_diff)

        # 7. Итоговый опыт связки
        combo_xp = max(0, int(raw_combo_xp * xp_mult))

        # Добавляем агрегат связки строго один раз в оба массива для финального подсчёта
        sport_results.append((combo_points, combo_xp))
        xp_results.append((combo_points, combo_xp))

    # --- ФИНАЛЬНЫЙ СПОРТИВНЫЙ БАЛЛС: топ-8 по очкам ---
    sorted_by_pts = sorted(sport_results, key=lambda r: r[0], reverse=True)
    top_8 = sorted_by_pts[:8]
    if top_8:
        avg_pts = sum(p for p, _ in top_8) / len(top_8)
        if len(top_8) < 8:
            avg_pts -= (8 - len(top_8)) * 3.0
        req = get_level_reqs(skater_level)
        min_cats = req[2]
        if categories_count < min_cats:
            avg_pts -= (min_cats - categories_count) * 3.0
        sport_score = round(avg_pts, 2)
    else:
        sport_score = 0.0

    # --- ФИНАЛЬНЫЙ ОПЫТ: топ-N по XP (N = max_workout_logs для уровня) ---
    sorted_by_xp = sorted(xp_results, key=lambda r: r[1], reverse=True)
    req = get_level_reqs(skater_level)
    max_logs = req[1]
    top_xp = sorted_by_xp[:max_logs]
    total_xp = sum(x for _, x in top_xp)
    min_cats = req[2]
    if categories_count < min_cats:
        miss = min_cats - categories_count
        total_xp = int(total_xp * max(0.0, 1.0 - miss * 0.1))

    return sport_score, total_xp


@dataclass
class WorkoutLogEntry:
    """Упрощённая запись лога для расчёта."""
    trick_id: int
    cone_line: str
    quantity: int
    execution_multiplier: float
    calculated_points: float
    calculated_xp: int
    combo_id: Optional[str] = None
    combo_step: Optional[int] = None


# ============================================================
# === ОСНОВНАЯ ЛОГИКА ========================================
# ============================================================

async def main():
    async with async_session() as session:
        # 1. Загружаем ученика
        student = await session.get(models.User, STUDENT_ID)
        if not student:
            print(f"[ERROR] Ученик с id={STUDENT_ID} не найден")
            return
        if student.role != "student":
            print(f"[WARN] Пользователь {student.full_name} (id={student.id}) не ученик (role={student.role})")

        # 2. Загружаем LevelExperience из БД
        global LEVEL_EXPERIENCE, XP_PENALTIES
        le_result = await session.execute(select(models.LevelExperience))
        for le in le_result.scalars().all():
            LEVEL_EXPERIENCE[le.level] = (le.required_xp, le.max_workout_logs, le.min_trick_categories)
        if not LEVEL_EXPERIENCE:
            print("[WARN] LevelExperience пуст в БД, используем фолбэк")
            LEVEL_EXPERIENCE = LEVEL_EXPERIENCE_FALLBACK.copy()
        else:
            print(f"[INFO] Загружено LevelExperience: {len(LEVEL_EXPERIENCE)} уровней из БД")

        # 3. Загружаем XpPenalty из БД
        xp_result = await session.execute(select(models.XpPenalty))
        for xp in xp_result.scalars().all():
            XP_PENALTIES[xp.level_difference] = xp.xp_multiplier
        if not XP_PENALTIES:
            print("[WARN] XpPenalty пуст в БД, используем фолбэк")
            XP_PENALTIES = XP_PENALTIES_FALLBACK.copy()
        else:
            print(f"[INFO] Загружено XpPenalty: {len(XP_PENALTIES)} записей из БД")

        # 4. Загружаем все трюки в кэш
        tricks_result = await session.execute(select(models.Trick))
        tricks = {t.id: t for t in tricks_result.scalars().all()}

        # Применяем переопределения TRICK_OVERRIDES (только в памяти, БД не меняется)
        for tid, overrides in TRICK_OVERRIDES.items():
            if tid in tricks:
                trick = tricks[tid]
                if "trick_tier" in overrides:
                    trick.trick_tier = overrides["trick_tier"]
                if "base_xp" in overrides:
                    trick.base_xp = overrides["base_xp"]
                if "extra_bonus_pct" in overrides:
                    trick.extra_bonus_pct = overrides["extra_bonus_pct"]
                if "base_tech_points" in overrides:
                    trick.base_tech_points = overrides["base_tech_points"]

        # Определяем стартовый уровень и XP (можно переопределить через конфиг)
        start_level = START_LEVEL if START_LEVEL is not None else student.level
        start_xp = START_XP if START_XP is not None else student.xp

        print(f"\n{'='*80}")
        print(f"СИМУЛЯЦИЯ {NUM_WORKOUTS} ТРЕНИРОВОК ДЛЯ УЧЕНИКА:")
        print(f"  ID: {student.id} | Имя: {student.full_name} | Telegram: {student.telegram_id}")
        print(f"  LEVEL из БД: {student.level} | XP из БД: {student.xp}")
        if START_LEVEL is not None or START_XP is not None:
            print(f"  СТАРТ симуляции: LEVEL={start_level} | XP={start_xp}")
        print(f"{'='*80}\n")

        # 3. Проверяем шаблон
        missing_tricks = [tid for tid, *_ in WORKOUT_TEMPLATE if tid not in tricks]
        if missing_tricks:
            print(f"[ERROR] В шаблоне есть неизвестные trick_id: {missing_tricks}")
            print("Доступные трюки (id, name, tier):")
            for t in tricks.values():
                print(f"  {t.id}: {t.name} (Tier {t.trick_tier})")
            return

        # 4. Симуляция
        current_level = start_level
        current_xp = start_xp
        workout_id = START_WORKOUT_ID
        log_id = 1  # локальный счётчик для вывода

        for w in range(1, NUM_WORKOUTS + 1):
            print(f"\n{'-'*80}")
            print(f"ТРЕНИРОВКА #{w} (workout_id={workout_id})")
            print(f"{'-'*80}")

            logs: List[WorkoutLogEntry] = []

            # Собираем шаги по комбо для вывода итогов комбо
            combo_steps_map: dict[str, List[tuple[int, int, float, int, float, int, str, int]]] = {}
            # combo_id -> list of (log_id, tid, pts, xp, cl, qty, mult, step)

            # Создаём записи лога по шаблону
            for idx, (tid, cl, qty, mult, cid, cstep) in enumerate(WORKOUT_TEMPLATE):
                trick = tricks[tid]
                pts, xp = calculate_single_trick_points_xp(
                    trick, cl, qty, mult, current_level
                )
                entry = WorkoutLogEntry(
                    trick_id=tid,
                    cone_line=cl,
                    quantity=qty,
                    execution_multiplier=mult,
                    calculated_points=pts,
                    calculated_xp=xp,
                    combo_id=cid,
                    combo_step=cstep,
                )
                logs.append(entry)

                # Накапливаем шаги комбо для последующего расчёта и вывода
                if cid:
                    combo_steps_map.setdefault(cid, []).append(
                        (log_id, tid, pts, xp, cl, qty, mult, cstep or 0)
                    )

                # Вывод строки TrickLog
                combo_info = ""
                if cid:
                    combo_info = f"  combo_id={cid:<3} step={cstep:<3}"
                print(f"  LOG#{log_id:3d} | {trick.name:<30} | Q= {qty:3d} | "
                      f"TID={tid:3d} | PTS={pts:7.2f} | XP={xp:5d}{combo_info}")
                log_id += 1

            # После всех логов тренировки — считаем и выводим итоги по комбо
            if combo_steps_map:
                for cid, steps in combo_steps_map.items():
                    steps.sort(key=lambda s: s[7])  # sort by combo_step
                    # Рассчитываем combo_points по тем же правилам
                    combo_pts = steps[0][2]  # pts первого шага
                    for s in steps[1:]:
                        combo_pts = calculate_combo_step(combo_pts, s[2])
                    combo_pts = round(combo_pts, 2)

                    # base_ratio
                    sum_base_xp = 0
                    sum_base_tech = 0
                    for s in steps:
                        tr = tricks.get(s[1])
                        if tr:
                            sum_base_xp += tr.base_xp
                            sum_base_tech += tr.base_tech_points
                        else:
                            sum_base_xp += s[3]
                            sum_base_tech += s[2]
                    base_ratio = sum_base_xp / sum_base_tech if sum_base_tech else 1.0

                    avg_qty = sum(s[5] for s in steps) / len(steps)
                    length_bonus = 1.0 + 0.15 * max(0, len(steps) - 1)
                    raw_combo_xp = combo_pts * base_ratio * avg_qty * 1.4 * length_bonus

                    # штраф за уровень по среднему тиру
                    combo_tiers = [tricks[s[1]].trick_tier for s in steps if s[1] in tricks]
                    xp_mult = 1.0
                    if combo_tiers:
                        avg_tier = round(sum(combo_tiers) / len(combo_tiers))
                        level_diff = current_level - avg_tier
                        xp_mult = get_xp_penalty(level_diff)

                    combo_xp = max(0, int(raw_combo_xp * xp_mult))

                    # Вывод итога комбо
                    step_nums = ",".join(str(s[7]) for s in steps)
                    print(f"  >>> COMBO {cid} (steps: {step_nums}) => PTS={combo_pts:.2f} | XP={combo_xp}")

            # Считаем итоги тренировки
            sport_score, workout_xp = process_workout_logs(logs, tricks, current_level)

            print(f"\n  >> ИТОГИ ТРЕНИРОВКИ: sport_score={sport_score:.2f} | workout_xp={workout_xp}")

            # Начисляем XP и проверяем уровни
            current_xp += workout_xp
            old_level = current_level
            while True:
                req_xp, _, _ = get_level_reqs(current_level + 1)
                if current_xp >= req_xp:
                    current_level += 1
                else:
                    break

            level_up = " [LEVEL UP!]" if current_level > old_level else ""
            print(f"  >> ПРОГРЕСС: LEVEL {old_level} -> {current_level} | XP {start_xp} -> {current_xp}{level_up}")

            workout_id += 1

        # 5. Финальный отчёт
        print(f"\n{'='*80}")
        print(f"ФИНАЛЬНЫЙ РЕЗУЛЬТАТ ПОСЛЕ {NUM_WORKOUTS} ТРЕНИРОВОК:")
        print(f"  LEVEL: {start_level} -> {current_level}")
        print(f"  XP:    {start_xp} -> {current_xp}")
        print(f"  Набрано XP: {current_xp - start_xp}")
        print(f"{'='*80}\n")


if __name__ == "__main__":
    asyncio.run(main())