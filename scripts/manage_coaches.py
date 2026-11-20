"""CLI-скрипт управления тренерами и комьюнити.

Команды:
  python scripts/manage_coaches.py add-coach 712345678 [--name "Имя"]
      — переводит пользователя в роль тренера (или создает нового),
        ставит is_approved=True, is_solo=False и создает личное комьюнити.

  python scripts/manage_coaches.py add-to-community <community_id> <coach_id>
      — добавляет тренера в комьюнити (общий ТОП).

  python scripts/manage_coaches.py link-student <coach_id> <student_id>
      — ручная привязка ученика к тренеру.

  python scripts/manage_coaches.py list-communities
      — список комьюнити с тренерами.

  python scripts/manage_coaches.py make-solo <user_id>
      — помечает пользователя как автономного (соло) спортсмена.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from database import async_session
import models  # noqa: F401
from models import Community, CommunityCoach, CoachStudent, User


async def add_coach(telegram_id: int, name: str | None) -> None:
    async with async_session() as session:
        user = (
            await session.execute(select(User).where(User.telegram_id == telegram_id))
        ).scalar_one_or_none()

        if user is None:
            user = User(
                telegram_id=telegram_id,
                full_name=name or f"Тренер {telegram_id}",
                role="coach",
                is_approved=True,
                is_solo=False,
            )
            session.add(user)
            await session.flush()
            print(f"✅ Создан новый тренер #{user.id} '{user.full_name}'")
        elif user.role == "coach":
            print(f"⏭️  Пользователь #{user.id} '{user.full_name}' уже тренер.")
        else:
            user.role = "coach"
            user.is_approved = True
            user.is_solo = False
            if name:
                user.full_name = name
            print(f"✅ Пользователь #{user.id} '{user.full_name}' переведен в тренеры")

        # Личное комьюнити тренера
        link = (
            await session.execute(
                select(CommunityCoach).where(CommunityCoach.coach_id == user.id)
            )
        ).scalar_one_or_none()
        if link:
            print(f"⏭️  У тренера уже есть комьюнити #{link.community_id}")
        else:
            community = Community(
                name=f"Группа тренера {user.full_name}",
                created_at=datetime.now(timezone.utc),
            )
            session.add(community)
            await session.flush()
            session.add(CommunityCoach(community_id=community.id, coach_id=user.id))
            print(f"✅ Создано комьюнити #{community.id} '{community.name}'")

        await session.commit()


async def add_to_community(community_id: int, coach_id: int) -> None:
    async with async_session() as session:
        community = (
            await session.execute(select(Community).where(Community.id == community_id))
        ).scalar_one_or_none()
        coach = (
            await session.execute(select(User).where(User.id == coach_id))
        ).scalar_one_or_none()

        if not community:
            print(f"❌ Комьюнити #{community_id} не найдено.")
            return
        if not coach or coach.role != "coach":
            print(f"❌ Тренер #{coach_id} не найден (или не тренер).")
            return

        exists = (
            await session.execute(
                select(CommunityCoach).where(
                    CommunityCoach.community_id == community_id,
                    CommunityCoach.coach_id == coach_id,
                )
            )
        ).scalar_one_or_none()
        if exists:
            print(f"⏭️  Тренер уже в комьюнити '{community.name}'")
        else:
            session.add(CommunityCoach(community_id=community_id, coach_id=coach_id))
            await session.commit()
            print(f"✅ Тренер '{coach.full_name}' добавлен в '{community.name}'")


async def link_student(coach_id: int, student_id: int) -> None:
    async with async_session() as session:
        coach = (
            await session.execute(select(User).where(User.id == coach_id))
        ).scalar_one_or_none()
        student = (
            await session.execute(select(User).where(User.id == student_id))
        ).scalar_one_or_none()

        if not coach or coach.role != "coach":
            print(f"❌ Тренер #{coach_id} не найден (или не тренер).")
            return
        if not student:
            print(f"❌ Ученик #{student_id} не найден.")
            return

        exists = (
            await session.execute(
                select(CoachStudent).where(
                    CoachStudent.coach_id == coach_id,
                    CoachStudent.student_id == student_id,
                )
            )
        ).scalar_one_or_none()
        if exists:
            print("⏭️  Связь уже существует.")
        else:
            session.add(CoachStudent(coach_id=coach_id, student_id=student_id))
            await session.commit()
            print(
                f"✅ Ученик '{student.full_name}' привязан к тренеру '{coach.full_name}'"
            )


async def list_communities() -> None:
    async with async_session() as session:
        communities = (
            (await session.execute(select(Community).order_by(Community.id)))
            .scalars()
            .all()
        )
        if not communities:
            print("Комьюнити пока нет.")
            return
        for c in communities:
            coaches = (
                (
                    await session.execute(
                        select(User)
                        .join(CommunityCoach, CommunityCoach.coach_id == User.id)
                        .where(CommunityCoach.community_id == c.id)
                    )
                )
                .scalars()
                .all()
            )
            names = ", ".join(f"#{u.id} {u.full_name}" for u in coaches)
            print(f"#{c.id} '{c.name}': {names}")


async def make_solo(user_id: int) -> None:
    async with async_session() as session:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if not user:
            print(f"❌ Пользователь #{user_id} не найден.")
            return
        user.is_solo = True
        await session.commit()
        print(f"✅ Пользователь '{user.full_name}' помечен как соло-спортсмен.")


def main():
    parser = argparse.ArgumentParser(description="Управление тренерами и комьюнити")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add-coach", help="Добавить/назначить тренера")
    p_add.add_argument("telegram_id", type=int)
    p_add.add_argument("--name", type=str, default=None)

    p_com = sub.add_parser("add-to-community", help="Добавить тренера в комьюнити")
    p_com.add_argument("community_id", type=int)
    p_com.add_argument("coach_id", type=int)

    p_link = sub.add_parser("link-student", help="Привязать ученика к тренеру")
    p_link.add_argument("coach_id", type=int)
    p_link.add_argument("student_id", type=int)

    sub.add_parser("list-communities", help="Список комьюнити")

    p_solo = sub.add_parser("make-solo", help="Пометить пользователя как соло")
    p_solo.add_argument("user_id", type=int)

    args = parser.parse_args()

    if args.command == "add-coach":
        asyncio.run(add_coach(args.telegram_id, args.name))
    elif args.command == "add-to-community":
        asyncio.run(add_to_community(args.community_id, args.coach_id))
    elif args.command == "link-student":
        asyncio.run(link_student(args.coach_id, args.student_id))
    elif args.command == "list-communities":
        asyncio.run(list_communities())
    elif args.command == "make-solo":
        asyncio.run(make_solo(args.user_id))


if __name__ == "__main__":
    main()
