from typing import Optional
from datetime import datetime, timezone, date
from sqlalchemy import (
    BigInteger,
    String,
    Integer,
    Float,
    Boolean,
    ForeignKey,
    DateTime,
    Date,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base

# Статусы тренировок — единые константы вместо строковых литералов по всему коду
STATUS_ACTIVE = "active"
STATUS_CLOSED = "closed"


# заполнена частично, по стостоянию на 12.11.26, заполнится при дабовлении нового тренера
class CoachStudent(Base):
    """Таблица связи между Тренерами и Учениками (Многие-ко-многим)"""

    __tablename__ = "coach_student"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    coach_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    # Обратные связи для SQLAlchemy
    coach: Mapped["User"] = relationship(
        "User", foreign_keys=[coach_id], back_populates="students"
    )
    student: Mapped["User"] = relationship(
        "User", foreign_keys=[student_id], back_populates="coaches"
    )


class User(Base):
    """Таблица пользователей (Тренеры и Спортсмены)"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, unique=True, nullable=True
    )
    role: Mapped[str] = mapped_column(
        String(20), default="student"
    )  # 'coach' или 'student'
    full_name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False)

    # Геймификация
    level: Mapped[int] = mapped_column(Integer, default=1)
    xp: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(50), default="Новичок")
    photo_file_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    birth_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    custom_status: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )  # Пойдет в личный кабинет пользователя при достижении 10 уровня, юзер сам его установит или тренер

    # Связи для мульти-тренерства, заложена архитектура на будующее
    coaches: Mapped[list["CoachStudent"]] = relationship(
        "CoachStudent", foreign_keys="CoachStudent.student_id", back_populates="student"
    )
    students: Mapped[list["CoachStudent"]] = relationship(
        "CoachStudent", foreign_keys="CoachStudent.coach_id", back_populates="coach"
    )

    @property  # встроенный дек-р, позволяющий пользоваться .is_coach, не держа в БД роль пользователя
    def is_coach(self) -> bool:
        """Удобная проверка роли без хранения дублирующего флага в базе"""
        return self.role == "coach"


class LevelExperience(Base):
    """Справочник уровней (от 1 до 80)"""

    __tablename__ = "level_experience"

    level: Mapped[int] = mapped_column(Integer, primary_key=True)
    required_xp: Mapped[int] = mapped_column(Integer)
    max_workout_logs: Mapped[int] = mapped_column(Integer, default=10)
    min_trick_categories: Mapped[int] = mapped_column(Integer, default=3)


class XpPenalty(Base):
    """Справочник штрафов на игровой опыт за простые трюки"""

    __tablename__ = "xp_penalties"

    level_difference: Mapped[int] = mapped_column(Integer, primary_key=True)
    xp_multiplier: Mapped[float] = mapped_column(Float, default=1.0)


class Trick(Base):
    """Справочник трюков"""

    __tablename__ = "tricks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    category: Mapped[str] = mapped_column(String(20), default="others")
    trick_tier: Mapped[int] = mapped_column(Integer, default=1)
    base_tech_points: Mapped[float] = mapped_column(Float, default=6.0)
    base_xp: Mapped[int] = mapped_column(Integer, default=10)
    measurement_type: Mapped[str] = mapped_column(String(20), default="cones")
    base_quantity: Mapped[int] = mapped_column(Integer, default=4)
    extra_bonus_pct: Mapped[float] = mapped_column(Float, default=0.05)

    # Коэффициенты дорожек
    coeff_50: Mapped[float] = mapped_column(Float, default=1.0)
    coeff_80: Mapped[float] = mapped_column(Float, default=1.0)
    coeff_120: Mapped[float] = mapped_column(Float, default=1.0)


class Workout(Base):
    """Сессии тренировок"""

    __tablename__ = "workouts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    date: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    # Наш PIN, для самостоятельной работы
    pin_code: Mapped[Optional[str]] = mapped_column(
        String(4), unique=True, nullable=True, default=None
    )
    status: Mapped[str] = mapped_column(
        String(20), default=STATUS_ACTIVE
    )  # STATUS_ACTIVE, STATUS_CLOSED


class WorkoutRegistration(Base):
    """Запись учеников на тренировку"""

    __tablename__ = "workout_registrations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    workout_id: Mapped[int] = mapped_column(
        ForeignKey("workouts.id", ondelete="CASCADE")
    )
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    training_points: Mapped[float] = mapped_column(Float, default=0.0)
    training_xp: Mapped[int] = mapped_column(Integer, default=0)


class TrickLog(Base):
    """Детализированный лог подходов и связок"""

    __tablename__ = "trick_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    workout_id: Mapped[int] = mapped_column(
        ForeignKey("workouts.id", ondelete="CASCADE")
    )
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    trick_id: Mapped[int] = mapped_column(ForeignKey("tricks.id", ondelete="CASCADE"))

    combo_id: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, default=None
    )
    combo_step: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, default=None
    )
    # Бонус связки: разница между итоговым Result шага и чистым баллом трюка
    combo_bonus: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, default=None
    )

    cone_line: Mapped[Optional[str]] = mapped_column(
        String(10), nullable=True, default="80"
    )
    quantity: Mapped[int] = mapped_column(Integer)
    execution_multiplier: Mapped[float] = mapped_column(Float, default=1.0)
    calculated_points: Mapped[float] = mapped_column(Float)
    calculated_xp: Mapped[int] = mapped_column(Integer)
    # Флаг: трюк отправлен учеником самостоятельно с устройства и ждет подтверждения тренера
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
