"""Хендлеры тренера: пакет-агрегатор.

Модули:
- menu.py        — главный экран, отмена, добавление ученика, статистика
- workout.py     — старт/продолжение тренировки, спортсмены на занятии, финализация
- handbook.py    — справочник трюков (просмотр)
- trick_input.py — внесение трюков тренером (FSM)
- review.py      — проверка самостоятельных трюков учеников
- helpers.py     — общие функции (меню, запросы, парсинг ввода)
"""

import logging

from aiogram import Router

from handlers.coach import menu, handbook, workout, trick_input, review, stats

logger = logging.getLogger(__name__)

router = Router()
router.include_router(menu.router)
router.include_router(handbook.router)
router.include_router(workout.router)
router.include_router(trick_input.router)
router.include_router(review.router)
router.include_router(stats.router)
