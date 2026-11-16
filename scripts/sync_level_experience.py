# File: roller_bot/scripts/sync_level_experience.py
# Синхронизация таблицы LevelExperience со словарём LEVEL_EXPERIENCE (upsert уровней 1..80).
import os
import sys
import asyncio

_here = os.path.dirname(os.path.abspath(__file__))
_root = _here
while _root != os.path.dirname(_root):
    if os.path.exists(os.path.join(_root, "models.py")):
        break
    _root = os.path.dirname(_root)
if _root not in sys.path:
    sys.path.insert(0, _root)

os.chdir(_root)

from sqlalchemy import select, func
from database import async_session
from models import LevelExperience, User, Workout

# После синхронизации tricks.csv base_xp топовых трюков вырос ~в 3 раза
# (BUTTERFLY CROSS 51->153 и т.д.), значит и доход за тренировку у ТОПов вырос втрое.
# Компенсируем: пороги с 9-го уровня умножаем на XP_INFLATION (L1-L8 не трогаем —
# доход новичков на простых трюках не вырос).
XP_INFLATION = 1.0  # отключено: применяются чистые значения из LEVEL_EXPERIENCE
INFLATION_FROM_LEVEL = 9


def _scale(level: int, req_xp: int) -> int:
    if level < INFLATION_FROM_LEVEL:
        return req_xp
    # округляем до красивых сотен
    return int(round(req_xp * XP_INFLATION / 100.0) * 100)


LEVEL_EXPERIENCE = {
    # LEVEL: (REQUIRED_XP, MAX_WORKOUT_LOGS, MIN_TRICK_CATEGORIES)
    1: (0, 5, 1),
    2: (200, 5, 1),
    3: (600, 5, 1),
    4: (1200, 5, 1),
    5: (2000, 5, 2),
    6: (3000, 5, 2),
    7: (4200, 5, 2),
    8: (5600, 5, 2),
    9: (8400, 5, 2),  # Скачок шага в 2 раза! так задуманно
    10: (12200, 7, 4),
    11: (17000, 7, 4),
    12: (22800, 8, 4),
    13: (30000, 8, 4),
    14: (38400, 9, 4),  # Округлено для красивой дельты +8400
    15: (48000, 9, 4),  # Скорректировано под плавный рост (+9600)
    16: (58800, 10, 4),  # Дельта +10800
    17: (70800, 10, 4),  # Дельта +12000
    18: (84000, 12, 4),  # Дельта +13200
    19: (98400, 12, 4),  # Дельта +14400
    20: (114000, 15, 5),  # Дельта +15600. Фиксация 15 логов и 5 категорий
    # --- ЭЛИТНЫЙ ДИАПАЗОН С ЗАПАСОМ НА РОСТ (Шаг +16000 за левел) ---
    21: (130000, 15, 5),
    22: (146000, 15, 5),
    23: (162000, 15, 5),
    24: (178000, 15, 5),
    25: (194000, 15, 5),
    26: (210000, 15, 5),
    27: (226000, 15, 5),
    28: (242000, 15, 5),
    29: (258000, 15, 5),
    30: (274000, 15, 5),
    31: (290000, 15, 5),
    32: (306000, 15, 5),
    33: (322000, 15, 5),
    34: (338000, 15, 5),
    35: (354000, 15, 5),
    36: (370000, 15, 5),
    37: (386000, 15, 5),
    38: (402000, 15, 5),
    39: (418000, 15, 5),
    40: (434000, 15, 5),
    41: (450000, 15, 5),
    42: (466000, 15, 5),
    43: (482000, 15, 5),
    44: (498000, 15, 5),
    45: (514000, 15, 5),
    46: (530000, 15, 5),
    47: (546000, 15, 5),
    48: (562000, 15, 5),
    49: (578000, 15, 5),
    50: (594000, 15, 5),
    51: (610000, 15, 5),
    52: (626000, 15, 5),
    53: (642000, 15, 5),
    54: (658000, 15, 5),
    55: (674000, 15, 5),
    56: (690000, 15, 5),
    57: (706000, 15, 5),
    58: (722000, 15, 5),
    59: (738000, 15, 5),
    60: (754000, 15, 5),
    61: (770000, 15, 5),
    62: (786000, 15, 5),
    63: (802000, 15, 5),
    64: (818000, 15, 5),
    65: (834000, 15, 5),
    66: (850000, 15, 5),
    67: (866000, 15, 5),
    68: (882000, 15, 5),
    69: (898000, 15, 5),
    70: (914000, 15, 5),
    71: (930000, 15, 5),
    72: (946000, 15, 5),
    73: (962000, 15, 5),
    74: (978000, 15, 5),
    75: (994000, 15, 5),
    76: (1010000, 15, 5),
    77: (1026000, 15, 5),
    78: (1042000, 15, 5),
    79: (1058000, 15, 5),
    80: (1074000, 15, 5),
}


