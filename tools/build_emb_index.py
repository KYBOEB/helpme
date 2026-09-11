"""
Сборка индекса эмбеддингов для поиска по смыслу.

Запускается ВРУЧНУЮ, на машине, где работает ключ модели:

    python3 -m tools.build_emb_index

Результат — `kb/emb_index.npz`, он коммитится в репозиторий. На сервере
приложение только читает этот файл: старт не зависит от внешнего сервиса.

Пересобирать нужно после любой правки карточек базы знаний — добавили статью
или переписали симптомы, значит индекс устарел. Приложение это переживёт
(неизвестные тексты досчитаются на лету), но лучше не оставлять так.
"""
from __future__ import annotations

import os
import sys

# Чтобы скрипт работал и как `python3 tools/build_emb_index.py`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kb import embeddings                      # noqa: E402
from core.kb_store import all_articles         # noqa: E402
from kb.retriever import _article_text         # noqa: E402

BATCH = 64

# Названия моделей у провайдеров отличаются префиксом. Перебираем и сообщаем,
# какое подошло: подставлять его в .env вручную — лишний шаг и лишняя ошибка.
CANDIDATES = [
    os.getenv("LLM_MODEL_EMBED", ""),
    "text-embedding-3-small",
    "openai/text-embedding-3-small",
    "text-embedding-3-large",
]


def pick_model(sample: str) -> str:
    from llm.client import embed
    tried = []
    for model in [m for m in CANDIDATES if m and m not in tried]:
        tried.append(model)
        try:
            embed([sample], model=model)
            return model
        except Exception as exc:  # noqa: BLE001
            print(f"  {model}: не подошла ({str(exc)[:120]})")
    raise SystemExit(
        "Ни одна модель векторизации не ответила. Проверьте LLM_API_KEY, "
        "баланс ProxyAPI и доступность сети, затем запустите скрипт заново."
    )


def main() -> None:
    articles = all_articles()      # карточки репозитория + добавленные оператором
    if not articles:
        raise SystemExit("База знаний пуста — нечего индексировать.")

    texts = [_article_text(a) for a in articles]
    print(f"Карточек в базе знаний: {len(articles)}")

    print("Подбираю модель векторизации…")
    model = pick_model(texts[0])
    print(f"  подошла: {model}")
    os.environ["LLM_MODEL_EMBED"] = model

    from llm.client import embed
    store: dict[str, list[float]] = {}
    for start in range(0, len(texts), BATCH):
        chunk = texts[start:start + BATCH]
        vectors = embed(chunk, model=model)
        for text, vector in zip(chunk, vectors):
            store[embeddings.text_key(text)] = vector
        print(f"  посчитано {min(start + BATCH, len(texts))} из {len(texts)}")

    path = embeddings.save_store(store, model=model)
    print(f"\nГотово: {path}, векторов {len(store)}")
    print("Дальше:")
    print(f"  1. добавьте в .env строку  LLM_MODEL_EMBED={model}")
    print("  2. закоммитьте kb/emb_index.npz")
    print("  3. проверьте поиск:  python3 -m tools.check_search")


if __name__ == "__main__":
    main()
