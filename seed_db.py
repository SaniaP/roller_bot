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
                    min_cats = 2
                elif 10 <= lvl < 20:
                    max_logs = 10
                    min_cats = 4
                else:
                    max_logs = 15
                    min_cats = 5
                # тут мы заполняем таблицу 
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
                    name="Кораблик",
                    category="others",
                    trick_tier=10,
                    base_tech_points=15.0,
                    base_xp=150,
                    measurement_type="cones",
                    base_quantity=4,
                    coeff_50=1.3,
                    coeff_80=1.0,
                    coeff_120=0.8
                ),
                Trick(
                    name="Вайпер",
                    category="jumping",
                    trick_tier=17,
                    base_tech_points=22.0,
                    base_xp=220,
                    measurement_type="cones",
                    base_quantity=4,
                    coeff_50=1.2,
                    coeff_80=1.0,
                    coeff_120=0.8
                ),
                Trick(
                    name="Лимончики",
                    category="basic",  # Скрытая от учеников группа
                    trick_tier=1,
                    base_tech_points=1.0,
                    base_xp=10,
                    measurement_type="reps",
                    base_quantity=5,
                    coeff_50=1.0,
                    coeff_80=1.0,
                    coeff_120=1.0
                ),
                Trick(
                    name="шарики ХТ",
                    category="spinning", 
                    trick_tier=7,
                    base_tech_points=12.0,
                    base_xp=120,
                    measurement_type="cones",
                    base_quantity=3,
                    coeff_50=1.1,
                    coeff_80=1.0,
                    coeff_120=1.1
                )
            ]
            session.add_all(test_tricks)

    print("✅ Все справочники успешно заполнены! База полностью готова к работе.")

if __name__ == "__main__":
    asyncio.run(seed_database())