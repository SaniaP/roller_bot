import asyncio
from sqlalchemy import delete

# Импортируем вашу фабрику сессий
from database import async_session

# Импортируем модель User (если файл называется по-другому, например main.py, замените 'models')
from models import User


async def main():
    # Открываем асинхронную сессию
    async with async_session() as session:
        # Выполняем удаление
        result = await session.execute(delete(User).where(User.full_name == "Груша 🍐"))
        # Обязательно сохраняем изменения в БД
        await session.commit()

        print(f"🎉 Успешно! Удалено пользователей с именем 'груша': {result.rowcount}")


if __name__ == "__main__":
    asyncio.run(main())
