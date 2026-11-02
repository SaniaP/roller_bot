import asyncio
from sqlalchemy import delete, select, inspect
# Импортируем вашу фабрику сессий
from database import async_session
# Импортируем модель User
import models

async def print_any_table(table_name: str, filter_column: str = None, filter_value: str = None):
    # 1. Пытаемся найти модель по имени класса таблицы
    model = getattr(models, table_name, None)
    if not model:
        print(f"❌ Модель с именем '{table_name}' не найдена в модуле models.")
        return

    # Получаем список ТОЛЬКО физических колонок модели
    mapper = inspect(model)
    columns = [col.key for col in mapper.columns]

    # Проверка фильтра, если он передан
    if filter_column and filter_column not in columns:
        print(f"❌ В таблице '{table_name}' нет поля '{filter_column}'. Доступные поля: {', '.join(columns)}")
        return

    async with async_session() as session:
        # 3. Строим запрос к базе данных
        stmt = select(model)
        
        # Если передан фильтр — добавляем WHERE
        if filter_column and filter_value is not None:
            # Получаем сам объект колонки для фильтрации
            db_column = getattr(model, filter_column)
            
            # Приведение типов для поиска:
            # Если колонка имеет тип Integer, пробуем сконвертировать строку от пользователя в int
            from sqlalchemy import Integer
            if isinstance(db_column.type, Integer):
                try:
                    filter_value = int(filter_value)
                except ValueError:
                    print(f"❌ Значение '{filter_value}' невозможно привести к числу для поля '{filter_column}'.")
                    return
            
            stmt = stmt.where(db_column == filter_value)

        # Если у таблицы есть колонка 'id', сортируем по ней
        if "id" in columns:
            stmt = stmt.order_by(model.id)
            
        result = await session.execute(stmt)
        rows = result.scalars().all()

        if not rows:
            print(f"📭 Записей не найдено.")
            return

        # 4. Динамически рассчитываем ширину колонок на основе данных
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

        # 5. Строим динамический формат для строки
        header_format = " ".join([f"{{:<{col_widths[col] + 2}}}" for col in columns])
        total_width = sum(col_widths.values()) + (len(columns) * 2) - 1
        line = "=" * total_width

        # 6. Вывод таблицы в консоль
        print(f"\n{line}")
        print(header_format.format(*[col.upper() for col in columns]))
        print(line)
        
        for row_data in prepared_rows:
            print(header_format.format(*[row_data[col] for col in columns]))
            
        print(f"{line}\nВсего выведено записей: {len(rows)}\n")


async def main():
    print("Доступные таблицы обычно соответствуют классам в файле models.py (например: User, Order, Product)")
    table_name = input("Введите имя таблицы (класса модели) для печати: ").strip()
    
    if not table_name:
        return
        
    # Сначала выводим всю таблицу
    await print_any_table(table_name)
    
    # Предлагаем сделать точечный поиск
    print("--- Поиск конкретной записи (необязательно) ---")
    filter_column = input("Введите имя поля для фильтрации (или нажмите Enter для пропуска): ").strip()
    
    if filter_column:
        filter_value = input(f"Введите искомое значение для поля '{filter_column}': ").strip()
        print(f"\n🔍 Выполняю поиск в '{table_name}' где '{filter_column}' == '{filter_value}'...")
        await print_any_table(table_name, filter_column=filter_column, filter_value=filter_value)

if __name__ == "__main__":
    asyncio.run(main())