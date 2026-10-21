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

# ИМПОРТИРУЕМ НАШ РОУТЕР (Вместо старого комментария)
from handlers.common import router as common_router  

async def main():
    # Настраиваем логирование в консоль
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        stream=sys.stdout
    )
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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

    # РЕГИСТРИРУЕМ РОУТЕР (Этой строчкой мы говорим диспетчеру слушать команду /start)
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