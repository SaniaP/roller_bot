from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from config import DATABASE_URL

# Создаем асинхронный движок. echo=True заставит Python выводить все SQL-запросы в консоль.
# Это мега-круто для обучения — ты будешь видеть, как "под капотом" создаются таблицы.
engine = create_async_engine(DATABASE_URL, echo=True)

# Фабрика сессий. Через сессии мы будем делать запросы к БД (добавлять, обновлять, читать)
async_session = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

# Базовый класс, от которого у нас будут наследоваться все модели (таблицы)
class Base(DeclarativeBase):
    pass

# Помощник для получения сессии в коде бота
async def get_db():
    async with async_session() as session:
        yield session