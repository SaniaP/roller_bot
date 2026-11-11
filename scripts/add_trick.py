import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

"""Скрипт для добавления нового трюка в таблицу Trick."""
import asyncio
from database import async_session
from models import Trick


async def add_trick():
    async with async_session() as session:
        # Проверяем, не существует ли уже такой трюк
        existing = await session.execute(
            __import__('sqlalchemy').select(Trick).where(Trick.name == "Лимончики спиной")
        )
        if existing.scalar_one_or_none():
            print("Трюк 'Лимончики спиной' уже существует в базе.")
            return

        new_trick = Trick(
            name="Лимончики спиной",
            category="basic",
            trick_tier=2,
            base_tech_points=2.0,
            base_xp=2,
            measurement_type="cones",
            base_quantity=4,
            extra_bonus_pct=0.03,
            coeff_50=1.0,
            coeff_80=1.0,
            coeff_120=1.0,
        )
        session.add(new_trick)
        await session.commit()
        print(f"Трюк '{new_trick.name}' успешно добавлен с ID={new_trick.id}")


if __name__ == "__main__":
    asyncio.run(add_trick())