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
    10: (12200, 7, 3),
    11: (16400, 7, 3),
    12: (20600, 8, 3),
    13: (24900, 8, 4),
    14: (29300, 9, 4),
    15: (33700, 9, 4),
    16: (38200, 10, 4),
    17: (42700, 10, 4),
    18: (47200, 12, 4),
    19: (51700, 12, 4),
    20: (56200, 15, 5),
}

# Уровни 21–80: REQUIRED_XP растёт с дельтой, начиная с 4700 на L21,
# и увеличивающейся на 50 на каждом следующем уровне.
# MAX_WORKOUT_LOGS=15, MIN_TRICK_CATEGORIES=5 (как на L20).
_base_xp = 56200  # L20
_delta = 4500  # стартовая дельта для L21
for _lvl in range(21, 81):
    _base_xp += _delta
    LEVEL_EXPERIENCE[_lvl] = (_base_xp, 15, 5)
    _delta += 50


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
