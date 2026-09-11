"""
Прогрев кэша векторов обращений.

    python3 -m tools.warm_queries

Зачем. Вектор обращения считается по сети, внутри обработки сообщения
пользователя. Первое обращение каждой новой формулировкой платит за это
ожиданием — секунды, которых на защите нет. Скрипт считает вектора заранее
для фраз, которые точно прозвучат: кнопки стартового экрана, сценарии
наполнения базы, заголовки и симптомы всех карточек.

Результат — `data/emb_queries.npz`. Файл переживает пересборку контейнера
(каталог `data/` смонтирован томом), и на демонстрации сеть для поиска
не нужна вообще.

Запускать после каждой правки базы знаний — вместе с tools/build_emb_index.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.kb_store import all_articles   # noqa: E402
from kb import embeddings                # noqa: E402

BATCH = 64

# Фразы с кнопок стартового экрана и типовые формулировки демонстрации.
DEMO_PHRASES = [
    "Не подключается VPN",
    "Не могу подключиться к корпоративному Wi-Fi",
    "Не печатает принтер",
    "не пускает во впн",
    "впн не работает",
    "не могу зайти в рабочую сеть из дома",
    "ноут не видит корпоративную сеть",
    "подключился к вайфаю, а интернета нет",
    "письма висят в исходящих",
    "забыл пароль от учётной записи",
    "бумага есть, а печати нет",
    "комп тормозит",
    "чёрный экран при включении",
    "программа вылетает",
    "не приходит код подтверждения",
]


def scenario_phrases() -> list[str]:
    """Тексты обращений из сценария наполнения базы, если он на месте."""
    try:
        from tests.seed_demo import SCENARIOS
    except Exception:  # noqa: BLE001
        return []
    return [s["text"] for s in SCENARIOS if s.get("text")]


def main() -> None:
    if embeddings.load_store() is None:
        raise SystemExit("Сначала соберите индекс: python3 -m tools.build_emb_index")

    embeddings.load_query_cache()

    phrases = list(DEMO_PHRASES) + scenario_phrases()
    for article in all_articles():
        phrases.append(article.title)
        phrases.extend(article.symptoms)

    # Убираем повторы, сохраняя порядок
    seen, unique = set(), []
    for p in phrases:
        key = embeddings.text_key(p)
        if key not in seen:
            seen.add(key)
            unique.append(p)

    todo = [p for p in unique if embeddings.text_key(p) not in embeddings._query_cache]
    print(f"Фраз всего: {len(unique)}, из них новых: {len(todo)}")
    if not todo:
        print("Всё уже прогрето.")
        return

    from llm.client import embed
    model = os.getenv("LLM_MODEL_EMBED", "text-embedding-3-small")
    for start in range(0, len(todo), BATCH):
        chunk = todo[start:start + BATCH]
        vectors = embed(chunk, model=model)
        for text, vector in zip(chunk, vectors):
            embeddings._query_cache[embeddings.text_key(text)] = vector
        print(f"  посчитано {min(start + BATCH, len(todo))} из {len(todo)}")

    embeddings.save_query_cache()
    print(f"\nГотово: {embeddings.QUERY_CACHE_PATH}, "
          f"векторов {len(embeddings._query_cache)}")
    print("На сервере этот файл окажется сам: каталог data/ смонтирован томом.")
    print("Если сервер отдельный — прогрейте его так же после выката.")


if __name__ == "__main__":
    main()
