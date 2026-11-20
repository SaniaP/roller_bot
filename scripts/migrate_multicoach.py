"""Миграция БД для мульти-тренерства и комьюнити.

Безопасно добавляет новые колонки/таблицы (идемпотентно) и переносит
существующие данные: создает личное Community для главного тренера
(CHIEF_COACH_ID из config.py) и привязывает всех текущих активных
учеников (role == 'student', is_approved=True) и их прошедшие
тренировки к нему, чтобы ни один студент не потерялся.

Запуск:  python scripts/migrate_multicoach.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from config import CHIEF_COACH_ID, DATABASE_URL
from database import engine, async_session, Base
import models  # noqa: F401  — регистрация моделей
from models import Community, CommunityCoach, CoachStudent, User, Workout

IS_SQLITE = DATABASE_URL.startswith("sqlite")


async def table_exists(conn, name: str) -> bool:
    if IS_SQLITE:
        res = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name=:n"),
            {"n": name},
        )
    else:
        res = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name=:n"
            ),
            {"n": name},
        )
    return res.scalar_one_or_none() is not None


async def column_exists(conn, table: str, column: str) -> bool:
    if IS_SQLITE:
        res = await conn.execute(text(f"PRAGMA table_info({table})"))
        cols = {row[1] for row in res.fetchall()}
    else:
        res = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name=:t"
            ),
            {"t": table},
        )
        cols = {row[0] for row in res.fetchall()}
    return column in cols


async def add_column_if_missing(conn, table: str, ddl: str, column: str):
    if not await column_exists(conn, table, column):
        await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))
        print(f"  ✅ Добавлена колонка {table}.{column}")
    else:
        print(f"  ⏭️  Колонка {table}.{column} уже есть")


async def migrate_schema():
    print("⏳ Обновление схемы БД...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Колонки users.is_solo
        await add_column_if_missing(
            conn, "users", "is_solo BOOLEAN DEFAULT 0", "is_solo"
        )
        # Колонки workouts.coach_id / workouts.is_solo
        await add_column_if_missing(conn, "workouts", "coach_id INTEGER", "coach_id")
        await add_column_if_missing(
            conn, "workouts", "is_solo BOOLEAN DEFAULT 0", "is_solo"
        )

        # Таблицы communities / community_coach create_all уже создал,
        # но убедимся явно (для старых БД, где models не импортирован).
        if not await table_exists(conn, "communities"):
            await conn.execute(
                text(
                    "CREATE TABLE communities ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "name VARCHAR(100) NOT NULL, "
                    "created_at DATETIME)"
                )
            )
            print("  ✅ Создана таблица communities")
        if not await table_exists(conn, "community_coach"):
            await conn.execute(
                text(
                    "CREATE TABLE community_coach ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "community_id INTEGER NOT NULL REFERENCES communities(id) ON DELETE CASCADE, "
                    "coach_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE)"
                )
            )
            print("  ✅ Создана таблица community_coach")
    print("✅ Схема обновлена.\n")


async def migrate_data(session: AsyncSession):
    print("⏳ Перенос существующих данных...")

    # --- Ищем главного тренера по CHIEF_COACH_ID (telegram_id) ---
    chief = None
    if CHIEF_COACH_ID:
        chief = (
            await session.execute(
                select(User).where(User.telegram_id == CHIEF_COACH_ID)
            )
        ).scalar_one_or_none()
    if chief is None:
        # Фолбэк: первый тренер в БД
        chief = (
            await session.execute(
                select(User).where(User.role == "coach").order_by(User.id)
            )
        ).scalars().first()

    if chief is None:
        print("⚠️  Тренер не найден — перенос данных пропущен.")
        return

    # --- Личное сообщество главного тренера ---
    existing_link = (
        await session.execute(
            select(CommunityCoach).where(CommunityCoach.coach_id == chief.id)
        )
    ).scalar_one_or_none()

    if existing_link:
        community = (
            await session.execute(
                select(Community).where(Community.id == existing_link.community_id)
            )
        ).scalar_one()
        print(f"⏭️  У тренера '{chief.full_name}' уже есть комьюнити #{community.id}")
    else:
        community = Community(
            name=f"Группа тренера {chief.full_name}",
            created_at=datetime.now(timezone.utc),
        )
        session.add(community)
        await session.flush()
        session.add(CommunityCoach(community_id=community.id, coach_id=chief.id))
        print(f"✅ Создано комьюнити #{community.id} '{community.name}'")

    # --- Привязываем активных учеников через CoachStudent ---
    students = (
        (
            await session.execute(
                select(User).where(User.role == "student", User.is_approved.is_(True))
            )
        )
        .scalars()
        .all()
    )

    linked = 0
    for student in students:
        exists = (
            await session.execute(
                select(CoachStudent).where(
                    CoachStudent.coach_id == chief.id,
                    CoachStudent.student_id == student.id,
                )
            )
        ).scalar_one_or_none()
        if not exists:
            session.add(CoachStudent(coach_id=chief.id, student_id=student.id))
            linked += 1
    print(f"✅ Привязано учеников к тренеру: {linked} (из {len(students)})")

    # --- Прошедшие тренировки -> coach_id главного тренера ---
    workouts = (
        (await session.execute(select(Workout).where(Workout.coach_id.is_(None))))
        .scalars()
        .all()
    )
    for w in workouts:
        w.coach_id = chief.id
        w.is_solo = False
    print(f"✅ Тренировок привязано к тренеру: {len(workouts)}")

    await session.commit()
    print("✅ Перенос данных завершен.\n")


async def main():
    print(f"🗄️  База данных: {DATABASE_URL}")
    await migrate_schema()
    async with async_session() as session:
        await migrate_data(session)
    print("🎉 Миграция завершена успешно!")


if __name__ == "__main__":
    asyncio.run(main())
