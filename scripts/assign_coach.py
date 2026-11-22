import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# запускать с корня: python -m scripts.assign_coach
"""Скрипт назначения тренера, создания/привязки сообщества и связи тренера с учениками.

Что делает:
  1. Назначает пользователя COACH_ID тренером (role = 'coach', is_approved = True).
  2. Находит сообщество по COMMUNITY_NAME или создаёт новое.
  3. Добавляет тренера в сообщество (community_coach), если его там ещё нет.
  4. Связывает тренера с учениками STUDENT_IDS (coach_student), пропуская
     несуществующих и уже привязанных.

Запуск:
  python -m scripts.assign_coach
"""

import asyncio

from sqlalchemy import text

from database import async_session

# ============================================
# КОНФИГУРАЦИЯ (меняйте под задачу)
# ============================================
COACH_ID = 65
COMMUNITY_NAME = "Игорята"
STUDENT_IDS = list(range(1, 21))


async def assign_coach():
    async with async_session() as session:
        # 1. Назначаем тренера
        coach = (
            await session.execute(
                text("SELECT id, role FROM users WHERE id = :cid"),
                {"cid": COACH_ID},
            )
        ).fetchone()

        if coach is None:
            print(f"Пользователь id={COACH_ID} не найден. Проверьте COACH_ID.")
            return

        if coach.role != "coach":
            await session.execute(
                text("UPDATE users SET role = 'coach', is_approved = 1 WHERE id = :cid"),
                {"cid": COACH_ID},
            )
            print(f"Пользователь id={COACH_ID} назначен тренером.")
        else:
            print(f"Пользователь id={COACH_ID} уже тренер.")

        # 2. Находим или создаём сообщество
        community = (
            await session.execute(
                text("SELECT id FROM communities WHERE name = :name"),
                {"name": COMMUNITY_NAME},
            )
        ).fetchone()

        if community is None:
            await session.execute(
                text("INSERT INTO communities (name, created_at) VALUES (:name, datetime('now'))"),
                {"name": COMMUNITY_NAME},
            )
            community = (
                await session.execute(
                    text("SELECT id FROM communities WHERE name = :name"),
                    {"name": COMMUNITY_NAME},
                )
            ).fetchone()
            print(f"Создано сообщество '{COMMUNITY_NAME}' (id={community.id}).")
        else:
            print(f"Сообщество '{COMMUNITY_NAME}' уже существует (id={community.id}).")

        community_id = community.id

        # 3. Добавляем тренера в сообщество
        exists = (
            await session.execute(
                text(
                    "SELECT 1 FROM community_coach "
                    "WHERE community_id = :cid AND coach_id = :coach"
                ),
                {"cid": community_id, "coach": COACH_ID},
            )
        ).fetchone()

        if exists is None:
            await session.execute(
                text(
                    "INSERT INTO community_coach (community_id, coach_id) "
                    "VALUES (:cid, :coach)"
                ),
                {"cid": community_id, "coach": COACH_ID},
            )
            print(f"Тренер id={COACH_ID} добавлен в сообщество '{COMMUNITY_NAME}'.")
        else:
            print(f"Тренер id={COACH_ID} уже в сообществе '{COMMUNITY_NAME}'.")

        # 4. Привязываем учеников
        added, skipped_missing, skipped_taken = 0, 0, 0
        for sid in STUDENT_IDS:
            if sid == COACH_ID:
                continue

            student = (
                await session.execute(
                    text("SELECT id FROM users WHERE id = :sid"),
                    {"sid": sid},
                )
            ).fetchone()

            if student is None:
                skipped_missing += 1
                continue

            already = (
                await session.execute(
                    text(
                        "SELECT 1 FROM coach_student "
                        "WHERE coach_id = :coach AND student_id = :sid"
                    ),
                    {"coach": COACH_ID, "sid": sid},
                )
            ).fetchone()

            if already is not None:
                skipped_taken += 1
                continue

            await session.execute(
                text(
                    "INSERT INTO coach_student (coach_id, student_id) "
                    "VALUES (:coach, :sid)"
                ),
                {"coach": COACH_ID, "sid": sid},
            )
            added += 1

        print(
            f"Привязка учеников завершена: добавлено {added}, "
            f"пропущено (нет в БД) {skipped_missing}, "
            f"уже привязаны {skipped_taken}."
        )

        await session.commit()
        print("Готово.")


if __name__ == "__main__":
    asyncio.run(assign_coach())