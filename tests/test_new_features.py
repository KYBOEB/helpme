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


# -------------------------------------------------- 6. время с часовым поясом

d = chat("не подключается VPN").json()
tid6, tok6 = d["ticket_id"], d["token"]
brief = client.get(f"/api/tickets/{tid6}").json()
check("время отдаётся с пометкой часового пояса",
      brief["created_at"].endswith("+00:00"), brief["created_at"])
check("у обращения есть короткий номер",
      isinstance(brief["public_no"], int) and brief["public_no"] > 1000,
      str(brief.get("public_no")))
check("короткий номер виден пользователю в карточке",
      d.get("ticket_card") is None or d["ticket_card"].get("public_no", 0) > 0,
      "нет номера в карточке")


# ------------------------------------------- 7. пользователь вышел из диалога

r = client.post(f"/api/tickets/{tid6}/close", json={"token": tok6})
check("пользователь может закрыть обращение", r.status_code == 200, str(r.status_code))
brief = client.get(f"/api/tickets/{tid6}").json()
check("закрытое пользователем обращение не активно",
      brief["state"] == "RESOLVED" and brief["closed_by_user"] is True, str(brief)[:150])
check("закрытое пользователем не ждёт специалиста",
      brief["needs_specialist"] is False, str(brief["needs_specialist"]))
r = client.post(f"/api/tickets/{tid6}/close", json={"token": "s_чужой"})
check("чужим токеном обращение не закрыть", r.status_code == 404, str(r.status_code))


# ---------------------------------------------------------- 8. оценка 1 и 0

d = chat("не подключается VPN").json()
tid8, tok8 = d["ticket_id"], d["token"]
r = client.post(f"/api/tickets/{tid8}/rate", json={"rating": 1, "token": tok8})
check("оценка «помогло» принимается", r.status_code == 200, str(r.status_code))
r = client.post(f"/api/tickets/{tid8}/rate", json={"rating": 5, "token": tok8})
check("старая пятибалльная оценка отклоняется", r.status_code == 422, str(r.status_code))
r = client.post(f"/api/tickets/{tid8}/rate", json={"rating": True, "token": tok8})
check("булево значение вместо оценки отклоняется", r.status_code == 422, str(r.status_code))
s = client.get("/api/stats").json()
check("полезность считается долей от 0 до 1",
      s["usefulness"] is None or 0.0 <= s["usefulness"] <= 1.0, str(s.get("usefulness")))


# ------------------------------------- 9. каскад: специалист после провала

_route_plan.append(RouteResult(category="рабочее место", article_id=None,
                               confidence=0.9, problem_summary="странная проблема"))
d = chat("монитор издаёт странный запах жжёного пластика").json()
tid9, tok9 = d["ticket_id"], d["token"]
check("общая рекомендация выдаёт шаги", d["reply"]["type"] == "steps", str(d["reply"])[:150])
check("в общей рекомендации нет кнопки «Позвать специалиста»",
      not d["reply"].get("quick_replies"), str(d["reply"].get("quick_replies")))
check("текст не дублирует сообщение о передаче специалисту",
      d["reply"]["text"].count("специалист") <= 1, d["reply"]["text"][:160])
brief = client.get(f"/api/tickets/{tid9}").json()
check("общая рекомендация НЕ ставит обращение в очередь",
      brief["needs_specialist"] is False, str(brief["needs_specialist"]))
d2 = chat("не помогло", ticket_id=tid9, token=tok9).json()
check("после провала рекомендаций зовём специалиста",
      d2["state"] == "ESCALATED", str(d2["state"]))
check("в сообщении об эскалации нет внутреннего номера",
      "t_" not in d2["reply"]["text"], d2["reply"]["text"][:160])


# ------------------------ 10. просьба о специалисте без описания проблемы

d = chat("вызови специалиста, я ниче не понимаю").json()
tid10, tok10 = d["ticket_id"], d["token"]
check("на просьбу о специалисте просим описать проблему",
      d["reply"]["type"] == "question" and d["state"] != "ESCALATED", str(d)[:200])
