"""
Клиент LLM. OpenAI-совместимый эндпоинт (ProxyAPI), провайдер меняется одной переменной.
"""
from __future__ import annotations

import logging
import os
import time

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

log = logging.getLogger(__name__)

_client = OpenAI(
    base_url=os.getenv("LLM_BASE_URL", "https://api.proxyapi.ru/v1"),
    api_key=os.getenv("LLM_API_KEY", ""),
    timeout=20.0,
    max_retries=0,
)

MODEL_FAST = os.getenv("LLM_MODEL_FAST")
MODEL_SMART = os.getenv("LLM_MODEL_SMART")


def chat(messages: list[dict], model: str | None = None,
         temperature: float = 0.2, max_tokens: int = 900) -> str:
    """Отправить сообщения модели и получить текст ответа.

    ПРАВИЛО: функция никогда не бросает исключение. При любой аварии возвращает "".
    """
    model = model or MODEL_FAST
    use_completion_tokens_param = False
    skip_temperature = False

    for attempt, pause in enumerate((0, 1, 3)):
        if pause:
            time.sleep(pause)
        try:
            t0 = time.monotonic()
            kwargs = dict(model=model, messages=messages)

            if use_completion_tokens_param:
                kwargs["max_completion_tokens"] = max_tokens
            else:
                kwargs["max_tokens"] = max_tokens

            if not skip_temperature:
                kwargs["temperature"] = temperature

            resp = _client.chat.completions.create(**kwargs)
            log.info("llm ok model=%s attempt=%s %.2fs", model, attempt, time.monotonic() - t0)
            return (resp.choices[0].message.content or "").strip()
        except Exception as exc:
            log.warning("llm fail model=%s attempt=%s: %s", model, attempt, exc)
            msg = str(exc)
            if "max_completion_tokens" in msg and not use_completion_tokens_param:
                use_completion_tokens_param = True
            if "temperature" in msg and not skip_temperature:
                skip_temperature = True
    return ""