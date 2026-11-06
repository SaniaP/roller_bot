"""
Скрипт полной замены справочника трюков.

Что делает:
  1. Удаляет все старые трюки (tricks)
  2. Удаляет все логи учеников (trick_logs)
  3. Удаляет записи учеников на тренировки (workout_registrations)
  4. Удаляет устаревшую таблицу combo_bonuses, если она существует
  5. Импортирует трюки из tricks.csv (UTF-8 или cp1251)

Запуск:
  python import_tricks.py
"""

import asyncio
import csv
import os

from sqlalchemy import delete, text

from database import async_session, engine
from models import User, Trick, TrickLog, Workout, WorkoutRegistration

CSV_FILE_PATH = "tricks.csv"


async def import_tricks_from_csv():
    if not os.path.exists(CSV_FILE_PATH):
        print(f"❌ Файл {CSV_FILE_PATH} не найден! Положите его в папку со скриптом.")
        return

    print("⏳ Запуск полной замены справочника трюков...")

    async with async_session() as session:
        # 1. Очищаем таблицы с трюками, логами и учениками
        print("→ Удаляем старые логи (trick_logs)...")
        await session.execute(delete(TrickLog))

        print("→ Удаляем записи учеников (workout_registrations)...")
        await session.execute(delete(WorkoutRegistration))

        print("→ Удаляем старые трюки (tricks)...")
        await session.execute(delete(Trick))

        print("→ Удаляем тренировки (workouts)...")
        await session.execute(delete(Workout))

        print("→ Удаляем учеников (users)...")
        await session.execute(delete(User))

        await session.flush()

        # 2. Определяем кодировку файла
        # utf-8-sig читает и обычный UTF-8, и UTF-8 с BOM (BOM отрезается,
        # иначе первая колонка читается как '\ufeffname' вместо 'name')
        file_encoding = "utf-8-sig"
        try:
            with open(CSV_FILE_PATH, mode="r", encoding="utf-8-sig") as test_f:
                test_f.read(2048)
        except UnicodeDecodeError:
            file_encoding = "cp1251"
            print("ℹ️ Файл сохранен в кодировке Windows (cp1251), переключаюсь на неё.")

        tricks_to_add = []
        with open(CSV_FILE_PATH, mode="r", encoding=file_encoding) as f:
            # Автоопределение разделителя (запятая или точка с запятой)
            sample = f.read(2048)
            f.seek(0)
            delimiter = ";" if ";" in sample else ","

            reader = csv.DictReader(f, delimiter=delimiter)

            for row_number, row in enumerate(reader, start=2):
                try:
                    # Безопасная конвертация дробей (запятая -> точка)
                    def parse_float(val, default=1.0):
                        if not val or val.strip() == "":
                            return default
                        return float(val.replace(",", ".").strip())

                    # Безопасная конвертация целых чисел
                    def parse_int(val, default=1):
                        if not val or val.strip() == "":
                            return default
                        return int(float(val.replace(",", ".").strip()))

                    trick = Trick(
                        name=row["name"].strip(),
                        category=row.get("category", "others").strip() or "others",
                        trick_tier=parse_int(row.get("trick_tier"), 1),
                        base_tech_points=parse_float(row.get("base_tech_points"), 6.0),
                        base_xp=parse_int(row.get("base_xp"), 10),
                        measurement_type=row.get("measurement_type", "cones").strip()
                        or "cones",
                        base_quantity=parse_int(row.get("base_quantity"), 4),
                        extra_bonus_pct=parse_float(row.get("extra_bonus_pct"), 0.05),
                        coeff_50=parse_float(row.get("coeff_50"), 1.0),
                        coeff_80=parse_float(row.get("coeff_80"), 1.0),
                        coeff_120=parse_float(row.get("coeff_120"), 1.0),
                    )
                    tricks_to_add.append(trick)

                except KeyError as ke:
                    print(f"❌ Ошибка в структуре файла! Отсутствует колонка: {ke}")
                    print(f"Найденные колонки: {list(row.keys())}")
                    print(
                        "Убедитесь, что первая строка CSV содержит правильные имена полей."
                    )
                    return
                except Exception as e:
                    print(
                        f"⚠️ Ошибка обработки строки {row_number}: {e}. Строка пропущена."
                    )

        # 3. Сохраняем новые трюки пачкой
        if tricks_to_add:
            session.add_all(tricks_to_add)
            await session.commit()
            print(
                f"✅ Успешно импортировано трюков: {len(tricks_to_add)} шт. Справочник полностью обновлен!"
            )
        else:
            print("⚠️ Новых трюков для добавления не найдено. Транзакция отменена.")

    # 4. Удаляем устаревшую таблицу combo_bonuses (вне основной транзакции)
    print("→ Удаляем устаревшую таблицу combo_bonuses (если существует)...")
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS combo_bonuses"))
    print("✅ Таблица combo_bonuses удалена.")

    print("🎉 Готово!")


if __name__ == "__main__":
    asyncio.run(import_tricks_from_csv())
