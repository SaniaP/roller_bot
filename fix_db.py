"""Скрипт для выполнения конкретных изменений в БД.
Значения можно менять в секции CONFIG ниже."""
import asyncio
from sqlalchemy import text
from database import async_session

# ============================================
# КОНФИГУРАЦИЯ: измените значения здесь
# подставить None если не хотим делать изменение пример "delete_user_id": None
# ============================================
CONFIG = {
    # 1. Удалить пользователя по ID
    "delete_user_id": None,

    # 2. Обновить is_approved для пользователя по ID
    "update_is_approved_user_id": None,
    "update_is_approved_value": True,  # True/False

    # 3. Обновить LEVEL и опыт для пользователя по ID
    "update_xp_user_id": 2,
    "update_xp_value": 0,
    "update_level_value":1,

    # 4. Удалить TrickLog по параметрам
    "delete_tricklog_id": None,
    "delete_tricklog_workout_id": 1,
    "delete_tricklog_student_id": 5,

    # 5. Удалить workout.id=1 из TrickLog, WorkoutRegistration, WorkoutParticipant
    "delete_workout_id": None,
}


async def apply_changes():
    async with async_session() as session:
        await session.execute(text("PRAGMA foreign_keys = OFF"))

        # 1. Удаление пользователя
        if CONFIG["delete_user_id"]:
            uid = CONFIG["delete_user_id"]
            await session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": uid})
            print(f"Удален пользователь с id={uid}")

        # 2. Обновление is_approved
        if CONFIG["update_is_approved_user_id"]:
            uid = CONFIG["update_is_approved_user_id"]
            val = CONFIG["update_is_approved_value"]
            await session.execute(
                text("UPDATE users SET is_approved = :val WHERE id = :uid"),
                {"val": val, "uid": uid},
            )
            print(f"Пользователь id={uid}: is_approved={val}")

        # 3. Обновление XP и уровня
        if CONFIG["update_xp_user_id"]:
            uid = CONFIG["update_xp_user_id"]
            xp = CONFIG["update_xp_value"]
            lvl = CONFIG["update_level_value"]  # Не забудьте добавить этот ключ в CONFIG
            
            await session.execute(
                text("UPDATE users SET xp = :xp, level = :lvl WHERE id = :uid"),
                {"xp": xp, "lvl": lvl, "uid": uid},
            )
            print(f"Пользователь id={uid}: xp={xp}, level={lvl}")

        # 4. Удаление TrickLog
        if CONFIG["delete_tricklog_id"]:
            log_id = CONFIG["delete_tricklog_id"]
            await session.execute(
                text("DELETE FROM trick_logs WHERE id = :id"), {"id": log_id}
            )
            print(f"Удален TrickLog с id={log_id}")

        # 5. Удаление workout.id=1 из всех связанных таблиц
        if CONFIG["delete_workout_id"]:
            wid = CONFIG["delete_workout_id"]
            await session.execute(
                text("DELETE FROM trick_logs WHERE workout_id = :wid"), {"wid": wid}
            )
            print(f"Удалены TrickLog с workout_id={wid}")
            await session.execute(
                text("DELETE FROM workout_registrations WHERE workout_id = :wid"),
                {"wid": wid},
            )
            print(f"Удалены WorkoutRegistration с workout_id={wid}")
            await session.execute(
                text("DELETE FROM workout_participants WHERE workout_id = :wid"),
                {"wid": wid},
            )
            print(f"Удалены WorkoutParticipant с workout_id={wid}")
            await session.execute(
                text("DELETE FROM workouts WHERE id = :wid"), {"wid": wid}
            )
            print(f"Удалена тренировка workout с id={wid}")

        await session.execute(text("PRAGMA foreign_keys = ON"))
        await session.commit()
        print("Изменения успешно применены!")


if __name__ == "__main__":
    asyncio.run(apply_changes())