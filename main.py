"""
Точка входа приложения «Помоги мне».

Запуск локально:   uvicorn main:app --reload
Запуск в Docker:   docker compose up -d --build
"""
from __future__ import annotations

import logging
import os
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("helpme")

from api import chat as chat_api        # noqa: E402
from api import tickets as tickets_api  # noqa: E402
from db import repo                     # noqa: E402

repo.init_db()
log.info("база инициализирована")

app = FastAPI(title="Помоги мне — виртуальная техподдержка", version="0.2.0")


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    """Ни один сбой не выходит наружу трейсбеком: в лог — детали, наружу — номер."""
    error_id = uuid.uuid4().hex[:8]
    log.exception("необработанная ошибка %s на %s", error_id, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": f"Внутренняя ошибка. Код для поддержки: {error_id}"},
    )


app.include_router(chat_api.router)
app.include_router(tickets_api.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "demo_mode": os.getenv("DEMO_MODE", "0") == "1"}


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse("static/index.html")
