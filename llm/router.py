"""
Классификация обращения.

Сейчас — заглушка: берёт первую статью-кандидата. Этого достаточно, чтобы тимлид
собрал сквозной сценарий, не дожидаясь реальной реализации.
"""
from __future__ import annotations

from common.models import Article, RouteResult


def route(user_text: str,
          history: list[dict],
          candidates: list[Article],
          known_slots: dict[str, str]) -> RouteResult:
    """Определить категорию, статью, уверенность и недостающие слоты."""
    # TODO(B): реальный вызов модели + валидация + постобработка
    if not candidates:
        return RouteResult(category="не определено", confidence=0.0)

    art = candidates[0]
    missing = [
        s.key for s in art.required_slots
        if not s.optional and s.key not in known_slots
    ]
    return RouteResult(
        category=art.category,
        article_id=art.id,
        confidence=0.9,
        problem_summary=user_text[:120],
        filled_slots=dict(known_slots),
        missing_slots=missing,
        is_out_of_scope=False,
    )
