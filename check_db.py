"""Проверка состояния таблиц после очистки."""
import asyncio
from sqlalchemy import text, inspect
from database import async_session, engine


async def check_tables():
    async with async_session() as session:
        tables = ["users", "workouts", "workout_registrations", "trick_logs", "workout_participants"]
        for table in tables:
            result = await session.execute(text(f"SELECT COUNT(*) FROM {table}"))
            count = result.scalar()
            print(f"{table}: {count} строк")


if __name__ == "__main__":
    asyncio.run(check_tables())