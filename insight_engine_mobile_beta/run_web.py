from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import aiohttp
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from insight_engine.auth_store import AuthStore, PLAN_DEFS, sha256
from insight_engine.web_server import InsightWebService

app = FastAPI(title="INSIGHT Engine Mobile Signal App", version="2.0-local-tiers")
service = InsightWebService()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

ADMIN_CODE = os.getenv("INSIGHT_ADMIN_CODE", "admin123").strip()
COOKIE_NAME = "insight_session"
ADMIN_COOKIE = "insight_admin"
SESSION_DAYS = int(os.getenv("INSIGHT_SESSION_DAYS", "30"))
SESSION_IDLE_SECONDS = int(os.getenv("INSIGHT_SESSION_IDLE_SECONDS", "120"))
AUTH_DB = Path(os.getenv("INSIGHT_AUTH_DB", str(BASE_DIR / "insight_auth_local.sqlite3")))
auth_store = AuthStore(AUTH_DB)


def _request_is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"


def _device_id_from_request(request: Request) -> str:
    return request.headers.get("x-insight-device", "").strip()


def _tab_id_from_request(request: Request) -> str:
    return request.headers.get("x-insight-tab", "").strip()


def _session_token(request: Request) -> str:
    return request.cookies.get(COOKIE_NAME, "").strip()


def _client_ip(request: Request) -> str:
    xf = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return xf or (request.client.host if request.client else "")


def _admin_authorized(request: Request) -> bool:
    token = request.cookies.get(ADMIN_COOKIE, "")
    return bool(ADMIN_CODE and token == sha256(ADMIN_CODE + ":admin"))


def _current_user(request: Request, touch: bool = False) -> Optional[dict[str, Any]]:
    return auth_store.validate_session(
        _session_token(request),
        _device_id_from_request(request),
        _tab_id_from_request(request),
        SESSION_IDLE_SECONDS,
        touch=touch,
    )


def _require_user(request: Request) -> dict[str, Any]:
    user = _current_user(request, touch=True)
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    return user


def _require_admin(request: Request) -> None:
    if not _admin_authorized(request):
        raise HTTPException(status_code=401, detail="Admin login required")