check("общие шаги при этом не выдаются", not d["reply"].get("steps"), str(d["reply"])[:150])
check("предложена кнопка позвать специалиста всё равно",
      any("специалист" in q.lower() for q in d["reply"]["quick_replies"]),
      str(d["reply"]["quick_replies"]))
d2 = chat(quick_reply=d["reply"]["quick_replies"][0],
          ticket_id=tid10, token=tok10).json()
check("по кнопке обращение уходит специалисту",
      d2["state"] == "ESCALATED", str(d2["state"]))

# описание проблемы вместе с просьбой обрабатывается как обычно
d = chat("не работает VPN, позовите специалиста").json()
check("описанная проблема не принимается за пустую просьбу",
      d["reply"]["type"] in ("steps", "question", "choice") and bool(d["category"]),
      str(d)[:200])


# ------------------------------------------- 11. завершение обращения оператором

d = chat("не подключается VPN").json()
tid11, tok11 = d["ticket_id"], d["token"]
client.post(f"/api/tickets/{tid11}/reply", json={"text": "Подключаюсь к заявке."})
r = client.post(f"/api/tickets/{tid11}/resolve")
check("специалист может завершить обращение",
      r.status_code == 200 and r.json()["state"] == "RESOLVED", str(r.json()))
upd = client.post("/api/chat/updates",
                  json={"ticket_id": tid11, "token": tok11, "after": 0}).json()
check("пользователь видит, что обращение закрыто",
      upd["state"] == "RESOLVED", str(upd["state"]))
r = client.post("/api/tickets/t_nosuch/resolve")
check("завершение несуществующего обращения даёт 404", r.status_code == 404,
      str(r.status_code))


# ------------------------------------------------ 12. период в метриках

for period in ("day", "week", "month", "all"):
    s = client.get(f"/api/stats?period={period}").json()
    check(f"метрики за период «{period}» считаются",
          s["period"] == period and isinstance(s["total"], int), str(s)[:120])
s_day = client.get("/api/stats?period=day").json()
s_all = client.get("/api/stats?period=all").json()
check("за сутки обращений не больше, чем за всё время",
      s_day["total"] <= s_all["total"], f'{s_day["total"]} > {s_all["total"]}')


# ------------------- 13. ассистент не обещает того, чего не было

from core import assist as _assist_mod  # noqa: E402

for promise in ("Готовой инструкции нет, обращение передано специалисту.",
                "Заявка зарегистрирована, с вами свяжется инженер поддержки.",
                "Передаю вопрос эксперту, а пока попробуйте это."):
    cleaned = _assist_mod._sanitize(AnswerResult(text=promise, steps=["шаг"]))
    check(f"вступление «{promise[:32]}…» очищено",
          "специалист" not in cleaned.text.lower()
          and "эксперт" not in cleaned.text.lower()
          and "свяж" not in cleaned.text.lower(),
          cleaned.text[:90])

ok_intro = "Готовой инструкции нет, попробуем безопасные общие шаги."
check("нормальное вступление не трогаем",
      _assist_mod._sanitize(AnswerResult(text=ok_intro, steps=["шаг"])).text == ok_intro,
      "текст изменён")


# ------------------- 14. нет двух сообщений об одном и том же

_route_plan.append(RouteResult(category="VPN", article_id=None, confidence=0.9,
                               problem_summary="что-то с впн"))
d = chat("не подключается VPN").json()
check("при отсутствии статьи вступление не пустое",
      bool(d["reply"]["text"]), "пустой текст")

# шаги из базы не помогли → общие рекомендации без повторного вступления
d = chat("не подключается VPN").json()
tid14, tok14 = d["ticket_id"], d["token"]
if d["reply"]["type"] == "question":            # ответили на уточняющий вопрос
    d = chat(quick_reply=d["reply"]["quick_replies"][0],
             ticket_id=tid14, token=tok14).json()
d2 = chat("не помогло", ticket_id=tid14, token=tok14).json()
check("после «не помогло» текст не дублирует подводку интерфейса",
      d2["reply"]["text"] == "" or "не помогли" not in d2["reply"]["text"],
      d2["reply"]["text"][:120])


