import os
from dotenv import load_dotenv

load_dotenv()

# ===== Окружение =====
# На Amvera в панели задано ENVIRONMENT=production.
# Локально переменной нет → "local".
ENVIRONMENT = os.getenv("ENVIRONMENT", "local")
IS_PRODUCTION = ENVIRONMENT == "production"

# ===== Переключатель ботов (только для локали) =====
# На Amvera USE_TEST_BOT не задаётся вовсе → false.
USE_TEST_BOT = os.getenv("USE_TEST_BOT", "false").lower() == "true"

# ===== Защита: тестовый бот не должен работать на Amvera =====
if IS_PRODUCTION and USE_TEST_BOT:
    raise RuntimeError(
        "Обнаружен USE_TEST_BOT=true в production! "
        "Проверьте переменные окружения в панели Amvera."
    )

# ===== Токен =====
# На Amvera — BOT_TOKEN (как было в оригинале).
# Локально — BOT_TOKEN_TEST (если USE_TEST_BOT=true), иначе BOT_TOKEN.
if IS_PRODUCTION:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
else:
    BOT_TOKEN = os.getenv("BOT_TOKEN_TEST") if USE_TEST_BOT else os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError(
        f"Не найден токен бота! ENVIRONMENT={ENVIRONMENT}, "
        f"USE_TEST_BOT={USE_TEST_BOT}. "
        f"Проверьте BOT_TOKEN (Amvera) / BOT_TOKEN_TEST (локально)."
    )

# ===== Главный тренер =====
CHIEF_COACH_ID = int(os.getenv("CHIEF_COACH_ID") or 0)

# ===== База данных =====
if IS_PRODUCTION:
    DATABASE_URL = "sqlite+aiosqlite:////data/roller_school.db"
else:
    DATABASE_URL = "sqlite+aiosqlite:///roller_school.db"

# ===== Лог при старте =====
print(f"[config] ENVIRONMENT={ENVIRONMENT}, IS_PRODUCTION={IS_PRODUCTION}, "
      f"USE_TEST_BOT={USE_TEST_BOT}, "
      f"token={'TEST' if (USE_TEST_BOT and not IS_PRODUCTION) else 'MAIN'}, "
      f"DB={DATABASE_URL}")