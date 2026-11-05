import asyncio
import csv
import os
from sqlalchemy import delete

# Импортируем ваши настройки базы и модели
from database import async_session
from models import Trick

# Путь к вашему CSV файлу
CSV_FILE_PATH = "tricks.csv"


async def import_tricks_from_csv():
    if not os.path.exists(CSV_FILE_PATH):
        print(f"❌ Файл {CSV_FILE_PATH} не найден! Положите его в папку со скриптом.")
        return

    print("⏳ Запуск полной замены справочника трюков...")

    async with async_session() as session:
        # 1. Очищаем таблицу tricks перед импортом
        print("→ Удаляем старые трюки из базы данных...")
        await session.execute(delete(Trick))
        await session.flush()  # Фиксируем удаление в рамках транзакции

        # 2. Читаем CSV файл
        print(f"→ Чтение файла {CSV_FILE_PATH} и импорт данных...")
        tricks_to_add = []

    print(f"→ Чтение файла {CSV_FILE_PATH} и импорт данных...")
    tricks_to_add = []

    # Сначала определяем кодировку файла, чтобы не было падений
    file_encoding = "utf-8"
    try:
        with open(CSV_FILE_PATH, mode="r", encoding="utf-8") as test_f:
            test_f.read(2048)
    except UnicodeDecodeError:
        file_encoding = "cp1251"
        print("ℹ️ Файл сохранен в кодировке Windows (cp1251), переключаюсь на неё.")

    with open(CSV_FILE_PATH, mode="r", encoding=file_encoding) as f:
        # Пытаемся автоматически определить разделитель (запятая или точка с запятой)
        sample = f.read(2048)
        f.seek(0)
        delimiter = ";" if ";" in sample else ","

        # Используем DictReader, чтобы обращаться к полям по названиям колонок
        reader = csv.DictReader(f, delimiter=delimiter)
            # Пытаемся автоматически определить разделитель (запятая или точка с запятой)
        sample = f.read(2048)
        f.seek(0)
        delimiter = ";" if ";" in sample else ","

            # Используем DictReader, чтобы обращаться к полям по названиям колонок
        reader = csv.DictReader(f, delimiter=delimiter)

        for row_number, row in enumerate(reader, start=2):
                try:
                    # Вспомогательная функция для безопасной конвертации дробей с заменой запятой на точку
                    def parse_float(val, default=1.0):
                        if not val or val.strip() == "":
                            return default
                        return float(val.replace(",", ".").strip())

                    # Вспомогательная функция для безопасной конвертации целых чисел
                    def parse_int(val, default=1):
                        if not val or val.strip() == "":
                            return default
                        return int(float(val.replace(",", ".").strip()))

                    # Создаем объект модели Trick
                    trick = Trick(
                        name=row["name"].strip(),
                        category=row.get("category", "others").strip() or "others",
                        trick_tier=parse_int(row.get("trick_tier"), 1),
                        base_tech_points=parse_float(row.get("base_tech_points"), 6.0),
                        base_xp=parse_int(row.get("base_xp"), 10),
                        measurement_type=row.get("measurement_type", "cones").strip() or "cones",
                        base_quantity=parse_int(row.get("base_quantity"), 4),
                        extra_bonus_pct=parse_float(row.get("extra_bonus_pct"), 0.05),
                        coeff_50=parse_float(row.get("coeff_50"), 1.0),
                        coeff_80=parse_float(row.get("coeff_80"), 1.0),
                        coeff_120=parse_float(row.get("coeff_120"), 1.0),
                    )
                    tricks_to_add.append(trick)

                except KeyError as ke:
                    print(f"❌ Ошибка в структуре файла! Отсутствует колонка: {ke}")
                    print("Убедитесь, что первая строка CSV содержит правильные имена полей.")
                    return
                except Exception as e:
                    print(f"⚠️ Ошибка обработки строки {row_number}: {e}. Строка пропущена.")

        # 3. Сохраняем новые трюки пачкой
        if tricks_to_add:
            session.add_all(tricks_to_add)
            await session.commit()
            print(f"✅ Успешно импортировано трюков: {len(tricks_to_add)} шт. Справочник полностью обновлен!")
        else:
            print("⚠️ Новых трюков для добавления не найдено. Транзакция отменена.")


if __name__ == "__main__":
    asyncio.run(import_tricks_from_csv())