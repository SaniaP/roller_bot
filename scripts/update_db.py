import os
import sqlite3
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

ENV = os.getenv("ENVIRONMENT", "local")
CHIEF_COACH_ID = int(os.getenv("CHIEF_COACH_ID", 0))

if ENV == "production":
    DB_FILE_PATH = "/data/roller_school.db"
else:
    DB_FILE_PATH = "roller_school.db"

def upgrade():
    if not os.path.exists(DB_FILE_PATH):
        print(f"❌ Ошибка: Файл базы данных по пути '{DB_FILE_PATH}' не найден.")
        return

    if not CHIEF_COACH_ID:
        print("❌ Ошибка: В .env не задан CHIEF_COACH_ID. Заполнение связей невозможно.")
        return

    print(f"Запуск обновления структуры и данных БД для окружения: [{ENV.upper()}]")
    conn = sqlite3.connect(DB_FILE_PATH)
    cursor = conn.cursor()

    # 1. Добавляем колонку custom_status в таблицу users
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN custom_status TEXT;")
        print("✅ Колонка 'custom_status' успешно добавлена в таблицу 'users'.")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            print("ℹ️ Колонка 'custom_status' уже существует.")
        else:
            print(f"❌ Ошибка при изменении таблицы 'users': {e}")

    # 2. Создаем промежуточную таблицу coach_student для мульти-тренерства
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS coach_student (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        coach_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        student_id INTEGER REFERENCES users(id) ON DELETE CASCADE
    );
    """)
    print("✅ Таблица 'coach_student' проверена/создана.")

    # 3. МИГРАЦИЯ ДАННЫХ: Связываем текущих учеников с Главным Тренером
    try:
        # Находим внутренний id тренера по его telegram_id (CHIEF_COACH_ID)
        cursor.execute("SELECT id FROM users WHERE telegram_id = ?", (CHIEF_COACH_ID,))
        coach_row = cursor.fetchone()
        
        if not coach_row:
            print(f"⚠️ Предупреждение: Главный тренер с telegram_id={CHIEF_COACH_ID} не найден в БД.")
            print("Сначала зайдите в бот под аккаунтом тренера, чтобы создался профиль, затем запустите скрипт снова.")
        else:
            coach_internal_id = coach_row[0]
            
            # Находим всех подтвержденных учеников
            cursor.execute("SELECT id, full_name FROM users WHERE role = 'student' AND is_approved = 1")
            students = cursor.fetchall()
            
            migrated_count = 0
            for student_id, student_name in students:
                # Проверяем, нет ли уже такой связи (чтобы не дублировать при повторном запуске)
                cursor.execute(
                    "SELECT id FROM coach_student WHERE coach_id = ? AND student_id = ?", 
                    (coach_internal_id, student_id)
                )
                if not cursor.fetchone():
                    cursor.execute(
                        "INSERT INTO coach_student (coach_id, student_id) VALUES (?, ?)", 
                        (coach_internal_id, student_id)
                    )
                    migrated_count += 1
            
            print(f"🎉 Миграция данных успешна: {migrated_count} действующих учеников привязаны к тренеру №1.")

    except sqlite3.Error as e:
        print(f"❌ Ошибка при переносе данных связей: {e}")

    # Сохраняем изменения и закрываем соединение
    conn.commit()
    conn.close()
    print("🎉 Обновление базы данных успешно завершено!")

if __name__ == "__main__":
    upgrade()