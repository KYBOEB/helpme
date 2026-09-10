"""
Клиент LLM. ВЛАДЕЛЕЦ:

Сейчас — заглушка. B заменит её на реальную реализацию через OpenAI-совместимый эндпоинт
(ProxyAPI). Сигнатуру менять нельзя.
"""
from __future__ import annotations

import os


def chat(messages: list[dict], model: str | None = None,
         temperature: float = 0.2, max_tokens: int = 900) -> str:
    """Отправить сообщения модели и получить текст ответа.

    ПРАВИЛО: функция никогда не бросает исключение. При любой аварии возвращает "".
    """
    last = messages[-1]["content"] if messages else ""
    return f"[заглушка LLM] {last[:80]}"
