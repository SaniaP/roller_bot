import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# запускать с корня: python -m scripts.add_coach
"""Скрипт добавляет пользователей в статус coach (тренер) и прикрепляет их к комьюнити.

Если комьюнити с указанным именем не существует — оно создаётся.
Если пользователь уже в этом комьюнити — повторная связь не добавляется.
Если пользователя нет в базе — при CREATE_IF_MISSING=True он создаётся с данными
из NEW_USERS_DATA (telegram_id, full_name, phone).

Запуск:
  python -m scripts.add_coach
"""

import asyncio

from sqlalchemy import select

from database import async_session
from models import Community, CommunityCoach, User

# ============================================
# КОНФИГУРАЦИЯ
# ============================================
USER_IDS_TO_ADD = []  # id пользователей из таблицы users или пусто если юзер новый
COMMUNITY_NAME = "Банда Anastacia"  # имя комьюнити (создаётся, если его нет)

# Создавать пользователя, если его нет в базе?
CREATE_IF_MISSING = True

# Данные для создания новых пользователей (используются, если CREATE_IF_MISSING=True).
# Каждый пользователь создаётся со статусом coach и сразу добавляется в комьюнити.
NEW_USERS_DATA = [
    {"telegram_id": 506447144, "full_name": "Anastacia", "phone": +375447276669},
    # {"telegram_id": 987654321, "full_name": "Пётр Петров", "phone": "+79001234567"},
]


async def add_coaches_to_community():
    if not USER_IDS_TO_ADD and not (CREATE_IF_MISSING and NEW_USERS_DATA):
        print("Нет ID для добавления и нет данных для создания новых пользователей.")
        return

    async with async_session() as session:
        # 1. Найти или создать комьюнити
        result = await session.execute(
            select(Community).where(Community.name == COMMUNITY_NAME)
        )
        community = result.scalar_one_or_none()
        if community is None:
            community = Community(name=COMMUNITY_NAME)
            session.add(community)
            await session.flush()  # получаем community.id
            print(f"Создано комьюнити: '{COMMUNITY_NAME}' (id={community.id})")
        else:
            print(f"Найдено комьюнити: '{COMMUNITY_NAME}' (id={community.id})")

        # 2. Добавить каждого пользователя как тренера в комьюнити
        for uid in USER_IDS_TO_ADD:
            user = await session.get(User, uid)
            if user is None:
                print(f"Пользователь id={uid} не найден.")
                if CREATE_IF_MISSING:
                    print(
                        "-> Пользователи с ID не создаются, используйте NEW_USERS_DATA."
                    )
                continue

            if user.role != "coach":
                user.role = "coach"
                print(
                    f"Пользователь id={uid} ({user.full_name}) получил статус 'coach'."
                )

            if not user.is_approved:
                user.is_approved = True
                print(f"Пользователь id={uid} подтверждён (is_approved=True).")

            # Проверяем, не состоит ли уже в этом комьюнити
            exists = await session.execute(
                select(CommunityCoach).where(
                    CommunityCoach.community_id == community.id,
                    CommunityCoach.coach_id == uid,
                )
            )
            if exists.scalar_one_or_none() is not None:
                print(f"Пользователь id={uid} уже в комьюнити — пропущен.")
                continue

            session.add(CommunityCoach(community_id=community.id, coach_id=uid))
            print(f"Пользователь id={uid} ({user.full_name}) добавлен в комьюнити.")

        # 3. Создать новых пользователей (если их нет в базе) и добавить в комьюнити
        if CREATE_IF_MISSING:
            for data in NEW_USERS_DATA:
                # Ищем по telegram_id (он уникальный), может быть None
                tid = data.get("telegram_id")
                existing = None
                if tid is not None:
                    res = await session.execute(
                        select(User).where(User.telegram_id == tid)
                    )
                    existing = res.scalar_one_or_none()

                if existing is not None:
                    uid = existing.id
                    print(
                        f"Пользователь с telegram_id={tid} уже существует (id={uid}) — пропущен."
                    )
                else:
                    new_user = User(
                        telegram_id=tid,
                        full_name=data["full_name"],
                        phone=data.get("phone"),
                        role="coach",
                        is_approved=True,
                    )
                    session.add(new_user)
                    await session.flush()  # получаем new_user.id
                    uid = new_user.id
                    print(
                        f"Создан новый пользователь: {new_user.full_name} (id={uid}, role='coach')."
                    )

                # Проверяем, не состоит ли уже в этом комьюнити
                exists = await session.execute(
                    select(CommunityCoach).where(
                        CommunityCoach.community_id == community.id,
                        CommunityCoach.coach_id == uid,
                    )
                )
                if exists.scalar_one_or_none() is not None:
                    print(f"Пользователь id={uid} уже в комьюнити — пропущен.")
                    continue

                session.add(CommunityCoach(community_id=community.id, coach_id=uid))
                print(f"Пользователь id={uid} добавлен в комьюнити.")

        await session.commit()
        print("Готово! Тренеры добавлены в комьюнити.")


if __name__ == "__main__":
    asyncio.run(add_coaches_to_community())
