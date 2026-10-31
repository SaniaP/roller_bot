import asyncio
from sqlalchemy import delete,select,inspect

# Импортируем вашу фабрику сессий
from database import async_session

# Импортируем модель User (если файл называется по-другому, например main.py, замените 'models')
import models
'''
async def print_users_table_one_line():
    async with async_session() as session:
        # Извлекаем вообще всех пользователей из базы
        stmt = select(User).order_by(User.id)
        result = await session.execute(stmt)
        users = result.scalars().all()

        if not users:
            print("📭 Таблица пользователей пуста.")
            return

        # 1. Шапка таблицы (задаем фиксированную ширину для каждой колонки)
        # Знаки < и > означают выравнивание по левому или правому краю, а цифра — ширину в символах
        header_format = "{:<4} {:<12} {:<8} {:<25} {:<13} {:<6} {:<5} {:<5}"
        
        print("\n" + "=" * 85)
        print(header_format.format("ID", "TG_ID", "ROLE", "FULL_NAME", "PHONE", "APP?", "LVL", "XP"))
        print("=" * 85)

        # 2. Вывод строк (каждый юзер — строго одна строка)
        for u in users:
            # Превращаем Boolean в наглядные символы ✅/❌ для компактности
            approved_status = "✅" if u.is_approved else "❌"
            
            # Обрабатываем возможные None, чтобы они не ломали форматирование текста
            tg_id = str(u.telegram_id) if u.telegram_id else "-"
            phone = str(u.phone) if u.phone else "-"
            full_name = u.full_name[:24] if len(u.full_name) > 24 else u.full_name # Обрезаем слишком длинные имена, чтобы не ломать строки

            print(header_format.format(
                u.id, 
                tg_id, 
                u.role, 
                full_name, 
                phone, 
                approved_status, 
                u.level, 
                u.xp
            ))
            
        print("=" * 85 + f"\nВсего записей: {len(users)}\n")

if __name__ == "__main__":
    asyncio.run(print_users_table_one_line())
'''
async def print_any_table(table_name: str):
    # 1. Пытаемся найти модель по имени класса таблицы
    model = getattr(models, table_name, None)
    if not model:
        print(f"❌ Модель с именем '{table_name}' не найдена в модуле models.")
        return

    async with async_session() as session:
        # 2. Получаем список всех колонок модели через inspect
        mapper = inspect(model)
        columns = [col.key for col in mapper.attrs]

        # 3. Делаем запрос к базе данных
        stmt = select(model)
        # Если у таблицы есть колонка 'id', сортируем по ней
        if "id" in columns:
            stmt = stmt.order_by(model.id)
            
        result = await session.execute(stmt)
        rows = result.scalars().all()

        if not rows:
            print(f"📭 Таблица '{table_name}' пуста.")
            return

        # 4. Динамически рассчитываем ширину колонок на основе данных
        # Изначально ставим ширину равную длине названия самой колонки
        col_widths = {col: len(col) for col in columns}
        
        # Подготавливаем данные для вывода (приводим к строкам, обрабатываем None и Boolean)
        prepared_rows = []
        for row in rows:
            row_data = {}
            for col in columns:
                val = getattr(row, col)
                # Красиво форматируем булевы значения
                if isinstance(val, bool):
                    val_str = "✅" if val else "❌"
                elif val is None:
                    val_str = "-"
                else:
                    val_str = str(val)
                
                # Ограничиваем слишком длинные тексты (например, > 30 символов), чтобы таблица не расползалась
                if len(val_str) > 30:
                    val_str = val_str[:27] + "..."
                    
                row_data[col] = val_str
                # Обновляем максимальную ширину для этой колонки
                if len(val_str) > col_widths[col]:
                    col_widths[col] = len(val_str)
            prepared_rows.append(row_data)

        # 5. Строим динамический формат для строки
        # Добавляем +2 для небольших отступов между колонками
        header_format = " ".join([f"{{:<{col_widths[col] + 2}}}" for col in columns])
        
        # Считаем общую длину разделительной линии
        total_width = sum(col_widths.values()) + (len(columns) * 2) - 1
        line = "=" * total_width

        # 6. Вывод таблицы в консоль
        print(f"\n{line}")
        print(header_format.format(*[col.upper() for col in columns]))
        print(line)

        for row_data in prepared_rows:
            print(header_format.format(*[row_data[col] for col in columns]))
            
        print(f"{line}\nВсего записей: {len(rows)}\n")

async def main():
    print("Доступные таблицы обычно соответствуют классам в файле models.py (например: User, Order, Product)")
    table_name = input("Введите имя таблицы (класса модели) для печати: ").strip()
    if table_name:
        await print_any_table(table_name)

if __name__ == "__main__":
    asyncio.run(main())