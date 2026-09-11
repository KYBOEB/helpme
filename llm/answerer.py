"""
Генерация ответа пользователю: переписываем шаги статьи под ситуацию,
короткие уточняющие вопросы, итог обращения.
"""
from __future__ import annotations

import json
import logging

from common.models import AnswerResult, Article, Slot
from llm import client, prompts

log = logging.getLogger(__name__)


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


def make_answer(article: Article, slots: dict[str, str], user_text: str) -> AnswerResult:
    """Переписать шаги статьи под ситуацию пользователя. Новых шагов не добавлять."""
    fallback = AnswerResult(text="", steps=list(article.steps))

    user_message = (
        f"Проблема пользователя: {user_text}\n"
        f"Известные данные: {slots or 'нет'}\n\n"
        f"Оригинальные шаги решения:\n"
        + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(article.steps))
    )

    raw = client.chat(
        messages=[
            {"role": "system", "content": prompts.ANSWER_SYSTEM},
            {"role": "user", "content": user_message},
        ],
        model=client.MODEL_SMART,
        temperature=0.2,
    )
    if not raw:
        return fallback

    try:
        data = json.loads(_strip_json_fence(raw))
        result = AnswerResult(**data)
    except Exception as exc:
        log.warning("make_answer: не смог разобрать ответ модели: %s", exc)
        return fallback

    if not result.steps or len(result.steps) > len(article.steps):
        result.steps = list(article.steps)

    return result


def make_clarifying_question(slot: Slot, problem_summary: str) -> str:
    """Один короткий уточняющий вопрос."""
    if slot.options:
        return slot.question

    raw = client.chat(
        messages=[
            {"role": "system", "content": prompts.CLARIFY_SYSTEM},
            {"role": "user", "content": f"Проблема: {problem_summary}\nНужно уточнить: {slot.question}"},
        ],
        model=client.MODEL_FAST,
        temperature=0.2,
        max_tokens=100,
    )
    return raw.strip() if raw else slot.question

