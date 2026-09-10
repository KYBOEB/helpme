"""
Генерация ответа пользователю.

Сейчас — заглушка: отдаёт шаги статьи как есть. Это же является фолбэком
в реальной реализации, так что поведение не изменится при отказе модели.
"""
from __future__ import annotations

from common.models import AnswerResult, Article, Slot, TicketCard


def make_answer(article: Article, slots: dict[str, str], user_text: str) -> AnswerResult:
    """Переписать шаги статьи под ситуацию пользователя. Новых шагов не добавлять."""
    return AnswerResult(text="", steps=list(article.steps))


def make_clarifying_question(slot: Slot, problem_summary: str) -> str:
    """Один короткий уточняющий вопрос."""
    return slot.question


def make_summary(card: TicketCard) -> str:
    """Итог обращения: что за проблема, решена ли, что дальше, нужен ли специалист."""
    status = "решена без специалиста" if card.resolved_by_bot else "передана специалисту"
    return f"Обращение по категории «{card.category}»: {card.problem_summary}. Проблема {status}."
