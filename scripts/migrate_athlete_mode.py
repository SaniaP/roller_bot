"""
Миграция: «Тренер в роли спортсмена за комьюнити» (ЭТАП 1)

Добавляет:
  - users.athlete_community_id  (FK -> communities.id, ON DELETE SET NULL)
  - workouts.community_id       (FK -> communities.id, ON DELETE SET NULL, индекс)

SQLite умеет добавлять nullable-колонку с REFERENCES через обычный
ALTER TABLE ADD COLUMN (запрещено только NOT NULL / non-const default),
поэтому здесь не нужен тяжёлый batch-copy. Если бы колонка требовала
NOT NULL или сложный constraint — использовали бы op.batch_alter_table
из Alembic (создание *_new, копирование данных, rename). ВАЖНО: сделать
бэкап roller_school.db перед запуском.

Запуск:  python -m scripts.migrate_athlete_mode   (из папки roller_bot)
"""

import asyncio
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import models  # noqa: F401 — регистрирует таблицы в metadata
from database import engine

DB_PATH = str(engine.url).split("///")[-1]


def _sync_sqlite_migration() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        user_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if "athlete_community_id" not in user_cols:
            conn.execute(
                "ALTER TABLE users ADD COLUMN athlete_community_id INTEGER "
                "REFERENCES communities(id) ON DELETE SET NULL"
            )
            print(
                "   ➕ Добавлена колонка users.athlete_community_id (FK -> communities)"
            )
        else:
            print("   ✔ users.athlete_community_id уже существует")

        workout_cols = {row[1] for row in conn.execute("PRAGMA table_info(workouts)")}
        if "community_id" not in workout_cols:
            conn.execute(
                "ALTER TABLE workouts ADD COLUMN community_id INTEGER "
                "REFERENCES communities(id) ON DELETE SET NULL"
            )
            print("   ➕ Добавлена колонка workouts.community_id (FK -> communities)")
        else:
            print("   ✔ workouts.community_id уже существует")

        indexes = {row[1] for row in conn.execute("PRAGMA index_list(workouts)")}
        if "ix_workouts_community_id" not in indexes:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_workouts_community_id "
                "ON workouts (community_id)"
            )
            print("   ➕ Добавлен индекс ix_workouts_community_id")
        else:
            print("   ✔ Индекс ix_workouts_community_id уже существует")

        conn.commit()
    finally:
        conn.close()


async def main():
    print("⏳ Миграция режима спортсмена у тренера...")
    _sync_sqlite_migration()
    print("✅ Миграция завершена!")


if __name__ == "__main__":
    asyncio.run(main())
