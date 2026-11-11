import asyncio, math
from sqlalchemy import select
from database import async_session
from models import Trick, TrickLog, User, XpPenalty


def calc_combo_step(prev, y):
    avg = (prev + y) / 2
    bump = 1.0 if y >= prev else 0.0
    diff = y - prev
    tv = max(0.0, 10.0 - abs(diff))
    return (avg + bump) * (1.0 + tv / 100.0)


async def main():
    ids = ["d0a2ac2e-f2ba-48ce-8d84-1aa", "b5f696e8-cedc-4352-bd47-849"]
    async with async_session() as s:
        for prefix in ids:
            logs = (
                (
                    await s.execute(
                        select(TrickLog).where(TrickLog.combo_id.like(prefix + "%"))
                    )
                )
                .scalars()
                .all()
            )
            if not logs:
                print(f"!!! combo {prefix}: логов не найдено")
                continue
            logs.sort(key=lambda l: l.combo_step or 0)
            print(
                f"\n=== combo {logs[0].combo_id} (student_id={logs[0].student_id}) ==="
            )
            student = await s.get(User, logs[0].student_id)
            print(f"student level = {student.level}")

            # баллы связки
            pts = logs[0].calculated_points
            for lg in logs[1:]:
                pts = calc_combo_step(pts, lg.calculated_points)
            pts = round(pts, 2)
            print(f"combo_points (Result) = {pts}")

            sum_base_xp = 0
            sum_base_tech = 0
            tiers = []
            for lg in logs:
                t = await s.get(Trick, lg.trick_id)
                print(
                    f"  step {lg.combo_step}: trick={t.name!r} qty={lg.quantity} "
                    f"log_pts={lg.calculated_points} log_xp={lg.calculated_xp} "
                    f"base_xp={t.base_xp} base_tech={t.base_tech_points} tier={t.trick_tier}"
                )
                sum_base_xp += t.base_xp
                sum_base_tech += t.base_tech_points
                tiers.append(t.trick_tier)

            ratio = sum_base_xp / sum_base_tech if sum_base_tech else 1.0
            avg_q = sum(lg.quantity for lg in logs) / len(logs)
            lb = 1.0 + 0.15 * max(0, len(logs) - 1)
            raw = pts * ratio * avg_q * 1.4 * lb
            print(f"ratio={ratio:.4f} avg_quantity={avg_q:.2f} length_bonus={lb:.2f}")
            print(f"raw_combo_xp (1.4 formula) = {raw:.2f}")

            avg_tier = round(sum(tiers) / len(tiers))
            diff = student.level - avg_tier
            mult = 1.0
            if diff > 9:
                mult = 0.01
            elif diff > 0:
                pen = await s.scalar(
                    select(XpPenalty).where(XpPenalty.level_difference == diff)
                )
                if pen:
                    mult = pen.xp_multiplier
            print(f"avg_tier={avg_tier} level_diff={diff} xp_multiplier={mult}")
            print(f"=> EXPECTED combo_xp = {int(raw * mult)}")


asyncio.run(main())


async def check_project_fn():
    """Проверяем фактическую функцию проекта: _combo_exp_map после правки."""
    from handlers.coach.stats import _combo_exp_map

    async with async_session() as s:
        for prefix in ["d0a2ac2e", "b5f696e8"]:
            logs = (
                (await s.execute(select(TrickLog).where(TrickLog.combo_id.like(prefix + "%"))))
                .scalars()
                .all()
            )
            student = await s.get(User, logs[0].student_id)
            pairs = [(lg, await s.get(Trick, lg.trick_id)) for lg in logs]
            mapping = await _combo_exp_map(pairs, student, s)
            print(f"project _combo_exp_map for {prefix}: {mapping}")


asyncio.run(check_project_fn())