# ------------------- 15. карточка обращения доступна, но не в чате

d = chat("не подключается VPN").json()
tid15, tok15 = d["ticket_id"], d["token"]
r = client.post(f"/api/tickets/{tid15}/escalate", json={"token": tok15})
check("эскалация по кнопке работает", r.status_code == 200, str(r.status_code))
card = client.get(f"/api/tickets/{tid15}/export.json").json()
check("карточка обращения формируется и выгружается",
      card["ticket_id"] == tid15 and "messages" in card and "public_no" in card,
      str(card)[:140])


# ------------------- 16. экран уточнения: конкретные проблемы, а не категории

from core import dialog as _dialog  # noqa: E402

_route_plan.append(RouteResult(category="VPN", article_id="KB-VPN-001",
                               confidence=0.4, problem_summary="непонятно что"))
d = chat("не подключается vpn на ноутбуке").json()
tid16, tok16 = d["ticket_id"], d["token"]
opts = d["reply"].get("quick_replies", [])
check("при низкой уверенности показываем варианты",
      d["reply"]["type"] == "choice" and len(opts) >= 2, str(d["reply"])[:180])
check("последний вариант — «Другое»", opts and opts[-1] == _dialog.OTHER_QR,
      str(opts))
check("варианты — заголовки статей, а не категории",
      any(o.strip().lower() in _dialog._by_title for o in opts[:-1]), str(opts))

# выбор варианта ведёт прямо к этой статье
title = next(o for o in opts[:-1] if o.strip().lower() in _dialog._by_title)
d2 = chat(quick_reply=title, ticket_id=tid16, token=tok16).json()
expected_id = _dialog._by_title[title.strip().lower()].id
check("выбранный вариант ведёт именно к этой статье",
      (d2.get("article") or {}).get("id") == expected_id
      or d2["reply"]["type"] == "question",
      str(d2.get("article")))
brief = client.get(f"/api/tickets/{tid16}").json()
check("после выбора варианта статья записана в обращение",
      brief["article_id"] == expected_id, str(brief["article_id"]))
check("уверенность после выбора пользователем максимальная",
      brief["confidence"] == 1.0, str(brief["confidence"]))

# «Другое» просит описать подробнее и не закрывает обращение
_route_plan.append(RouteResult(category="VPN", article_id="KB-VPN-001",
                               confidence=0.4, problem_summary="непонятно"))
d = chat("проблема с vpn подключением").json()
d3 = chat(quick_reply=_dialog.OTHER_QR,
          ticket_id=d["ticket_id"], token=d["token"]).json()
check("«Другое» просит описать подробнее",
      d3["reply"]["type"] == "question" and d3["state"] != "RESOLVED",
      str(d3["reply"])[:150])
check("«Другое» не считается нецелевым обращением",
      "не относится" not in d3["reply"]["text"], d3["reply"]["text"][:120])


# ------------------- 17. полезность не выходит за 100 %

s = client.get("/api/stats").json()
check("полезность не превышает 100%",
      s["usefulness"] is None or 0.0 <= s["usefulness"] <= 1.0,
      str(s.get("usefulness")))


# ------------------- 18. номера обращения нет в сообщениях пользователю

d = chat("не подключается VPN").json()
tid18, tok18 = d["ticket_id"], d["token"]
r = client.post(f"/api/tickets/{tid18}/escalate", json={"token": tok18})
upd = client.post("/api/chat/updates",
                  json={"ticket_id": tid18, "token": tok18,
                        "after": 0, "full": True}).json()
bot_texts = " ".join(m["text"] for m in upd["messages"] if m["role"] == "assistant")
check("в сообщениях пользователю нет номера обращения",
      "№" not in bot_texts, bot_texts[-160:])


print()
if FAILED:
    print(f"ПРОВАЛЕНО {len(FAILED)}: " + "; ".join(FAILED))
    sys.exit(1)
print("Все проверки пройдены.")
