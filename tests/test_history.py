"""
Проверка истории обращений, возврата к предыдущему и вызова специалиста.
Запуск: python3 tests/test_history.py

Модель подменяется заглушкой: проверяется поведение автомата и API,
а не качество классификации.
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_tmp, "test.db")
os.environ["DEMO_CACHE_PATH"] = os.path.join(_tmp, "cache.json")
os.environ["OPERATOR_PASSWORD"] = "test-pass-123"
os.environ["SECRET_KEY"] = "test-secret-key-for-cookies"

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from common.models import AnswerResult, RouteResult  # noqa: E402
from core import assist, demo_cache  # noqa: E402

client = TestClient(main.app)
FAILED: list[str] = []


def fake_route(user_text, history, candidates, known_slots):
    art = candidates[0] if candidates else None
    return RouteResult(category=art.category if art else "не определено",
                       article_id=art.id if art else None,
                       confidence=0.92, problem_summary=user_text[:100])


demo_cache.cached_route = fake_route
demo_cache.cached_answer = lambda article, slots, text: AnswerResult(
    text="", steps=list(article.steps))
assist.general_help = lambda text, category, exclude_steps=None: AnswerResult(
    text="Готовой инструкции нет.", steps=["Общий шаг 1", "Общий шаг 2"])


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "OK  " if condition else "FAIL"
    print(f"[{mark}] {name}" + (f"  — {detail}" if detail and not condition else ""))
    if not condition:
        FAILED.append(name)


def chat(**kw):
    body = {"request_id": str(uuid.uuid4())}
    body.update(kw)
    return client.post("/api/chat", json=body)


def my_tickets(client_id):
    return client.post("/api/my/tickets", json={"client_id": client_id})


# ------------------------------------------------- 1. идентификатор посетителя

r = chat(message="не работает vpn").json()
cid = r.get("client_id")
check("1.1 идентификатор посетителя выдан при первом обращении", bool(cid), str(r)[:200])
check("1.2 короткий номер приходит в ответе", (r.get("public_no") or 0) > 1000,
      f"public_no={r.get('public_no')}")

r2 = chat(ticket_id=r["ticket_id"], token=r["token"], message="Windows").json()
check("1.3 идентификатор повторно не гоняется по сети",
      r2.get("client_id") is None, f"client_id={r2.get('client_id')}")

# ------------------------------------------------------------- 2. история

first_id = r["ticket_id"]
second = chat(message="не печатает принтер", client_id=cid).json()
hist = my_tickets(cid).json()["tickets"]
ids = [t["ticket_id"] for t in hist]
check("2.1 оба обращения в истории посетителя",
      first_id in ids and second["ticket_id"] in ids, str(ids))
check("2.2 свежие первыми", ids[0] == second["ticket_id"], str(ids))
check("2.3 в списке есть номер, категория и состояние",
      all(k in hist[0] for k in ("public_no", "category", "state", "created_at")))

other = my_tickets("c_" + "x" * 30).json()["tickets"]
check("2.4 чужой идентификатор не видит чужих обращений", other == [], str(other)[:120])
check("2.5 мусорный идентификатор не роняет сервер",
      client.post("/api/my/tickets", json={"client_id": 12345}).status_code == 200)
check("2.6 пустой запрос отдаёт пустой список",
      client.post("/api/my/tickets", json={}).json()["tickets"] == [])

# ------------------------------------------------ 3. переписка закрытого обращения

client.post(f"/api/tickets/{first_id}/close", json={"token": r["token"]})
upd = client.post("/api/chat/updates", json={
    "ticket_id": first_id, "token": r["token"], "after": 0, "full": True}).json()
check("3.1 переписка закрытого обращения читается", len(upd.get("messages", [])) > 0)
# Шаги должны приходить ВМЕСТЕ с репликой бота, а не только отдельным полем
# ticket.steps_json: оно перезаписывается при каждой новой выдаче, и при
# возврате к обращению пользователь видел «давайте по шагам» и пустое место.
_bot = [m for m in upd.get("messages", []) if m["role"] == "assistant"]
check("3.1.1 шаги приходят вместе с репликой бота",
      any(m.get("steps") for m in _bot),
      str([m.get("steps") for m in _bot])[:200])
check("3.1.2 в переписке нет реплик бота без текста и без шагов",
      all(m.get("text") or m.get("steps") for m in _bot),
      str([m.get("text") for m in _bot])[:200])

check("3.2 в переписке есть короткий номер", (upd.get("public_no") or 0) > 1000,
      f"public_no={upd.get('public_no')}")
check("3.3 закрытое обращение не принимает сообщений",
      chat(ticket_id=first_id, token=r["token"], message="ещё раз").status_code == 409)

# --------------------------------------------------------- 4. «проблема вернулась»

again = chat(message="снова не работает vpn",
             client_id=cid,
             parent_ticket_id=first_id,
             parent_token=r["token"]).json()
check("4.1 создано НОВОЕ обращение, а не переоткрыто старое",
      again["ticket_id"] != first_id, again["ticket_id"])
check("4.2 у нового обращения свой токен",
      again["token"] != r["token"])
check("4.3 виден номер исходного обращения",
      again.get("parent_public_no") == r.get("public_no"),
      f"{again.get('parent_public_no')} vs {r.get('public_no')}")
check("4.4 повторное обращение попало в историю того же посетителя",
      again["ticket_id"] in [t["ticket_id"] for t in my_tickets(cid).json()["tickets"]])

denied = chat(message="снова не работает vpn",
              parent_ticket_id=first_id, parent_token="s_подделка")
check("4.5 чужой токен родителя отвергается", denied.status_code == 404,
      str(denied.status_code))

# ------------------------------------------------------- 5. вызов специалиста

blank = chat(message="позовите специалиста").json()
check("5.1 первая просьба без описания — предлагаем помочь",
      blank["state"] != "ESCALATED", blank["state"])

insist = chat(ticket_id=blank["ticket_id"], token=blank["token"],
              message="позовите специалиста").json()
check("5.2 вторая просьба подряд — передаём человеку",
      insist["state"] == "ESCALATED", insist["state"])

described = chat(message="не подключается vpn").json()
called = chat(ticket_id=described["ticket_id"], token=described["token"],
              quick_reply="Позвать специалиста").json()
check("5.3 когда проблема описана — передаём сразу",
      called["state"] == "ESCALATED", called["state"])

# Кнопка не должна записываться как ответ на уточняющий вопрос
slots = client.get("/api/tickets/" + described["ticket_id"])
check("5.4 просьба о специалисте не попала в карточку как ответ на вопрос",
      "специалист" not in str(called.get("ticket_card") or {}).lower(),
      str(called.get("ticket_card"))[:160])

print()
if FAILED:
    print(f"ПРОВАЛЕНО: {len(FAILED)}")
    for name in FAILED:
        print("  -", name)
    sys.exit(1)
print("Все проверки пройдены")
