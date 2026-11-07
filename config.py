import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHIEF_COACH_ID = int(os.getenv("CHIEF_COACH_ID", 0))

# Получаем значение переменной, если её нет — ставим 'local'
ENV = os.getenv("ENVIRONMENT", "local")

if ENV == "production":
    # Путь для сервера Amvera
    DATABASE_URL = "sqlite+aiosqlite:////data/roller_school.db"
else:
    # Путь для локальной разработки (на ноутбуке)
    DATABASE_URL = "sqlite+aiosqlite:///roller_school.db"