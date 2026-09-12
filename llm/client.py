"""
Клиент LLM. OpenAI-совместимый эндпоинт (ProxyAPI), провайдер меняется одной переменной.
"""
from __future__ import annotations

import json
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
# Модель векторизации для поиска по смыслу. Провайдер тот же, эндпоинт другой.
MODEL_EMBED = os.getenv("LLM_MODEL_EMBED", "text-embedding-3-small")

# Что мы узнали про каждую модель из её же ошибок: принимает ли `max_tokens`
# или требует `max_completion_tokens`, принимает ли `temperature`.
#
# Лежит на диске, потому что иначе разведка повторяется после КАЖДОГО
# перезапуска процесса: два запроса впустую и четыре секунды сна на первом же
# обращении. На защите первым обращением будет наше демонстрационное —
# ровно то место, где лишние секунды заметит комиссия.
_QUIRKS_PATH = os.getenv("LLM_QUIRKS_PATH", "data/llm_quirks.json")
_MODEL_QUIRKS: dict[str, dict[str, bool]] = {}


def _load_quirks() -> None:
    try:
        with open(_QUIRKS_PATH, encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            _MODEL_QUIRKS.update({k: v for k, v in loaded.items()
                                  if isinstance(v, dict)})
            log.info("особенности моделей подняты с диска: %s", list(_MODEL_QUIRKS))
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001
        log.warning("особенности моделей не читаются: %s", exc)


def _save_quirks() -> None:
    try:
        os.makedirs(os.path.dirname(_QUIRKS_PATH) or ".", exist_ok=True)
        with open(_QUIRKS_PATH, "w", encoding="utf-8") as fh:
            json.dump(_MODEL_QUIRKS, fh, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        log.warning("особенности моделей не сохранились: %s", exc)


_load_quirks()


def embed(texts: list[str], model: str | None = None) -> list[list[float]]:
    """Вектора для списка текстов.

    В отличие от chat(), эта функция НЕ глотает ошибки, а бросает исключение.
    Так задумано: при сборке индекса молчаливый провал дал бы пустой индекс,
    а в бою решение «откатиться на BM25 или упасть» принимает kb/embeddings.py,
    и принять его можно, только увидев ошибку.
    """
    if not texts:
        return []
    model = model or MODEL_EMBED

    # Бюджет времени. Вектор запроса считается ВНУТРИ обработки сообщения
    # пользователя, а браузер ждёт ответа 20 секунд (REQUEST_TIMEOUT_MS).
    # На восьмисекундном таймауте с повтором худший случай был 8 + 1 + 8 = 17
    # секунд ТОЛЬКО на векторизацию, плюс классификация и ответ — отсюда
    # «Помощник не ответил вовремя» в чате.
    #
    # Поэтому одиночный запрос: 3,5 секунды и ОДНА попытка. Не успели — молча
    # откатываемся на BM25. Чуть худший поиск лучше, чем повисший чат.
    # Пакетная сборка индекса — другое дело: там никто не сидит перед экраном.
    batch = len(texts) > 4
    timeout = float(os.getenv("LLM_EMBED_TIMEOUT", "3.5"))
    if batch:
        timeout = max(timeout, 60.0)
    client = _client.with_options(timeout=timeout)

    last: Exception | None = None
    for attempt, pause in enumerate((0, 1, 3) if batch else (0,)):
        if pause:
            time.sleep(pause)
        try:
            t0 = time.monotonic()
            resp = client.embeddings.create(model=model, input=texts)
            log.info("embed ok model=%s n=%s attempt=%s %.2fs",
                     model, len(texts), attempt, time.monotonic() - t0)
            # Порядок ответа провайдер не гарантирует — раскладываем по index.
            ordered = sorted(resp.data, key=lambda d: d.index)
            return [list(d.embedding) for d in ordered]
        except Exception as exc:  # noqa: BLE001
            log.warning("embed fail model=%s attempt=%s: %s", model, attempt, exc)
            last = exc
    raise RuntimeError(f"не удалось получить эмбеддинги моделью {model}: {last}")


def chat(messages: list[dict], model: str | None = None,
         temperature: float = 0.2, max_tokens: int = 900,
         timeout: float | None = None) -> str:
    """Отправить сообщения модели и получить текст ответа.

    ПРАВИЛО: функция никогда не бросает исключение. При любой аварии возвращает "".
    """
    model = model or MODEL_FAST
    # Особенности модели запоминаем МЕЖДУ вызовами.
    #
    # Раньше флаги жили внутри функции, и каждый вызов gpt-5.6-luna начинался
    # заново: попытка 0 падала на `max_tokens`, попытка 1 (через секунду) —
    # на `temperature`, и только попытка 2 (ещё через три секунды) проходила.
    # То есть КАЖДЫЙ ответ умной модели стоил двух лишних запросов и четырёх
    # секунд сна. Это и есть заметная часть «долго работает» из отзыва комиссии,
    # и она же удваивала расход на ProxyAPI.
    quirks = _MODEL_QUIRKS.setdefault(model, {"completion_tokens": False,
                                              "no_temperature": False})
    use_completion_tokens_param = quirks["completion_tokens"]
    skip_temperature = quirks["no_temperature"]

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

            # Свой таймаут нужен пакетным инструментам: генератор базы знаний
            # просит модель написать шаги сразу для нескольких карточек, и
            # двадцати секунд общего таймаута ей не хватает. В диалоге,
            # наоборот, ждать дольше нельзя — там остаётся значение по умолчанию.
            api = _client.with_options(timeout=timeout) if timeout else _client
            resp = api.chat.completions.create(**kwargs)
            log.info("llm ok model=%s attempt=%s %.2fs", model, attempt, time.monotonic() - t0)
            return (resp.choices[0].message.content or "").strip()
        except Exception as exc:
            log.warning("llm fail model=%s attempt=%s: %s", model, attempt, exc)
            msg = str(exc)
            # Узнали особенность модели — записываем её насовсем, чтобы
            # следующий вызов начинался сразу с рабочих параметров.
            learned = False
            if "max_completion_tokens" in msg and not use_completion_tokens_param:
                use_completion_tokens_param = True
                quirks["completion_tokens"] = True
                learned = True
                log.info("модель %s требует max_completion_tokens — запомнили", model)
            if "temperature" in msg and not skip_temperature:
                skip_temperature = True
                quirks["no_temperature"] = True
                learned = True
                log.info("модель %s не принимает temperature — запомнили", model)
            if learned:
                _save_quirks()
    return ""