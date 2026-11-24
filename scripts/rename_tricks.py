import os
import sys

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)
os.chdir(_root)

# Запуск: python -m scripts.rename_tricks

import asyncio

from sqlalchemy import select

from database import async_session
from models import Trick

RENAME_MAP = {
    "NELSON / BACK / Хлеборезка": "NELSON / BACK NELSON",
    "SITTING HT SNAKE / б. машинка": "SITTING HT SNAKE",
    "5 WHEELS SITTING / м. машинка": "5 WHEELS SITTING",
    "WHEELING SHIFTS / COUNTERSHIFTS SERIES": "WHEELING SHIFTS",
    "WHEELING FLIP / DAYNIGHT SERIES": "WHEELING FLIP / DAYNIGHTS",
    "FLAT SHIFTS / COUNTERSHIFTS SERIES": "FLAT SHIFTS",
    "H one wheel / одно колесо": "H one wheel",
    "Т one wheel / одно колесо": "Т one wheel",
    "FLAT FLIP / DAYNIGHT SERIES": "FLAT FLIP / DAYNIGHTS",
    "HT SNAKE / 2 колеса": "HT SNAKE",
    "ТT SNAKE / 2 носка": "ТT SNAKE",
    "SNAKE / монолайн/ крис-кросс": "SNAKE",
    "CROSS / крис-кросс": "CROSS",
    "FISH / волна": "FISH",
    "NELSON SNAKE/ детская хлеборезка": "NELSON SNAKE",
    "NELSON SNAKE BACK/ детская хлеборезка": "NELSON SNAKE BACK",
}


async def rename():
    async with async_session() as session:
        tricks = (await session.execute(select(Trick))).scalars().all()
        by_name = {t.name: t for t in tricks}

        renamed, missing = 0, 0
        for old_name, new_name in RENAME_MAP.items():
            trick = by_name.get(old_name)
            if trick is None:
                print(f"⚠️ Не найден в БД: '{old_name}'")
                missing += 1
                continue
            if by_name.get(new_name) is not None and trick.name != new_name:
                print(f"⚠️ Конфликт: '{new_name}' уже существует, пропуск '{old_name}'")
                continue
            trick.name = new_name
            by_name[new_name] = trick
            print(f"✏️ '{old_name}' -> '{new_name}'")
            renamed += 1

        if renamed:
            await session.commit()
        print(f"\n✅ Переименовано: {renamed}, не найдено: {missing}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    asyncio.run(rename())