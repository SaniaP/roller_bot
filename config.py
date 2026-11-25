import os
from dotenv import load_dotenv

load_dotenv()

# ===== Окружение =====
ENV = os.getenv("ENV", "development")

# ===== Переключатель ботов =====
USE_TEST_BOT = os.getenv("USE_TEST_BOT", "false").lower() == "true"

# ===== Защита: тестовый бот не должен работать в production =====
if ENV == "production" and USE_TEST_BOT:
    raise RuntimeError(
        "Обнаружен USE_TEST_BOT=true в production-окружении! "
        "Проверьте переменные окружения в панели Amvera."
    )

# ===== Токен  и главный тренер=====
BOT_TOKEN = os.getenv("BOT_TOKEN_TEST") if USE_TEST_BOT else os.getenv("BOT_TOKEN_MAIN")
CHIEF_COACH_ID = int(os.getenv("CHIEF_COACH_ID", 0))

if not BOT_TOKEN:
    raise RuntimeError(
        f"Не найден токен бота! USE_TEST_BOT={USE_TEST_BOT}, ENV={ENV}. "
        f"Проверьте переменные BOT_TOKEN_MAIN / BOT_TOKEN_TEST."
    )

# ===== База данных =====
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if ENV == "production":
    # Путь для сервера Amvera
    DATABASE_URL = "sqlite+aiosqlite:////data/roller_school.db"
else:
    # Путь для локальной разработки (относительный, не зависит от текущей папки)
    DATABASE_URL = "sqlite+aiosqlite:///roller_school.db"

# ===== лог при старте (видно в логах Amvera) =====
# Можно  заменить на logger
print(f"[config] ENV={ENV}, USE_TEST_BOT={USE_TEST_BOT}, "
      f"token={'TEST' if USE_TEST_BOT else 'MAIN'}, DB={DATABASE_URL}")