import asyncio
from database import engine, Base
# Обязательно импортируем models, чтобы SQLAlchemy "увидела" наши таблицы перед созданием!
import models 

async def initialize_database():
    print("⏳ Запуск инициализации базы данных...")
    
    # Открываем асинхронное соединение с базой и создаем все таблицы
    # engine знает ссылку на БД, и создает файл
    async with engine.begin() as conn:
        # Команда run_sync помогает асинхронному движку выполнить команду создания таблиц
        await conn.run_sync(Base.metadata.create_all)
        
    print("✅ База данных успешно создана! Все таблицы сгенерированы.")

if __name__ == "__main__":
    # Запускаем асинхронную функцию инициализации
    asyncio.run(initialize_database())