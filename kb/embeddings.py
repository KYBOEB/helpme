"""
Эмбеддинги для поиска по смыслу.

Зачем это появилось. `HybridRetriever` с самого начала умел объединять BM25
и эмбеддинги через RRF, но `embed_fn` ему никто не передавал — в проде работал
чистый BM25. Это лексический поиск: он находит статью, только если в обращении
встретились те же слова, что в карточке. «Не пускает во впн», «впн отваливается»,
«впн не рабоатет» мимо `symptoms` — статья не находится, и каскад уходит на
общий совет. Комиссия отборочного этапа написала об этом прямым текстом.

Как устроено здесь:

  * вектора карточек считаются ЗАРАНЕЕ, скриптом `tools/build_emb_index.py`,
    и лежат в репозитории (`kb/emb_index.npz`). На старте приложения сетевых
    вызовов нет — иначе развёртывание зависело бы от чужого сервиса;
  * вектор обращения пользователя считается на лету и кэшируется в памяти;
  * если индекса нет, он собран другой моделью или сеть недоступна — поиск
    честно откатывается на BM25. Приложение при этом стартует и работает.

Последнее важно: любая ошибка здесь не имеет права уронить прод за сутки
до защиты.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import os

import numpy as np

log = logging.getLogger(__name__)

INDEX_PATH = os.getenv("KB_EMB_INDEX", "kb/emb_index.npz")

# Сколько векторов обращений держим в памяти. Обращения повторяются (демонстрация,
# наполнение базы, типовые проблемы), поэтому кэш экономит и время, и деньги.
_QUERY_CACHE_MAX = 2000
_query_cache: dict[str, np.ndarray] = {}


class EmbeddingsUnavailable(RuntimeError):
    """Вектора получить не удалось: нет индекса, нет сети или ответ битый."""


# ---------------------------------------------------------------- ключи

def text_key(text: str) -> str:
    """Устойчивый ключ текста.

    Нормализуем пробелы и регистр: иначе перенос строки в карточке сделает
    заранее посчитанный вектор ненайденным, и приложение полезет в сеть
    там, где не должно.
    """
    normalized = " ".join((text or "").split()).lower()
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- индекс

def _current_model() -> str:
    return os.getenv("LLM_MODEL_EMBED", "text-embedding-3-small")


def load_store(path: str | None = None) -> dict[str, np.ndarray] | None:
    """Прочитать заранее посчитанные вектора.

    Возвращает None, если файла нет или он собран другой моделью — смешивать
    вектора разных моделей нельзя, расстояния между ними ничего не значат.
    """
    path = path or INDEX_PATH
    if not os.path.exists(path):
        log.info("индекс эмбеддингов не найден (%s), поиск работает на BM25", path)
        return None
    try:
        data = np.load(path, allow_pickle=False)
        keys = [str(k) for k in data["keys"]]
        vectors = np.asarray(data["vectors"], dtype=np.float32)
        model = str(data["model"].item()) if "model" in data else ""
    except Exception as exc:  # noqa: BLE001
        log.warning("индекс эмбеддингов не читается (%s), поиск на BM25: %s", path, exc)
        return None

    if len(keys) != vectors.shape[0]:
        log.warning("индекс эмбеддингов повреждён: %s ключей на %s векторов",
                    len(keys), vectors.shape[0])
        return None

    if model and model != _current_model():
        log.warning("индекс собран моделью %s, сейчас настроена %s — "
                    "индекс не используется, пересоберите tools/build_emb_index.py",
                    model, _current_model())
        return None

    log.info("индекс эмбеддингов загружен: %s векторов, модель %s, размерность %s",
             len(keys), model or "не указана", vectors.shape[1])
    return dict(zip(keys, vectors))


def save_store(store: dict[str, np.ndarray], path: str | None = None,
               model: str | None = None) -> str:
    """Записать индекс. Вызывается только скриптом сборки."""
    path = path or INDEX_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    keys = list(store.keys())
    vectors = np.asarray([store[k] for k in keys], dtype=np.float32)
    np.savez_compressed(
        path,
        keys=np.array(keys),
        vectors=vectors,
        model=np.array(model or _current_model()),
        built_at=np.array(dt.datetime.now(dt.timezone.utc).isoformat()),
    )
    return path


# ---------------------------------------------------------------- вектора

def _remote(texts: list[str]) -> np.ndarray:
    """Сходить за векторами во внешний сервис. Бросает исключение при любой беде."""
    from llm.client import embed as remote_embed   # импорт здесь: сборка индекса
                                                   # не должна тянуть весь слой LLM
    vectors = remote_embed(texts, model=_current_model())
    matrix = np.asarray(vectors, dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[0] != len(texts):
        raise EmbeddingsUnavailable(
            f"сервис вернул массив формы {matrix.shape}, ожидалось ({len(texts)}, dim)")
    return matrix


def _cache_query(key: str, vector: np.ndarray) -> None:
    if len(_query_cache) >= _QUERY_CACHE_MAX:
        _query_cache.clear()          # кэш вспомогательный, LRU здесь избыточен
    _query_cache[key] = vector


def make_embed_fn(store: dict[str, np.ndarray] | None = None):
    """Собрать функцию векторизации для `HybridRetriever`.

    Возвращает None, если заранее посчитанного индекса нет — тогда вызывающая
    сторона строит поиск без эмбеддингов, а не ходит в сеть на каждый запрос.

    Сама функция сначала смотрит в индекс и в кэш, и только за неизвестными
    текстами идёт в сеть. На старте приложения все тексты карточек известны,
    поэтому сетевых вызовов там ноль.
    """
    store = store if store is not None else load_store()
    if store is None:
        return None

    def embed_fn(texts: list[str]) -> np.ndarray:
        keys = [text_key(t) for t in texts]
        missing = [i for i, k in enumerate(keys)
                   if k not in store and k not in _query_cache]

        if missing:
            fresh = _remote([texts[i] for i in missing])
            for position, i in enumerate(missing):
                _cache_query(keys[i], fresh[position])

        out = []
        for k in keys:
            vector = store.get(k)
            if vector is None:
                vector = _query_cache[k]
            out.append(vector)
        return np.asarray(out, dtype=np.float32)

    return embed_fn
