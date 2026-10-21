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
from handlers.student import student_router

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

    # 👇 ДОБАВЛЯЕМ ЭТИ СТРОЧКИ ДЛЯ АВТОМАТИЧЕСКОГО СОЗДАНИЯ ТАБЛИЦ 👇
    print("⏳ Проверяем и создаем таблицы в базе данных...")
    async with engine.begin() as conn:
        # Эта команда посмотрит на все модели из models.py и создаст таблицы, если их нет
        await conn.run_sync(Base.metadata.create_all)
    print("✅ Все таблицы базы данных проверены/созданы!")


    # Инициализируем бот с поддержкой HTML-разметки в сообщениях
    bot = Bot(
        token=TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    
    # Хранилище состояний в оперативной памяти (блокнот памяти FSM)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # ВАЖНО: Нанимаем охранника! Теперь база данных автоматически едет в хэндлеры
    dp.message.middleware(DbSessionMiddleware())
    #для инлайн-кнопок
    dp.callback_query.middleware(DbSessionMiddleware())

    # РЕГИСТРИРУЕМ РОУТЕР (Этой строчкой мы говорим диспетчеру слушать команду /start)
    dp.include_router(common_router)
    #тоже для тренера и спротсмена
    dp.include_router(coach_router)
    dp.include_router(student_router)
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