async def sync(dry_run: bool = True):
    async with async_session() as session:
        db_levels = (await session.execute(select(LevelExperience))).scalars().all()
        db_by_level = {l.level: l for l in db_levels}

        added, updated, removed = [], [], []

        for level, (req_xp, max_logs, min_cats) in LEVEL_EXPERIENCE.items():
            req_xp = _scale(level, req_xp)
            row = db_by_level.get(level)
            if row is None:
                added.append(level)
                if not dry_run:
                    session.add(
                        LevelExperience(
                            level=level,
                            required_xp=req_xp,
                            max_workout_logs=max_logs,
                            min_trick_categories=min_cats,
                        )
                    )
                continue
            changed = []
            if row.required_xp != req_xp:
                changed.append(f"required_xp: {row.required_xp} -> {req_xp}")
                row.required_xp = req_xp
            if row.max_workout_logs != max_logs:
                changed.append(
                    f"max_workout_logs: {row.max_workout_logs} -> {max_logs}"
                )
                row.max_workout_logs = max_logs
            if row.min_trick_categories != min_cats:
                changed.append(
                    f"min_trick_categories: {row.min_trick_categories} -> {min_cats}"
                )
                row.min_trick_categories = min_cats
            if changed:
                updated.append((level, changed))

        for level in db_by_level:
            if level not in LEVEL_EXPERIENCE:
                removed.append(level)

        print(f"➕ Новых уровней: {len(added)} {added if added else ''}")
        print(f"🔄 Обновлено уровней: {len(updated)}")
        for level, changes in updated:
            print(f"   • L{level}: {'; '.join(changes)}")
        if removed:
            print(f"🗑️ В базе, но нет в словаре (НЕ удаляются): {removed}")
        if not added and not updated and not removed:
            print("✅ Таблица уже соответствует словарю.")

        # --- Диагностика по реальным пользователям ---
        print("\n--- ДИАГНОСТИКА ПОЛЬЗОВАТЕЛЕЙ ---")
        students = (
            (await session.execute(select(User).where(User.role == "student")))
            .scalars()
            .all()
        )
        for u in students:
            # XP за последние тренировки — грубая оценка темпа
            workouts_count = (
                await session.execute(
                    select(func.count()).select_from(User).where(User.id == u.id)
                )
            ).scalar_one()
            print(
                f"  id={u.id:<4} {u.full_name[:30]:<32} level={u.level:<3} xp={u.xp:<7}"
            )

        if dry_run:
            print(
                "\n🔍 DRY RUN — изменения не сохранены. Запустите с --apply для записи."
            )
        else:
            await session.commit()
            print("\n✅ Изменения сохранены в базу.")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    apply = "--apply" in sys.argv
    try:
        asyncio.run(sync(dry_run=not apply))
    finally:
        if not apply:
            input("Нажмите Enter для выхода...")
