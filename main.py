import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN as TOKEN
# Подключаем нашего созданного охранника из папки middlewares
from middlewares.db_middleware import DbSessionMiddleware  

# ИМПОРТИРУЕМ НАШ РОУТЕР и роутер тренера
from handlers.common import router as common_router  
from handlers.coach import router as coach_router
from handlers.student import router as student_router

# Добавили импорт движка и базы моделей
from database import engine, Base 

async def main():
    # Настраиваем логирование в консоль
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        stream=sys.stdout
    )
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    #  СИНХРОНИЗАЦИЯ СТРУКТУРЫ БД 
    # Проверяем, появились ли в models.py новые таблицы. 
    # Если появились — бот аккуратно достроит их, не трогая старые данные.
    print("⏳ Синхронизация структуры базы данных (актуализация таблиц)...")
    async with engine.begin() as conn:
        # Эта команда посмотрит на все модели из models.py и создаст таблицы, если их нет
        await conn.run_sync(Base.metadata.create_all)

        # Легкая миграция: SQLite не достраивает колонки существующих таблиц через create_all,
        # поэтому новые колонки моделей (например, trick_logs.needs_review) добавляем вручную
        import sqlite3
        from models import TrickLog

        db_path = str(engine.url).split("///")[-1]  # sqlite+aiosqlite:///roller_school.db -> roller_school.db
        sync_conn = sqlite3.connect(db_path)
        existing_cols = {row[1] for row in sync_conn.execute("PRAGMA table_info(trick_logs)")}
        if existing_cols:
            model_cols = {c.name for c in TrickLog.__table__.columns}
            for col in model_cols - existing_cols:
                col_type = "INTEGER" if col == "needs_review" else "REAL"
                sync_conn.execute(f"ALTER TABLE trick_logs ADD COLUMN {col} {col_type} DEFAULT 0")
                print(f"   ➕ Добавлена колонка trick_logs.{col}")
        sync_conn.commit()
        sync_conn.close()
    print("✅ Структура базы данных успешно синхронизирована!")


    # Инициализируем бот с поддержкой HTML-разметки в сообщениях
    bot = Bot(
        token=TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    
    # Хранилище состояний в оперативной памяти (блокнот памяти FSM)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # ВАЖНО: Нанимаем охранника! Теперь база данных автоматически едет в хэндлеры
    # для сообщений и  инлайн-кнопок 
    dp.message.middleware(DbSessionMiddleware())

    dp.callback_query.middleware(DbSessionMiddleware())

    # РЕГИСТРИРУЕМ РОУТЕР
    # Передаем диспетчеру списки хэндлеров, чтобы он знал, как реагировать 
    # на команды, сообщения и кнопки пользователей.

  
    dp.include_router(coach_router)
    dp.include_router(student_router)
    dp.include_router(common_router)
    # Сообщение в консоль о старте
    print("🚀 Бот роллер-школы успешно запущен и готов к работе!")

    # Запуск бота (пропускаем накопленные апдейты при выключенном состоянии)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот остановлен!")