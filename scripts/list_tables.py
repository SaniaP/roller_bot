import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
from sqlalchemy import text
from database import async_session

async def f():
    async with async_session() as s:
        r = await s.execute(text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"))
        rows = r.fetchall()
        print(f"Таблиц ({len(rows)}):")
        for row in rows:
            print("  ", row[0])

asyncio.run(f())