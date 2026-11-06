from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
#from config import DATABASE_URL
# Явный путь к загруженной базе на сервере для провери!
DATABASE_URL = "sqlite+aiosqlite:////data/roller_school.db"
# Создаем асинхронный движок. echo=True заставит Python выводить все SQL-запросы в консоль.


#тоже временно!
engine = create_async_engine(DATABASE_URL, echo=True)
import logging
logger = logging.getLogger("db_config")
logger.info(f"DB-CONFIG-V2 Инициализация движка. Файл: {__file__} | URL: {engine.url}")


# Фабрика сессий. Через сессии мы будем делать запросы к БД (добавлять, обновлять, читать)
async_session = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

# Базовый класс, от которого у нас будут наследоваться все модели (таблицы)
class Base(DeclarativeBase):
    pass
