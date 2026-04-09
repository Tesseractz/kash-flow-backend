import os
import time
from collections import defaultdict, deque
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(dotenv_path=_ENV_PATH, override=False)

from app.api.routers import billing, customers, expenses, legal_health, notifications, privacy, products, profile_plan, reports, users
from app.core.http_config import allowed_origins
from app.core.password_crypto import _decrypt_password, _encrypt_password

app = FastAPI(title="KashPoint API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_rl_window_sec = int(os.getenv("RATE_LIMIT_WINDOW_SEC", "60"))
_rl_max_requests = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "120"))
_rl_buckets = defaultdict(lambda: deque())
_rl_paths = {
    ("POST", "/billing/checkout"),
    ("POST", "/billing/portal"),
    ("POST", "/billing/paystack/sync"),
    ("POST", "/paystack/webhook"),
}


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    method = request.method.upper()
    path = request.url.path

    if (method, path) in _rl_paths:
        forwarded_for = request.headers.get("x-forwarded-for")
        ip = (forwarded_for.split(",")[0].strip() if forwarded_for else None) or (
            request.client.host if request.client else "unknown"
        )
        key = f"{ip}:{method}:{path}"
        now = time.time()
        q = _rl_buckets[key]

        while q and (now - q[0]) > _rl_window_sec:
            q.popleft()

        if len(q) >= _rl_max_requests:
            return Response(
                content='{"detail":"Too many requests"}',
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": str(_rl_window_sec)},
            )

        q.append(now)

    return await call_next(request)


app.include_router(products.router)
app.include_router(billing.router)
app.include_router(profile_plan.router)
app.include_router(users.router)
app.include_router(reports.router)
app.include_router(customers.router)
app.include_router(notifications.router)
app.include_router(expenses.router)
app.include_router(privacy.router)
app.include_router(legal_health.router)
