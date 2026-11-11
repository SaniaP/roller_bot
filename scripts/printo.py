import os
import sys

# Поднимаемся от файла вверх, пока не найдём models.py — это и есть корень проекта
_here = os.path.dirname(os.path.abspath(__file__))
_root = _here
while _root != os.path.dirname(_root):
    if os.path.exists(os.path.join(_root, "models.py")):
        break
    _root = os.path.dirname(_root)
if _root not in sys.path:
    sys.path.insert(0, _root)

import asyncio
from sqlalchemy import select, inspect, Integer, Boolean
from sqlalchemy.orm import DeclarativeBase
os.chdir(_root)
from database import async_session
import models


async def print_any_table(table_name: str, filter_column: str = None, filter_value: str = None):
    model = getattr(models, table_name, None)
    if model is None or not (isinstance(model, type) and issubclass(model, DeclarativeBase)):
        print(f"❌ '{table_name}' не является моделью SQLAlchemy в модуле models.")
        return

    mapper = inspect(model)
    columns = [col.key for col in mapper.columns]

    if filter_column and filter_column not in columns:
        print(f"❌ В таблице '{table_name}' нет поля '{filter_column}'. Доступные поля: {', '.join(columns)}")
        return

    async with async_session() as session:
        stmt = select(model)

        if filter_column and filter_value is not None:
            db_column = getattr(model, filter_column)
            if isinstance(db_column.type, Integer):
                try:
                    filter_value = int(filter_value)
                except ValueError:
                    print(f"❌ Значение '{filter_value}' невозможно привести к числу для поля '{filter_column}'.")
                    return
            elif isinstance(db_column.type, Boolean):
                lowered = filter_value.strip().lower()
                if lowered in ("true", "1", "да", "yes"):
                    filter_value = True
                elif lowered in ("false", "0", "нет", "no"):
                    filter_value = False
                else:
                    print(f"❌ Значение '{filter_value}' не похоже на bool для поля '{filter_column}'.")
                    return
            stmt = stmt.where(db_column == filter_value)

        if "id" in columns:
            stmt = stmt.order_by(model.id)

        result = await session.execute(stmt)
        rows = result.scalars().all()

        if not rows:
            print("📭 Записей не найдено.")
            return

        # Динамически рассчитываем ширину колонок
        col_widths = {col: len(col) for col in columns}
        prepared_rows = []

        for row in rows:
            row_data = {}
            for col in columns:
                val = getattr(row, col)
                if isinstance(val, bool):
                    val_str = "✅" if val else "❌"
                elif val is None:
                    val_str = "-"
                else:
                    val_str = str(val)

                if len(val_str) > 30:
                    val_str = val_str[:27] + "..."

                row_data[col] = val_str
                if len(val_str) > col_widths[col]:
                    col_widths[col] = len(val_str)
            prepared_rows.append(row_data)

        header_format = " ".join([f"{{:<{col_widths[col] + 2}}}" for col in columns])
        total_width = sum(col_widths.values()) + (len(columns) * 2) + (len(columns) - 1)
        line = "=" * total_width

        print(f"\n{line}")
        print(header_format.format(*[col.upper() for col in columns]))
        print(line)
        for row_data in prepared_rows:
            print(header_format.format(*[row_data[col] for col in columns]))
        print(f"{line}\nВсего выведено записей: {len(rows)}\n")


async def main():
    print("Доступные таблицы обычно соответствуют классам в файле models.py (например: User, Order, Product)", flush=True)
    table_name = input("Введите имя таблицы (класса модели) для печати: ").strip()

    if not table_name:
        return

    await print_any_table(table_name)

    print("--- Поиск конкретной записи (необязательно) ---", flush=True)
    filter_column = input("Введите имя поля для фильтрации (или нажмите Enter для пропуска): ").strip()

    if filter_column:
        filter_value = input(f"Введите искомое значение для поля '{filter_column}': ").strip()
        print(f"\n🔍 Выполняю поиск в '{table_name}' где '{filter_column}' == '{filter_value}'...", flush=True)
        await print_any_table(table_name, filter_column=filter_column, filter_value=filter_value)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        import traceback
        traceback.print_exc()
        input("Нажмите Enter для выхода...")