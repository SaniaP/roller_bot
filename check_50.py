"""Проверка расчета итогов для workout_id=50 до/после правки."""

import asyncio

from sqlalchemy import select

from calculator import calculate_current_workout_totals
from database import async_session
from models import TrickLog, User


async def main():
    async with async_session() as session:
        logs = (
            await session.scalars(select(TrickLog).where(TrickLog.workout_id == 50))
        ).all()
        # группируем по ученику
        by_student = {}
        for log in logs:
            by_student.setdefault(log.student_id, []).append(log)
        for sid, st_logs in by_student.items():
            student = await session.get(User, sid)
            sport, xp = await calculate_current_workout_totals(
                st_logs, student, session
            )
            print(f"student_id={sid} ({student.level} lvl): sport={sport}, xp={xp}")
            for log in sorted(
                st_logs, key=lambda l: (l.combo_id or "", l.combo_step or 0)
            ):
                print(
                    f"  trick={log.trick_id} combo={log.combo_id} step={log.combo_step} "
                    f"pts={log.calculated_points} bonus={log.combo_bonus} xp={log.calculated_xp}"
                )


if __name__ == "__main__":
    asyncio.run(main())
