from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from database import async_session

class DbSessionMiddleware(BaseMiddleware):
    """Middleware для проброса асинхронной сессии SQLAlchemy в хэндлеры с контролем транзакций"""
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        async with async_session() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
                # Если хэндлер выполнился без ошибок, фиксируем все изменения (add, update, delete)
                await session.commit()
                return result
            except Exception as e:
                # Если в хэндлере произошла ошибка, откатываем транзакцию, чтобы не сломать БД
                await session.rollback()
                raise e