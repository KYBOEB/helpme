"""
Кэш ответов модели и демонстрационный режим. ВЛАДЕЛЕЦ: A.

Зачем: на защите может не быть интернета, а внешний провайдер может ответить
медленно или отказать. Кэш решает обе задачи сразу.

Два режима, переключаются переменной DEMO_MODE:

  DEMO_MODE=0 (рабочий)  — обычный вызов модели, результат складывается в кэш.
                           Так кэш «прогревается» во время подготовки к демо.
  DEMO_MODE=1 (демо)     — сеть не трогаем вообще: берём из кэша, а при промахе
                           отдаём безопасное значение по умолчанию.

Побочная польза: во время разработки повторные прогоны тест-набора не тратят
деньги и идут мгновенно.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading

from common.models import Article, AnswerResult, RouteResult
from llm import answerer, router

log = logging.getLogger(__name__)

CACHE_PATH = os.getenv("DEMO_CACHE_PATH", "data/demo_cache.json")
_lock = threading.Lock()


def demo_mode() -> bool:
    return os.getenv("DEMO_MODE", "0") == "1"


def _load() -> dict:
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


_cache: dict = _load()


def _save() -> None:
    try:
        os.makedirs(os.path.dirname(CACHE_PATH) or ".", exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(_cache, f, ensure_ascii=False, indent=1)
    except OSError as exc:
        log.warning("не удалось сохранить кэш: %s", exc)


def _key(kind: str, *parts: str) -> str:
    raw = kind + "|" + "|".join(p.strip().lower() for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def stats() -> dict:
    return {"entries": len(_cache), "demo_mode": demo_mode(), "path": CACHE_PATH}


# ---------------------------------------------------------------- обёртки

def cached_route(user_text: str, history: list[dict],
                 candidates: list[Article], known_slots: dict[str, str]) -> RouteResult:
    key = _key("route", user_text, ",".join(a.id for a in candidates),
               json.dumps(known_slots, sort_keys=True, ensure_ascii=False))

    hit = _cache.get(key)
    if hit is not None:
        return RouteResult(**hit)

    if demo_mode():
        # В демо-режиме сеть не трогаем: отдаём лучший результат поиска.
        log.info("демо-режим: промах кэша на классификации, работаем по поиску")
        if not candidates:
            return RouteResult(category="не определено", confidence=0.0)
        art = candidates[0]
        missing = [s.key for s in art.required_slots
                   if not s.optional and s.key not in known_slots]
        return RouteResult(category=art.category, article_id=art.id, confidence=0.8,
                           problem_summary=user_text[:120],
                           filled_slots=dict(known_slots), missing_slots=missing)

    result = router.route(user_text, history, candidates, known_slots)
    with _lock:
        _cache[key] = result.model_dump()
        _save()
    return result


def cached_answer(article: Article, slots: dict[str, str], user_text: str) -> AnswerResult:
    key = _key("answer", article.id,
               json.dumps(slots, sort_keys=True, ensure_ascii=False), user_text)

    hit = _cache.get(key)
    if hit is not None:
        return AnswerResult(**hit)

    if demo_mode():
        log.info("демо-режим: промах кэша на ответе, отдаём шаги статьи как есть")
        return AnswerResult(text="", steps=list(article.steps))

    result = answerer.make_answer(article, slots, user_text)
    with _lock:
        _cache[key] = result.model_dump()
        _save()
    return result
