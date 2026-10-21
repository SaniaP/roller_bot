# Архитектура БД: Роллер-Школа (aiogram 3 + SQLAlchemy 2.0)

## 1. Справочники (Глобальные правила игры)

### Таблица: `level_experience` (Класс: `LevelExperience`)
* `level` (Int, PK) — Номер уровня (1-80).
* `required_xp` (Int) — Сколько ВСЕГО опыта нужно набрать для этого уровня.
* `max_workout_logs` (Int) — Лимит зачисляемых трюков/связок за одно занятие.
* `min_trick_categories` (Int) — Сколько РАЗНЫХ категорий нужно показать, чтобы не получить штраф -5 баллов.

### Таблица: `xp_penalties` (Класс: `XpPenalty`)
* `level_difference` (Int, PK) — На сколько уровней ученик выше, чем `trick_tier` трюка.
* `xp_multiplier` (Float) — Множитель опыта (от 1.0 до 0.0 при разнице >= 10).

### Таблица: `tricks` (Класс: `Trick`)
* `id` (Int, PK, AutoIncrement) — Уникальный ID трюка.
* `name` (String, Unique) — Название (например, "Вайпер", "Кораблик (метры)").
* `category` (String) — Группы (`basic` — скрыта от детей, `spinning`, `sitting`, `jumping`, `wheelie`, `others`).
* `trick_tier` (Int) — Геймерский ранг сложности трюка (1–10) для расчета штрафов на опыт.
* `base_tech_points` (Float) — Базовые технические очки.
* `base_xp` (Int) — Базовый опыт персонажа.
* `measurement_type` (String) — В чём мерить (`cones`, `meters`, `reps`).
* `base_quantity` (Int) — Норматив (например, 4 конуса).
* `extra_bonus_pct` (Float) — Бонус за перевыполнение норматива (по дефолту +5% или 0.05).
* `coeff_50` / `coeff_80` / `coeff_120` (Float) — Множители сложности за ширину дорожки.

---

## 2. Пользователи и Сессии

### Таблица: `users` (Класс: `User`)
* `id` (Int, PK) — Внутренний системный ID.
* `telegram_id` (BigInteger, Unique, Nullable) — Пусто для учеников, введенных тренером вручную.
* `role` (String) — `coach` или `student`.
* `full_name` (String) — ФИО.
* `phone` (String, Nullable) — Телефон.
* `is_approved` (Boolean) — Одобрен ли аккаунт тренером.
* `level` (Int) — Текущий уровень героя (1-80).
* `xp` (Int) — Суммарный накопленный опыт за всё время.
* `title` (String) — Текстовый ранг (Новичок, Мастер Конусов).

### Таблица: `workouts` (Класс: `Workout`)
* `id` (Int, PK) — Уникальный ID тренировки.
* `date` (DateTime) — Дата занятия.
* `status` (String) — Состояние занятия (`planned` — запись открыта, `active` — идёт тренировка, результаты собираются, `finished` — закрыто, начислен опыт).

---

## 3. Логи и Связи (Многие-ко-Многим)

### Таблица: `workout_registrations` (Класс: `WorkoutRegistration`)
* `id` (Int, PK) — ID записи.
* `workout_id` (FK ──► `workouts.id`, Cascade) — Какая тренировка.
* `student_id` (FK ──► `users.id`, Cascade) — Какой ученик.
* `training_points` (Float) — Финальный технический балл за конкретный день.
* `training_xp` (Int) — Финальный полученный опыт за конкретный день.

### Таблица: `trick_logs` (Класс: `TrickLog`)
* `id` (Int, PK) — ID подхода.
* `workout_id` / `student_id` / `trick_id` (FK) — Кто, где и какой трюк делал.
* `combo_id` (String, Nullable) — Одинаковый маркер для трюков, объединенных в связку.
* `combo_step` (Int, Nullable) — Очередность трюка в связке (1, 2, 3...).
* `cone_line` (String, Nullable) — Дорожка конусов ("50", "80", "120" или None).
* `quantity` (Int) — Фактический результат (число конусов/метров/повторов).
* `execution_multiplier` (Float) — Штраф от тренера за качество техники (1.0 — норма, 0.7 — грязно).
* `calculated_points` (Float) — Вычисленный Питоном итог по баллам.
* `calculated_xp` (Int) — Вычисленный Питоном итог по игровому опыту.


* версия 17.10 23:28



Краткая суть проекта

Мы пишем Телеграм-бот на aiogram 3 для роллер-школы. Есть 2 роли — Тренер и Спортсмен. Реализована мощная система геймификации в стиле RPG (уровни 1–80, накопление опыта XP) [2.2]. Учтён ручной ввод учеников без телефонов тренером (telegram_id IS NULL), штрафы за отсутствие разнообразия категорий трюков на тренировке, лимиты подходов и судейские связки любой длины (combo_id, combo_step) [2.2].

📦 Что уже сделано в Главе 2:

1. config.py — Содержит токен бота и строку DATABASE_URL = "sqlite+aiosqlite:///./roller_school.db" [2.3].
2. database.py — Настроен асинхронный движок SQLAlchemy 2.0 + aiosqlite и генератор сессий get_db() [2.3].
3. models.py — Декларативно описана вся структура таблиц (User, LevelExperience, XpPenalty, Trick, Workout, WorkoutRegistration, TrickLog) [2.2].
4. init_db.py — Одноразовый скрипт инициализации. Успешно запущен пользователем. Файл базы данных roller_school.db физически создался на диске, таблицы сгенерированы!

📅 ЗАДАЧА НА ЗАВТРА: Шаг 2.5 — Наполнение справочников

Файлы базы данных созданы, но таблицы пока пусты. Пользователь отложил запуск наполнения справочников на завтра.