async def notify_telegram(text: str) -> None:
    token = os.getenv("INSIGHT_TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("INSIGHT_TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(url, json={"chat_id": chat_id, "text": text[:3500], "disable_web_page_preview": True}, timeout=aiohttp.ClientTimeout(total=8))
    except Exception:
        pass


@app.on_event("startup")
async def startup_event() -> None:
    if os.getenv("INSIGHT_AUTOSTART", "1") == "1":
        await service.start()


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await service.stop()


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/admin")
async def admin_page():
    return FileResponse(STATIC_DIR / "admin.html")


@app.get("/health")
async def health():
    state = await service.state()
    return {"ok": state["status"].startswith("Live") or state["status"] in {"Starting", "Stopped"}, "status": state["status"]}


@app.get("/api/public/config")
async def public_config():
    wallet = os.getenv("INSIGHT_PAYMENT_WALLET", "YOUR-USDT-WALLET-HERE").strip()
    network = os.getenv("INSIGHT_PAYMENT_NETWORK", "TRC20").strip()
    proof_email = os.getenv("INSIGHT_PAYMENT_EMAIL", "insight@tutorsonline.pk").strip()
    return {
        "brand": os.getenv("INSIGHT_BRAND", "INSIGHT Engine").strip(),
        "payment_wallet": wallet,
        "payment_network": network,
        "payment_email": proof_email,
        "payment_note": os.getenv("INSIGHT_PAYMENT_NOTE", "After payment, enter TX hash here and email screenshot/proof for manual approval.").strip(),
        "plans": PLAN_DEFS,
        "admin_status": auth_store.get_admin_status(),
    }


@app.get("/api/support/status")
async def support_status():
    return auth_store.get_admin_status()


@app.get("/api/auth/status")
async def auth_status(request: Request):
    user = _current_user(request, touch=True)
    return {"auth_required": True, "authorized": bool(user), "user": user}


@app.post("/api/signup")
async def signup(request: Request):
    try:
        data = await request.json()
        user = auth_store.create_signup(
            name=str(data.get("name", "")),
            email=str(data.get("email", "")),
            pin=str(data.get("pin", "")),
            plan=str(data.get("plan", "basic")),
            payment_network=str(data.get("payment_network", "")),
            tx_hash=str(data.get("tx_hash", "")),
            note=str(data.get("note", "")),
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await notify_telegram(
        "New INSIGHT access request\n"
        f"Name: {user['name']}\nEmail: {user['email']}\nPlan: {user['plan_name']}\n"
        f"TX: {user.get('tx_hash') or '-'}\nOpen admin panel to approve."
    )
    return {"ok": True, "message": "Request submitted. Email payment screenshot/proof and wait for admin approval.", "user": user}


@app.post("/api/login")
async def login(request: Request):
    try:
        data = await request.json()
        token, user = auth_store.login(
            email=str(data.get("email", "")),
            pin=str(data.get("pin", "")),
            device_id=str(data.get("device_id", "")),
            tab_id=str(data.get("tab_id", "")),
            user_agent=request.headers.get("user-agent", ""),
            ip=_client_ip(request),
            idle_seconds=SESSION_IDLE_SECONDS,
            session_days=SESSION_DAYS,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=423, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    response = JSONResponse({"ok": True, "user": user})
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=_request_is_https(request),
        samesite="lax",
    )
    return response


@app.post("/api/heartbeat")
async def heartbeat(request: Request):
    user = _require_user(request)
    return {"ok": True, "user": user}


@app.post("/api/logout")
async def logout(request: Request):
    auth_store.logout(_session_token(request))
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE_NAME)
    return response


@app.post("/api/chat")
async def chat(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    user = _current_user(request, touch=True)
    name = user["name"] if user else str(data.get("name", ""))
    email = user["email"] if user else str(data.get("email", ""))
    try:
        row = auth_store.add_chat(
            user_id=user["id"] if user else None,
            name=name,
            email=email,
            message=str(data.get("message", "")),
            sender="user",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await notify_telegram(
        "New INSIGHT support message\n"
        f"Name: {name or '-'}\nEmail: {email or '-'}\nMessage: {row['message']}"
    )
    return {"ok": True, "message": "Message sent. Admin will reply/approve from dashboard."}


@app.get("/api/state")
async def state(request: Request):
    user = _require_user(request)
    return await service.state(limit=user.get("plan_limit", 5))


@app.post("/api/start")
async def start(request: Request):
    _require_user(request)
    await service.start()
    return await service.state()


@app.post("/api/stop")
async def stop(request: Request):
    _require_user(request)
    await service.stop()
    return await service.state()


@app.post("/api/admin/login")
async def admin_login(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    code = str(data.get("code", "")).strip()
    if not ADMIN_CODE or code != ADMIN_CODE:
        raise HTTPException(status_code=403, detail="Wrong admin code")
    response = JSONResponse({"ok": True})
    response.set_cookie(
        ADMIN_COOKIE,
        sha256(ADMIN_CODE + ":admin"),
        max_age=24 * 60 * 60,
        httponly=True,
        secure=_request_is_https(request),
        samesite="lax",
    )
    return response


@app.post("/api/admin/logout")
async def admin_logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(ADMIN_COOKIE)
    return response


@app.post("/api/admin/heartbeat")
async def admin_heartbeat(request: Request):
    _require_admin(request)
    auth_store.admin_heartbeat()
    return {"ok": True, "status": auth_store.get_admin_status()}


@app.get("/api/admin/data")
async def admin_data(request: Request):
    _require_admin(request)
    auth_store.admin_heartbeat()
    return {
        "ok": True,
        "users": auth_store.list_users(),
        "chats": auth_store.list_chats(),
        "admin_status": auth_store.get_admin_status(),
        "plans": PLAN_DEFS,
    }


@app.post("/api/admin/status")
async def admin_status(request: Request):
    _require_admin(request)
    data = await request.json()
    auth_store.set_admin_status(str(data.get("status", "away")))
    auth_store.admin_heartbeat()
    return {"ok": True, "status": auth_store.get_admin_status()}


@app.post("/api/admin/user/{user_id}/{action}")
async def admin_user_action(user_id: int, action: str, request: Request):
    _require_admin(request)
    data = {}
    try:
        data = await request.json()
    except Exception:
        pass
    try:
        if action == "approve":
            user = auth_store.approve_user(user_id, days=int(data.get("days", 30)))
            await notify_telegram(f"INSIGHT user approved: {user['email']} ({user['plan_name']})")
            return {"ok": True, "user": user}
        if action == "reject":
            auth_store.reject_user(user_id)
        elif action == "disable":
            auth_store.disable_user(user_id)
        elif action == "reset_device":
            auth_store.reset_device(user_id)
        elif action == "reset_session":
            auth_store.reset_session(user_id)
        elif action == "extend":
            user = auth_store.extend_user(user_id, days=int(data.get("days", 30)))
            return {"ok": True, "user": user}
        else:
            raise ValueError("Unknown action")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}
