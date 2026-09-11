"""
Самопроверка LLM-слоя без остального проекта.
Запуск: python -m llm.selftest
"""
from __future__ import annotations

from common.models import Article, Slot
from llm import client, router, answerer

def test_client() -> None:
    print("=== 1. Проверка клиента (chat) ===")
    reply = client.chat([{"role": "user", "content": "Ответь одним словом: тест"}])
    if reply:
        print("OK:", reply)
    else:
        print("ОШИБКА: пустой ответ. Проверь LLM_API_KEY и LLM_BASE_URL в .env")
    print()


FAKE_ARTICLES = [
    Article(
        id="wifi-001",
        category="Wi-Fi",
        title="Не подключается корпоративный Wi-Fi",
        symptoms=["не вижу сеть", "не подключается wifi", "пишет неверный пароль"],
        required_slots=[Slot(key="os", question="Какая у вас операционная система?")],
        steps=["Забудьте сеть в настройках", "Подключитесь заново и введите пароль", "Перезагрузите роутер"],
    ),
    Article(
        id="vpn-001",
        category="VPN",
        title="VPN не подключается",
        symptoms=["не работает vpn", "ошибка подключения vpn"],
        required_slots=[],
        steps=["Проверьте интернет-соединение", "Перезапустите VPN-клиент", "Обратитесь в поддержку"],
    ),
]

TEST_PHRASES = [
    "не могу подключиться к рабочему вайфаю",
    "вин пишет неверный пароль от вифи",
    "впн выдаёт ошибку при подключении",
    "не приходит почта на корпоративный ящик",
    "забыл пароль от учётки",
]


def test_router() -> None:
    print("=== 2. Проверка route() ===")
    print(f"{'фраза':45} {'категория':20} {'уверенность'}")
    for phrase in TEST_PHRASES:
        result = router.route(phrase, [], FAKE_ARTICLES, {})
        print(f"{phrase:45} {result.category:20} {result.confidence:.2f}")
    print()


def test_answerer() -> None:
    print("=== 3. Проверка make_answer() ===")
    article = FAKE_ARTICLES[0]
    result = answerer.make_answer(article, {"os": "Windows"}, "не могу подключиться к рабочему вайфаю")
    print("Текст:", result.text)
    print("Шаги:")
    for step in result.steps:
        print(" -", step)
    print()

def test_clarify() -> None:
    print("=== 4. Проверка make_clarifying_question() ===")
    slot = Slot(key="os", question="Какая у вас операционная система?")
    question = answerer.make_clarifying_question(slot, "не могу подключиться к wifi")
    print(question)
    print()


if __name__ == "__main__":
    test_client()
    test_router()
    test_answerer()
    test_clarify()