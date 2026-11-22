import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# запускать с корня : python -m scripts.delete_users
"""Скрипт каскадного удаления конкретных пользователей по ID.

Удаляет пользователей и всё связанные с ними данные:
  - trick_logs (student_id)
  - workout_registrations (student_id)
  - coach_student (coach_id / student_id)
  - community_coach (coach_id)
  - workouts (coach_id -> SET NULL)
  - users.viewer_for_id (-> SET NULL у зрителей)

Запуск:
  python delete_users.py
"""

import asyncio

from sqlalchemy import text

from database import async_session

# ============================================
# КОНФИГУРАЦИЯ
# ============================================
USER_IDS_TO_DELETE = [65]


async def delete_users_cascade():
    if not USER_IDS_TO_DELETE:
        print("Нет ID для удаления.")
        return

    ids_tuple = tuple(USER_IDS_TO_DELETE)
    ids_list = list(USER_IDS_TO_DELETE)

    async with async_session() as session:
        await session.execute(text("PRAGMA foreign_keys = OFF"))

        # 1. Удалить логи трюков учеников
        for uid in ids_list:
            await session.execute(
                text("DELETE FROM trick_logs WHERE student_id = :uid"),
                {"uid": uid},
            )
        print(f"Удалены trick_logs для user.id in {ids_tuple}")

        # 2. Удалить записи учеников на тренировки
        for uid in ids_list:
            await session.execute(
                text("DELETE FROM workout_registrations WHERE student_id = :uid"),
                {"uid": uid},
            )
        print(f"Удалены workout_registrations для user.id in {ids_tuple}")

        # 3. Удалить связи в coach_student (и как тренера, и как ученика)
        for uid in ids_list:
            await session.execute(
                text("DELETE FROM coach_student WHERE coach_id = :uid OR student_id = :uid"),
                {"uid": uid},
            )
        print(f"Удалены coach_student для user.id in {ids_tuple}")

        # 4. Удалить связи в community_coach (coach_id)
        for uid in ids_list:
            await session.execute(
                text("DELETE FROM community_coach WHERE coach_id = :uid"),
                {"uid": uid},
            )
        print(f"Удалены community_coach для user.id in {ids_tuple}")

        # 5. Обнулить coach_id в тренировках, где удалённый пользователь — тренер
        for uid in ids_list:
            await session.execute(
                text("UPDATE workouts SET coach_id = NULL WHERE coach_id = :uid"),
                {"uid": uid},
            )
        print(f"Обнулён coach_id в workouts для user.id in {ids_tuple}")

        # 6. Обнулить viewer_for_id у зрителей, которые следили за удаляемыми
        for uid in ids_list:
            await session.execute(
                text("UPDATE users SET viewer_for_id = NULL WHERE viewer_for_id = :uid"),
                {"uid": uid},
            )
        print(f"Обнулён viewer_for_id у зрителей для user.id in {ids_tuple}")

        # 7. Удалить самих пользователей
        for uid in ids_list:
            await session.execute(
                text("DELETE FROM users WHERE id = :uid"),
                {"uid": uid},
            )
        print(f"Удалены пользователи с id in {ids_tuple}")

        await session.execute(text("PRAGMA foreign_keys = ON"))
        await session.commit()
        print("Каскадное удаление завершено!")


if __name__ == "__main__":
    asyncio.run(delete_users_cascade())