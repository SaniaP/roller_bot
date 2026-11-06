import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHIEF_COACH_ID = int(os.getenv("CHIEF_COACH_ID", 0))

# Проверяем, запущен ли бот на сервере Amvera (сервер всегда создает переменную AMVERA_APP_ID)
if os.getenv("AMVERA_APP_ID"):
    # Путь для сервера (с 4 слэшами для абсолютного пути)
    DATABASE_URL = "sqlite+aiosqlite:////data/roller_school.db"
else:
    # Путь для вашего локального ноутбука (обычный файл в папке проекта)
    DATABASE_URL = "sqlite+aiosqlite:///roller_school.db"