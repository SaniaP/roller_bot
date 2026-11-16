# File: roller_bot/scripts/sync_tricks_csv.py
# Синхронизация таблицы Trick с CSV-файлом: обновление существующих, добавление новых
# Если отчёт выглядит правильно — запускаете с --apply 
# python sync_tricks_csv.py --apply
import os
import sys
import csv
import asyncio

_here = os.path.dirname(os.path.abspath(__file__))
_root = _here
while _root != os.path.dirname(_root):
    if os.path.exists(os.path.join(_root, "models.py")):
        break
    _root = os.path.dirname(_root)
if _root not in sys.path:
    sys.path.insert(0, _root)

os.chdir(_root)

from sqlalchemy import select
from database import async_session
from models import Trick

CSV_PATH = os.path.join(_root, "freestyle_slalom_tricks_ordered.csv")

FLOAT_FIELDS = {
    "base_tech_points",
    "extra_bonus_pct",
    "coeff_50",
    "coeff_80",
    "coeff_120",
}
INT_FIELDS = {"trick_tier", "base_xp", "base_quantity"}


def parse_row(row: dict) -> dict:
    """CSV с ';' и десятичной запятой -> dict со значениями нужных типов."""
    data = {}
    for key, value in row.items():
        key = (key or "").strip()
        if not key:
            continue
        value = (value or "").strip().replace(",", ".")
        if key in FLOAT_FIELDS:
            data[key] = float(value) if value else 0.0
        elif key in INT_FIELDS:
            data[key] = int(float(value)) if value else 0
        else:
            data[key] = value
    return data


def load_csv() -> dict[str, dict]:
    tricks = {}
    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f, delimiter=";"):
            data = parse_row(row)
            name = data.pop("name", "").strip()
            if name:
                tricks[name] = data
    return tricks


async def sync(dry_run: bool = True):
    csv_tricks = load_csv()
    print(f"📄 В CSV найдено трюков: {len(csv_tricks)}")

    # Нормализация имён: схлопываем пробелы, чтобы 'TT  CROSS' == 'TT CROSS'
    def norm(s: str) -> str:
        return " ".join(s.split())

    csv_by_norm = {norm(name): (name, fields) for name, fields in csv_tricks.items()}

    async with async_session() as session:
        db_tricks = (await session.execute(select(Trick))).scalars().all()
        db_by_name = {t.name: t for t in db_tricks}
        db_by_norm = {norm(t.name): t for t in db_tricks}

        updated, added, renamed, missing_in_csv = [], [], [], []

        for norm_name, (name, fields) in csv_by_norm.items():
            trick = db_by_norm.get(norm_name)
            if trick is None:
                added.append(name)
                if not dry_run:
                    session.add(Trick(name=name, **fields))
                continue
            if trick.name != name:
                renamed.append((trick.name, name))
                trick.name = name
            changed = {}
            for field, value in fields.items():
                old = getattr(trick, field)
                if isinstance(value, float):
                    is_same = old is not None and abs(float(old) - value) <= 1e-9
                else:
                    is_same = old == value
                if not is_same:
                    changed[field] = (old, value)
                    setattr(trick, field, value)
            if changed:
                updated.append((name, changed))

        for name in db_by_name:
            if norm(name) not in csv_by_norm:
                missing_in_csv.append(name)

        # Отчёт
        print(f"\n🔄 Обновлено: {len(updated)}")
        for name, changed in updated:
            details = ", ".join(f"{f}: {o} -> {n}" for f, (o, n) in changed.items())
            print(f"   • {name}: {details}")

        print(f"\n✏️ Переименовано (убраны лишние пробелы): {len(renamed)}")
        for old, new in renamed:
            print(f"   • '{old}' -> '{new}'")

        print(f"\n➕ Новых в CSV (нет в базе): {len(added)}")
        for name in added:
            print(f"   • {name}")

        print(f"\n⚠️ Есть в базе, но нет в CSV ({len(missing_in_csv)}): НЕ изменяются")
        for name in missing_in_csv:
            print(f"   • {name}")

        if dry_run:
            print(
                "\n🔍 DRY RUN — изменения не сохранены. Запустите с аргументом --apply для записи в БД."
            )
        else:
            await session.commit()
            print("\n✅ Изменения сохранены в базу.")


if __name__ == "__main__":
    # Консоль Windows (cp1251) не умеет эмодзи/кириллицу из print без этой строки
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    apply = "--apply" in sys.argv
    try:
        asyncio.run(sync(dry_run=not apply))
    except Exception:
        import traceback

        traceback.print_exc()
    finally:
        if not apply:
            input("Нажмите Enter для выхода...")
