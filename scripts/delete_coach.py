import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# запускать с корня: python -m scripts.delete_coach
"""Скрипт удаления тренера: пользователь, его тренировки и сообщества.

Что делает:
  1. Находит все тренировки тренера (workouts.coach_id = COACH_ID).
  2. Удаляет связанные trick_logs и workout_registrations этих тренировок.
  3. Удаляет сами тренировки.
  4. Удаляетcoach_student (и как тренера, и как ученика).
  5. Удаляет community_coach (coach_id).
  6. Удаляет сообщества, где тренер был единственным участником
     (communities без оставшихся community_coach).
  7. Обнуляет workouts.coach_id, если где-то остались ссылки.
  8. Обнуляет users.viewer_for_id.
  9. Удаляет пользователя COACH_ID.

Запуск:
  python -m scripts.delete_coach
"""

import asyncio

from sqlalchemy import text

from database import async_session

# ============================================
# КОНФИГУРАЦИЯ
# ============================================
COACH_ID = 65


async def delete_coach():
    async with async_session() as session:
        coach = (
            await session.execute(
                text("SELECT id, role FROM users WHERE id = :cid"),
                {"cid": COACH_ID},
            )
        ).fetchone()

        if coach is None:
            print(f"Пользователь id={COACH_ID} не найден.")
            return

        # 1. Поиск тренировок тренера
        workout_ids = [
            r.id
            for r in (
                await session.execute(
                    text("SELECT id FROM workouts WHERE coach_id = :cid"),
                    {"cid": COACH_ID},
                )
            ).fetchall()
        ]
        print(f"Найдено тренировок тренера id={COACH_ID}: {len(workout_ids)}")

        if workout_ids:
            for wid in workout_ids:
                await session.execute(
                    text("DELETE FROM trick_logs WHERE workout_id = :wid"),
                    {"wid": wid},
                )
                await session.execute(
                    text("DELETE FROM workout_registrations WHERE workout_id = :wid"),
                    {"wid": wid},
                )
                await session.execute(
                    text("DELETE FROM workouts WHERE id = :wid"),
                    {"wid": wid},
                )
            print(f"Удалены тренировки и их логи: {workout_ids}")

        # 2. coach_student
        await session.execute(
            text(
                "DELETE FROM coach_student WHERE coach_id = :cid OR student_id = :cid"
            ),
            {"cid": COACH_ID},
        )
        print(f"Удалены coach_student для id={COACH_ID}.")

        # 3. community_coach — собираем сообщества тренера
        community_ids = [
            r.community_id
            for r in (
                await session.execute(
                    text("SELECT community_id FROM community_coach WHERE coach_id = :cid"),
                    {"cid": COACH_ID},
                )
            ).fetchall()
        ]
        await session.execute(
            text("DELETE FROM community_coach WHERE coach_id = :cid"),
            {"cid": COACH_ID},
        )
        print(f"Удалён community_coach для id={COACH_ID}.")

        # 4. Сообщества, где тренер был единственным участником
        for community_id in community_ids:
            remaining = (
                await session.execute(
                    text(
                        "SELECT COUNT(*) AS cnt FROM community_coach "
                        "WHERE community_id = :cid"
                    ),
                    {"cid": community_id},
                )
            ).fetchone()
            if remaining.cnt == 0:
                await session.execute(
                    text("DELETE FROM communities WHERE id = :cid"),
                    {"cid": community_id},
                )
                print(f"Удалено пустое сообщество id={community_id}.")

        # 5. Обнуляем ссылки на тренера в workouts и users
        await session.execute(
            text("UPDATE workouts SET coach_id = NULL WHERE coach_id = :cid"),
            {"cid": COACH_ID},
        )
        await session.execute(
            text("UPDATE users SET viewer_for_id = NULL WHERE viewer_for_id = :cid"),
            {"cid": COACH_ID},
        )

        # 6. Удаляем пользователя
        await session.execute(
            text("DELETE FROM users WHERE id = :cid"),
            {"cid": COACH_ID},
        )
        print(f"Пользователь id={COACH_ID} удалён.")

        await session.commit()
        print("Готово.")


if __name__ == "__main__":
    asyncio.run(delete_coach())