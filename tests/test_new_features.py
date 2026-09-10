"""
Проверка правок последнего дня. Запуск: python3 tests/test_new_features.py

Модель здесь подменяется заглушкой: проверяем поведение автомата и API,
а не качество классификации — за него отвечает регресс-прогон tests/run_cases.py.
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

_route_plan: list[RouteResult] = []
FAILED: list[str] = []


def fake_route(user_text, history, candidates, known_slots):
    if _route_plan:
        return _route_plan.pop(0)
    art = candidates[0] if candidates else None
    return RouteResult(category=art.category if art else "не определено",
                       article_id=art.id if art else None,
                       confidence=0.92, problem_summary=user_text[:100])


demo_cache.cached_route = fake_route
demo_cache.cached_answer = lambda article, slots, text: AnswerResult(
    text="", steps=list(article.steps))
assist.general_help = lambda text, category: AnswerResult(
    text="Готовой инструкции нет.", steps=["Общий шаг 1", "Общий шаг 2"])


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "OK  " if condition else "FAIL"
    print(f"[{mark}] {name}" + (f"  — {detail}" if detail and not condition else ""))
    if not condition:
        FAILED.append(name)


def chat(message=None, ticket_id=None, token=None, quick_reply=None):
    return client.post("/api/chat", json={
        "ticket_id": ticket_id, "token": token, "request_id": str(uuid.uuid4()),
        "message": message, "quick_reply": quick_reply,
    })


def login():
    return client.post("/api/operator/login", json={"password": "test-pass-123"})


# ---------------------------------------------------------------- 1. бессмыслица

r = chat("!!!! 1234 ))))")
d = r.json()
check("бессмыслица не уходит в модель и получает предупреждение",
      r.status_code == 200 and d["reply"]["type"] == "question", str(d)[:200])
check("уверенность при бессмыслице обнулена", d["confidence"] == 0.0, str(d["confidence"]))

r2 = chat("))))))", ticket_id=d["ticket_id"], token=d["token"])
d2 = r2.json()
check("вторая бессмыслица закрывает обращение",
      d2["reply"]["type"] == "closed" and d2["state"] == "RESOLVED", str(d2)[:200])
check("закрытое нецелевое НЕ уходит специалисту",
      d2["ticket_card"]["needs_specialist"] is False
      and d2["ticket_card"]["out_of_scope"] is True, str(d2["ticket_card"]))

r3 = chat("а теперь по делу", ticket_id=d["ticket_id"], token=d["token"])
check("после закрытия обращение не принимает сообщений", r3.status_code == 409,
      str(r3.status_code))


# ------------------------------------------------------- 2. вопрос не по адресу

_route_plan.append(RouteResult(is_out_of_scope=True, off_topic_kind="off_topic",
                               problem_summary="выпал молочный зуб"))
r = chat("у ребёнка выпал молочный зубик, что делать")
d = r.json()
check("нецелевой вопрос не получает шагов", d["reply"]["type"] == "question", str(d)[:200])
check("нецелевой вопрос не получает уверенности 90%", d["confidence"] == 0.0,
      str(d["confidence"]))
check("нецелевой вопрос не получает категории",
      d["category"] in (None, "не определено"), str(d["category"]))
check("в тексте объяснено, чем помощник занимается",
      "техническ" in d["reply"]["text"].lower(), d["reply"]["text"][:80])

_route_plan.append(RouteResult(is_out_of_scope=True, off_topic_kind="abuse"))
d2 = chat("да ты бесполезный кусок кода", ticket_id=d["ticket_id"],
          token=d["token"]).json()
check("повтор нецелевого закрывает обращение", d2["reply"]["type"] == "closed",
      str(d2)[:200])

# нормальное обращение после одного промаха продолжается как обычно
_route_plan.append(RouteResult(is_out_of_scope=True, off_topic_kind="off_topic"))
d = chat("какая завтра погода").json()
d = chat("не подключается VPN", ticket_id=d["ticket_id"], token=d["token"]).json()
check("после одного промаха обращение продолжается нормально",
      d["reply"]["type"] in ("steps", "question", "choice")
      and d["state"] != "RESOLVED", str(d)[:200])


# ------------------------------------------------- 3. уверенность без статьи

_route_plan.append(RouteResult(category="рабочее место", article_id=None,
                               confidence=0.9, problem_summary="что-то странное"))
d = chat("монитор издаёт странный запах жжёного пластика").json()
check("без статьи уверенность равна нулю", d["confidence"] == 0.0, str(d["confidence"]))
check("без статьи ответ помечен как общая рекомендация",
      d["reply"].get("source") == "general" or d["reply"]["type"] == "escalation",
      str(d["reply"])[:200])


# --------------------------------------------- 4. ответ специалиста из панели

d = chat("не подключается VPN").json()
tid, tok = d["ticket_id"], d["token"]

r = client.post(f"/api/tickets/{tid}/reply", json={"text": "Здравствуйте, я подключился."})
check("ответ специалиста без авторизации отклоняется", r.status_code == 401,
      str(r.status_code))

check("вход оператора работает", login().status_code == 200)

r = client.post(f"/api/tickets/{tid}/reply", json={"text": "   "})
check("пустой ответ специалиста отклоняется", r.status_code == 422, str(r.status_code))

r = client.post(f"/api/tickets/{tid}/reply", json={"text": "x" * 2001})
check("слишком длинный ответ отклоняется", r.status_code == 422, str(r.status_code))

r = client.post("/api/tickets/t_nosuch/reply", json={"text": "привет"})
check("ответ по несуществующему обращению даёт 404", r.status_code == 404,
      str(r.status_code))

r = client.post(f"/api/tickets/{tid}/reply",
                json={"text": "Здравствуйте, вижу вашу заявку. Подключаюсь."})
check("ответ специалиста принят", r.status_code == 200 and r.json()["state"] == "ESCALATED",
      str(r.json()))

upd = client.post("/api/chat/updates",
                  json={"ticket_id": tid, "token": tok, "after": 0}).json()
check("пользователь видит реплику специалиста",
      len(upd["messages"]) == 1 and "Подключаюсь" in upd["messages"][0]["text"],
      str(upd)[:200])
last = upd["last_id"]

upd2 = client.post("/api/chat/updates",
                   json={"ticket_id": tid, "token": tok, "after": last}).json()
check("повторный опрос не дублирует сообщение", upd2["messages"] == [], str(upd2)[:200])

r = client.post("/api/chat/updates",
                json={"ticket_id": tid, "token": "s_подделка", "after": 0})
check("чужой токен не даёт читать переписку", r.status_code == 404, str(r.status_code))

r = client.post("/api/chat/updates", json={"ticket_id": tid, "after": 0})
check("опрос без токена не работает", r.status_code == 404, str(r.status_code))

r = chat("да, спасибо, жду", ticket_id=tid, token=tok)
dd = r.json()
check("пользователь может отвечать специалисту",
      r.status_code == 200 and dd["reply"]["type"] == "operator", str(dd)[:200])

card = client.get(f"/api/tickets/{tid}").json()
roles = [m["role"] for m in card["messages"]]
check("реплики специалиста и пользователя видны в панели",
      "operator" in roles and "user" in roles, str(roles))

# нецелевое обращение отвечать нельзя
d_closed = chat("......").json()
chat("qqqqqq", ticket_id=d_closed["ticket_id"], token=d_closed["token"])
r = client.post(f"/api/tickets/{d_closed['ticket_id']}/reply", json={"text": "ответ"})
check("по закрытому нецелевому обращению ответить нельзя", r.status_code == 409,
      str(r.status_code))


# ---------------------------------------------------------------- 5. метрики

s = client.get("/api/stats").json()
check("метрика нецелевых обращений считается", s.get("out_of_scope", 0) >= 3,
      str(s.get("out_of_scope")))
check("нецелевые не попали в очередь специалиста",
      all(not t["out_of_scope"] for t in client.get("/api/tickets?queue=1").json()),
      "в очереди есть нецелевые")
csv_text = client.get("/api/tickets/export.csv").text
check("выгрузка CSV содержит колонку out_of_scope", "out_of_scope" in csv_text,
      csv_text.splitlines()[0][:120] if csv_text else "пусто")


print()
if FAILED:
    print(f"ПРОВАЛЕНО {len(FAILED)}: " + "; ".join(FAILED))
    sys.exit(1)
print("Все проверки пройдены.")
