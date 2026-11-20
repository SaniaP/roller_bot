import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHIEF_COACH_ID = int(os.getenv("CHIEF_COACH_ID", 0))

# Получаем значение переменной, если её нет — ставим 'local'
ENV = os.getenv("ENVIRONMENT", "local")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if ENV == "production":
    # Путь для сервера Amvera
    DATABASE_URL = "sqlite+aiosqlite:////data/roller_school.db"
else:
    # Путь для локальной разработки (абсолютный: не зависит от текущей папки,
    # поэтому скрипты можно запускать и из scripts/)
    # DB_PATH = os.path.join(BASE_DIR, "roller_school.db").replace("\\", "/")
    DATABASE_URL = "sqlite+aiosqlite:///roller_school.db"
