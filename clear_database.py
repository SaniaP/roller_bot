"""Скрипт для очистки таблиц: User, Workout, WorkoutRegistration, TrickLog, WorkoutParticipant.
Справочные таблицы (Trick, LevelExperience, XpPenalty) не удаляются."""
import asyncio
from sqlalchemy import text
from database import async_session


async def clear_tables():
    async with async_session() as session:
        # Отключаем проверку внешних ключей для SQLite, чтобы можно удалять в любом порядке
        await session.execute(text("PRAGMA foreign_keys = OFF"))

        # Удаляем данные из таблиц в правильном порядке (сначала дочерние, потом родительские)
        print("Очистка TrickLog...")
        await session.execute(text("DELETE FROM trick_logs"))

        print("Очистка WorkoutRegistration...")
        await session.execute(text("DELETE FROM workout_registrations"))

        print("Очистка WorkoutParticipant...")
        await session.execute(text("DELETE FROM workout_participants"))

        print("Очистка Workout...")
        await session.execute(text("DELETE FROM workouts"))

        print("Очистка User...")
        await session.execute(text("DELETE FROM users"))

        # Включаем обратно проверку внешних ключей
        await session.execute(text("PRAGMA foreign_keys = ON"))

        await session.commit()
        print("✅ Все таблицы успешно очищены!")


if __name__ == "__main__":
    asyncio.run(clear_tables())