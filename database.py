from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from config import DATABASE_URL
# Явный путь к загруженной базе на сервере для провери!
#DATABASE_URL = "sqlite+aiosqlite:////data/roller_school.db"       # ВРЕМЕННО ВЕРНУТЬ для ПЕРВОГО ЗАПУСКА ЗАВТРА
# Перед запуска на сервере вернуть эту строчку а №3 закоментить

#LALALLA
engine = create_async_engine(DATABASE_URL, echo=False)



# Фабрика сессий. Через сессии мы будем делать запросы к БД (добавлять, обновлять, читать)
async_session = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

# Базовый класс, от которого у нас будут наследоваться все модели (таблицы)
class Base(DeclarativeBase):
    pass