🛠️ Что нужно сделать ИИ-преемнику в первую очередь:

1. Напомнить пользователю открыть файл seed_db.py в VS Code.
2. Убедиться, что в него вставлен следующий код (он сгенерирует 80 уровней, штрафы за разницу уровней и добавит тестовые трюки):
python
import asyncio
from database import async_session
from models import LevelExperience, XpPenalty, Trick

async def seed_database():
    print("⏳ Начало заполнения справочников базы данных...")
    
    async with async_session() as session:
        async with session.begin():
            
            # 1. ГЕНЕРАЦИЯ 80 УРОВНЕЙ ОПЫТА, ЛИМИТОВ И РАЗНООБРАЗИЯ
            print("→ Генерируем 80 уровней опыта, лимиты подходов и правила разнообразия...")
            total_required_xp = 0
            
            for lvl in range(1, 81):
                if lvl > 1:
                    total_required_xp += (lvl - 1) * 200
                
                # Правила лимитов и разнообразия трюков по уровням
                if lvl < 10:
                    max_logs = 5
                    min_cats = 3
                elif 10 <= lvl < 20:
                    max_logs = 10
                    min_cats = 4
                else:
                    max_logs = 15
                    min_cats = 5
                
                level_entry = LevelExperience(
                    level=lvl,
                    required_xp=total_required_xp,
                    max_workout_logs=max_logs,
                    min_trick_categories=min_cats
                )
                session.add(level_entry)

            # 2. ЗАПОЛНЕНИЕ ШТРАФОВ ЗА РАЗНИЦУ УРОВНЕЙ (Lineage 2 стиль)
            print("→ Заполняем матрицу штрафов на опыт...")
            penalties = [
                XpPenalty(level_difference=0, xp_multiplier=1.0),
                XpPenalty(level_difference=1, xp_multiplier=1.0),
                XpPenalty(level_difference=2, xp_multiplier=1.0),
                XpPenalty(level_difference=3, xp_multiplier=1.0),
                XpPenalty(level_difference=4, xp_multiplier=1.0),
                XpPenalty(level_difference=5, xp_multiplier=1.0),
                XpPenalty(level_difference=6, xp_multiplier=0.9),  # -10% опыта
                XpPenalty(level_difference=7, xp_multiplier=0.8),  # -20% опыта
                XpPenalty(level_difference=8, xp_multiplier=0.6),  # -40% опыта
                XpPenalty(level_difference=9, xp_multiplier=0.3),  # -70% опыта
                XpPenalty(level_difference=10, xp_multiplier=0.0), # -100% опыта (кап)
            ]
            session.add_all(penalties)

            # 3. ДОБАВЛЕНИЕ СТАРТОВЫХ ТРЮКОВ ДЛЯ ТЕСТОВ
            print("→ Добавляем тестовые трюки с коэффициентами дорожек...")
            test_tricks = [
                Trick(
                    name="Кораблик (конусы)",
                    category="spinning",
                    trick_tier=2,
                    base_tech_points=6.0,
                    base_xp=10,
                    measurement_type="cones",
                    base_quantity=4,
                    coeff_50=0.8, coeff_80=1.0, coeff_120=1.3
                ),
                Trick(
                    name="Вайпер (Wiper)",
                    category="spinning",
                    trick_tier=4,
                    base_tech_points=8.0,
                    base_xp=15,
                    measurement_type="cones",
                    base_quantity=4,
                    coeff_50=1.5, coeff_80=1.0, coeff_120=0.7
                ),
                Trick(
                    name="Лимончики",
                    category="basic",  # Скрытая от учеников группа
                    trick_tier=1,
                    base_tech_points=1.0,
                    base_xp=2,
                    measurement_type="reps",
                    base_quantity=5,
                    coeff_50=1.0, coeff_80=1.0, coeff_120=1.0
                )
            ]
            session.add_all(test_tricks)

    print("✅ Все справочники успешно заполнены! База полностью готова к работе.")

if __name__ == "__main__":
    asyncio.run(seed_database())
Используйте код с осторожностью.
3. Сказать пользователю запустить команду в терминале: python seed_db.py.
4. После успешного заполнения справочников официально закрыть Главу 2 и переходить к Главе 3: Разворачивание структуры бота на aiogram 3 (создание папок handlers, keyboards, middlewares, states и файла main.py).
Всё сохранено в памяти системы. Завтра просто скопируй этот блок и отправь мне, чтобы мы мгновенно включились в работу с нужной секунды. Отличного вечера и до завтра! 🚀
Карта проекта:
roller_school_bot/ (корень проекта)
├── config.py
├── database.py
├── models.py
├── seed_db.py
├── calculator.py
├── roller_school.db
│
├── main.py                 # Главная точка входа для запуска бота
│
├── handlers/               # Обработчики команд и сообщений (контроллеры)
│   ├── __init__.py
│   ├── common.py           # Общие команды (/start, /help)
│   ├── coach.py            # Логика тренера (ввод учеников, тренировки)
│   └── skater.py           # Логика спортсмена (профиль, логи трюков)
│
├── keyboards/              # Клавиатуры (Reply, Inline и Builder-фабрики)
│   ├── __init__.py
│   ├── coach_kb.py         # Кнопки для роли Тренера
│   └── skater_kb.py        # Кнопки для роли Спортсмена
│
├── middlewares/            # Прослойки (проверка ролей, сессии БД)
│   ├── __init__.py
│   └── db_middleware.py    # Проброс сессии get_db в каждый хэндлер
│
└── states/                 # Состояния FSM (Finite State Machine)
    ├── __init__.py
    ├── coach_states.py     # Шаги создания тренировок/учеников
    └── skater_states.py    # Шаги отправки логов трюков
