from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from config import DATABASE_URL

# Создаем асинхронный движок. echo=True заставит Python выводить все SQL-запросы в консоль.
engine = create_async_engine(DATABASE_URL, echo=True)

# Фабрика сессий. Через сессии мы будем делать запросы к БД (добавлять, обновлять, читать)
async_session = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

# Базовый класс, от которого у нас будут наследоваться все модели (таблицы)
class Base(DeclarativeBase):
    pass

# Генератор сессий БД. Обеспечивает изолированную сессию для каждого запроса/пользователя.
# Именно здесь используется асинхронный контекст, чтобы не блокировать бота при работе с БД.
async def get_db():
    async with async_session() as session:
        yield session               