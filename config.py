import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHIEF_COACH_ID = int(os.getenv("CHIEF_COACH_ID", 0))

DATABASE_URL = "sqlite+aiosqlite:///roller_school.db"