"""
Одновременные обращения не должны мешать друг другу.

Запрос к внешней модели идёт несколько секунд, и всё это время обращение уже
записано в базу. Без журнала упреждающей записи открытая панель оператора
или второй пользователь упирались в «database is locked».

Запуск: python3 tests/test_concurrency.py
"""
import os, sys, tempfile, threading, time, uuid
sys.path.insert(0, os.getcwd())
tmp = tempfile.mkdtemp()
os.environ.update(DB_PATH=os.path.join(tmp, "t.db"),
                  DEMO_CACHE_PATH=os.path.join(tmp, "c.json"),
                  OPERATOR_PASSWORD="zzz", SECRET_KEY="kkk")
from fastapi.testclient import TestClient
import main
from common.models import AnswerResult, RouteResult
from core import assist, demo_cache

def slow_route(user_text, history, candidates, known_slots):
    time.sleep(2.0)                      # столько же, сколько живой запрос к модели
    art = candidates[0] if candidates else None
    return RouteResult(category=art.category if art else "не определено",
                       article_id=art.id if art else None, confidence=0.92,
                       problem_summary=user_text[:80])

def slow_answer(article, slots, text):
    time.sleep(1.5)
    return AnswerResult(text="", steps=list(article.steps))

demo_cache.cached_route = slow_route
demo_cache.cached_answer = slow_answer
assist.general_help = lambda t, c: AnswerResult(text="нет статьи", steps=["шаг"])

client = TestClient(main.app)
codes, lock = [], threading.Lock()

def one(i):
    r = client.post("/api/chat", json={"ticket_id": None, "token": None,
        "request_id": str(uuid.uuid4()), "message": f"не подключается vpn {i}",
        "quick_reply": None})
    with lock:
        codes.append(r.status_code)

def reader():
    """Панель оператора опрашивает список обращений, пока идут диалоги."""
    client.post("/api/operator/login", json={"password": "zzz"})
    for _ in range(12):
        client.get("/api/tickets"); client.get("/api/stats")
        time.sleep(0.4)

threads = [threading.Thread(target=one, args=(i,)) for i in range(8)]
threads.append(threading.Thread(target=reader))
t0 = time.monotonic()
for t in threads: t.start()
for t in threads: t.join()
ok = codes.count(200) == 8
print()
print(f"[{'OK  ' if ok else 'FAIL'}] восемь одновременных обращений обработаны")
print(f"      коды: {sorted(codes)}, время: {round(time.monotonic() - t0, 1)} с")
sys.exit(0 if ok else 1)
