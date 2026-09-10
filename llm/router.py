"""
Классификация обращения пользователя: определяем категорию и статью базы знаний.
"""
from __future__ import annotations

import json
import logging

from common.models import CATEGORIES, Article, RouteResult
from llm import client, prompts

log = logging.getLogger(__name__)


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


def _build_candidates_block(candidates: list[Article]) -> str:
    lines = []
    for art in candidates:
        slots = ", ".join(s.key for s in art.required_slots) or "нет"
        symptoms = ", ".join(art.symptoms) or "нет"
        lines.append(
            f"- id={art.id}, категория={art.category}, заголовок={art.title}, "
            f"симптомы=[{symptoms}], нужные_слоты=[{slots}]"
        )
    return "\n".join(lines) if lines else "(кандидатов нет)"


def _build_history_block(history: list[dict]) -> str:
    if not history:
        return "(истории пока нет)"
    lines = [f"{m['role']}: {m['content']}" for m in history[-6:]]
    return "\n".join(lines)


def _call_model(user_text: str, history: list[dict], candidates: list[Article],
                 known_slots: dict[str, str], retry_hint: str = "") -> dict | None:
    system = prompts.ROUTER_SYSTEM.format(categories="\n".join(f"- {c}" for c in CATEGORIES))
    user_message = (
        f"История диалога:\n{_build_history_block(history)}\n\n"
        f"Уже известные слоты: {known_slots or 'нет'}\n\n"
        f"Статьи-кандидаты:\n{_build_candidates_block(candidates)}\n\n"
        f"Сообщение пользователя: {user_text}"
    )
    if retry_hint:
        user_message += (
            f"\n\nТвой предыдущий ответ был некорректным JSON: {retry_hint}\n"
            "Верни только исправленный JSON."
        )

    raw = client.chat(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
        model=client.MODEL_FAST,
        temperature=0.1,
    )
    if not raw:
        return None
    raw = _strip_json_fence(raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("route: битый JSON: %s", exc)
        return {"__error__": str(exc)}


def route(user_text: str, history: list[dict], candidates: list[Article],
          known_slots: dict[str, str]) -> RouteResult:
    try:
        data = _call_model(user_text, history, candidates, known_slots)

        if data is None:
            return RouteResult(category="не определено", confidence=0.0, problem_summary=user_text[:100])

        if "__error__" in data:
            data = _call_model(user_text, history, candidates, known_slots, retry_hint=data["__error__"])
            if data is None or "__error__" in data:
                return RouteResult(category="не определено", confidence=0.0, problem_summary=user_text[:100])

        result = RouteResult(**data)

        if result.category not in CATEGORIES:
            result.category = "не определено"
            result.confidence = 0.0

        candidate_ids = {art.id for art in candidates}
        if result.article_id not in candidate_ids:
            result.article_id = None

        result.confidence = max(0.0, min(1.0, result.confidence))

        selected = next((a for a in candidates if a.id == result.article_id), None)
        if selected:
            required_keys = {s.key for s in selected.required_slots if not s.optional}
            result.missing_slots = [
                k for k in result.missing_slots
                if k in required_keys and k not in known_slots
            ]
        else:
            result.missing_slots = []

        return result

    except Exception as exc:
        log.warning("route: авария: %s", exc)
        return RouteResult(category="не определено", confidence=0.0, problem_summary=user_text[:100])