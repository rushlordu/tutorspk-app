from __future__ import annotations

import hashlib
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional


PLAN_DEFS = {
    "basic": {"name": "Basic", "price": 10, "limit": 5, "days": 30},
    "pro": {"name": "Pro", "price": 15, "limit": 10, "days": 30},
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or utc_now()).isoformat()


def parse_dt(value: str | None) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


@dataclass
class UserSession:
    user_id: int
    name: str
    email: str
    plan: str
    plan_limit: int
    status: str
    expires_at: Optional[str]


class AuthStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE,
                    pin_hash TEXT NOT NULL,
                    plan TEXT NOT NULL DEFAULT 'basic',
                    status TEXT NOT NULL DEFAULT 'pending',
                    payment_network TEXT,
                    tx_hash TEXT,
                    signup_note TEXT,
                    created_at TEXT NOT NULL,
                    approved_at TEXT,
                    rejected_at TEXT,
                    expires_at TEXT,
                    device_hash TEXT,
                    device_label TEXT,
                    last_login_at TEXT,
                    last_seen_at TEXT,
                    last_ip TEXT,
                    last_user_agent TEXT
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_hash TEXT NOT NULL UNIQUE,
                    user_id INTEGER NOT NULL,
                    device_hash TEXT NOT NULL,
                    tab_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                );
                CREATE TABLE IF NOT EXISTS chats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    name TEXT,
                    email TEXT,
                    sender TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    read_by_admin INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            con.execute(
                "INSERT OR IGNORE INTO settings(key, value, updated_at) VALUES('admin_status', 'away', ?)",
                (iso(),),
            )
            con.execute(
                "INSERT OR IGNORE INTO settings(key, value, updated_at) VALUES('admin_heartbeat', '', ?)",
                (iso(),),
            )

    def _pin_hash(self, email: str, pin: str) -> str:
        return sha256(f"{normalize_email(email)}:{pin.strip()}:insight-v1")

    def _user_to_public(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        plan = (row["plan"] or "basic").lower()
        return {
            "id": row["id"],
            "name": row["name"],
            "email": row["email"],
            "plan": plan,
            "plan_name": PLAN_DEFS.get(plan, PLAN_DEFS["basic"])["name"],
            "plan_limit": PLAN_DEFS.get(plan, PLAN_DEFS["basic"])["limit"],
            "status": row["status"],
            "payment_network": row["payment_network"],
            "tx_hash": row["tx_hash"],
            "signup_note": row["signup_note"],
            "created_at": row["created_at"],
            "approved_at": row["approved_at"],
            "rejected_at": row["rejected_at"],
            "expires_at": row["expires_at"],
            "device_locked": bool(row["device_hash"]),
            "last_login_at": row["last_login_at"],
            "last_seen_at": row["last_seen_at"],
            "last_ip": row["last_ip"],
            "last_user_agent": row["last_user_agent"],
        }

    def create_signup(self, *, name: str, email: str, pin: str, plan: str, payment_network: str, tx_hash: str, note: str = "") -> dict[str, Any]:
        name = (name or "").strip()
        email = normalize_email(email)
        pin = (pin or "").strip()
        plan = (plan or "basic").strip().lower()
        if plan not in PLAN_DEFS:
            raise ValueError("Invalid plan")
        if len(name) < 2:
            raise ValueError("Name is required")
        if "@" not in email or "." not in email:
            raise ValueError("Valid email is required")
        if len(pin) < 4:
            raise ValueError("PIN/password must be at least 4 characters")
        if not (tx_hash or "").strip():
            raise ValueError("Transaction hash is required")
        now = iso()
        with self._connect() as con:
            existing = con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            if existing:
                if existing["status"] == "active":
                    raise PermissionError("This email already has an active account. Please sign in or contact support.")
                con.execute(
                    """
                    UPDATE users
                    SET name=?, pin_hash=?, plan=?, status='pending', payment_network=?, tx_hash=?, signup_note=?, created_at=?, rejected_at=NULL
                    WHERE email=?
                    """,
                    (name, self._pin_hash(email, pin), plan, payment_network.strip(), tx_hash.strip(), note.strip(), now, email),
                )
            else:
                con.execute(
                    """
                    INSERT INTO users(name,email,pin_hash,plan,status,payment_network,tx_hash,signup_note,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (name, email, self._pin_hash(email, pin), plan, "pending", payment_network.strip(), tx_hash.strip(), note.strip(), now),
                )
            row = con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            return self._user_to_public(row)

    def login(self, *, email: str, pin: str, device_id: str, tab_id: str, user_agent: str, ip: str, idle_seconds: int, session_days: int) -> tuple[str, dict[str, Any]]:
        email = normalize_email(email)
        pin = (pin or "").strip()
        if not email or not pin:
            raise ValueError("Email and PIN are required")
        if not device_id or not tab_id:
            raise ValueError("Browser device/session ID missing")
        device_hash = sha256(device_id)
        tab_hash = sha256(tab_id)
        now_dt = utc_now()
        now = iso(now_dt)
        with self._connect() as con:
            user = con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            if not user or user["pin_hash"] != self._pin_hash(email, pin):
                raise PermissionError("Wrong email or PIN")
            if user["status"] == "pending":
                raise PermissionError("Your payment is still pending admin approval.")
            if user["status"] == "rejected":
                raise PermissionError("Your payment request was rejected. Please contact support.")
            if user["status"] != "active":
                raise PermissionError("This account is not active.")
            exp = parse_dt(user["expires_at"])
            if exp and exp <= now_dt:
                raise PermissionError("Your subscription has expired. Please renew access.")
            if user["device_hash"] and user["device_hash"] != device_hash:
                raise RuntimeError("This account is locked to another device/browser. Contact admin for device reset.")
            # one active session/tab at a time
            rows = con.execute("SELECT * FROM sessions WHERE user_id=? AND active=1", (user["id"],)).fetchall()
            for s in rows:
                last_seen = parse_dt(s["last_seen_at"])
                still_alive = bool(last_seen and (now_dt - last_seen).total_seconds() <= idle_seconds)
                if still_alive and s["tab_hash"] != tab_hash:
                    raise RuntimeError("This account is already open in another active browser tab/session. Close it and wait about 2 minutes.")
                if not still_alive:
                    con.execute("UPDATE sessions SET active=0 WHERE id=?", (s["id"],))
            token = secrets.token_urlsafe(32)
            token_hash = sha256(token)
            expires = iso(now_dt + timedelta(days=session_days))
            con.execute(
                """
                INSERT INTO sessions(token_hash,user_id,device_hash,tab_hash,created_at,last_seen_at,expires_at,active)
                VALUES(?,?,?,?,?,?,?,1)
                """,
                (token_hash, user["id"], device_hash, tab_hash, now, now, expires),
            )
            if not user["device_hash"]:
                con.execute("UPDATE users SET device_hash=?, device_label=? WHERE id=?", (device_hash, user_agent[:160], user["id"]))
            con.execute(
                "UPDATE users SET last_login_at=?, last_seen_at=?, last_ip=?, last_user_agent=? WHERE id=?",
                (now, now, ip, user_agent[:300], user["id"]),
            )
            row = con.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
            return token, self._user_to_public(row)

    def validate_session(self, token: str, device_id: str, tab_id: str, idle_seconds: int, touch: bool = False) -> Optional[dict[str, Any]]:
        if not token or not device_id or not tab_id:
            return None
        token_hash = sha256(token)
        device_hash = sha256(device_id)
        tab_hash = sha256(tab_id)
        now_dt = utc_now()
        with self._connect() as con:
            row = con.execute(
                """
                SELECT id, user_id, device_hash, tab_hash, last_seen_at, expires_at
                FROM sessions
                WHERE token_hash=? AND active=1
                """,
                (token_hash,),
            ).fetchone()
            if not row:
                return None
            last_seen = parse_dt(row["last_seen_at"])
            expires = parse_dt(row["expires_at"])
            user = con.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
            if not user or user["status"] != "active":
                return None
            user_exp = parse_dt(user["expires_at"])
            if user_exp and user_exp <= now_dt:
                return None
            if expires and expires <= now_dt:
                return None
            if last_seen and (now_dt - last_seen).total_seconds() > idle_seconds:
                con.execute("UPDATE sessions SET active=0 WHERE token_hash=?", (token_hash,))
                return None
            if row["device_hash"] != device_hash or row["tab_hash"] != tab_hash:
                return None
            if user["device_hash"] and user["device_hash"] != device_hash:
                return None
            if touch:
                now = iso(now_dt)
                con.execute("UPDATE sessions SET last_seen_at=? WHERE token_hash=?", (now, token_hash))
                con.execute("UPDATE users SET last_seen_at=? WHERE id=?", (now, user["id"]))
            return self._user_to_public(user)

    def logout(self, token: str) -> None:
        if not token:
            return
        with self._connect() as con:
            con.execute("UPDATE sessions SET active=0 WHERE token_hash=?", (sha256(token),))

    def add_chat(self, *, user_id: Optional[int], name: str, email: str, message: str, sender: str = "user") -> dict[str, Any]:
        message = (message or "").strip()
        if len(message) < 1:
            raise ValueError("Message is empty")
        now = iso()
        with self._connect() as con:
            con.execute(
                "INSERT INTO chats(user_id,name,email,sender,message,created_at,read_by_admin) VALUES(?,?,?,?,?,?,0)",
                (user_id, (name or "").strip(), normalize_email(email), sender, message[:2000], now),
            )
            row = con.execute("SELECT * FROM chats WHERE id=last_insert_rowid()").fetchone()
            return dict(row)

    def list_chats(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute("SELECT * FROM chats ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            con.execute("UPDATE chats SET read_by_admin=1 WHERE read_by_admin=0")
            return [dict(r) for r in rows]

    def list_users(self) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute("SELECT * FROM users ORDER BY id DESC").fetchall()
            out = []
            for r in rows:
                d = self._user_to_public(r)
                d["active_sessions"] = con.execute("SELECT COUNT(*) AS c FROM sessions WHERE user_id=? AND active=1", (r["id"],)).fetchone()["c"]
                out.append(d)
            return out

    def get_user(self, user_id: int) -> Optional[dict[str, Any]]:
        with self._connect() as con:
            r = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            return self._user_to_public(r) if r else None

    def approve_user(self, user_id: int, days: Optional[int] = None) -> dict[str, Any]:
        now_dt = utc_now()
        with self._connect() as con:
            user = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not user:
                raise ValueError("User not found")
            plan = user["plan"] or "basic"
            plan_days = days or PLAN_DEFS.get(plan, PLAN_DEFS["basic"])["days"]
            exp = iso(now_dt + timedelta(days=plan_days))
            con.execute(
                "UPDATE users SET status='active', approved_at=?, expires_at=?, rejected_at=NULL WHERE id=?",
                (iso(now_dt), exp, user_id),
            )
            r = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            return self._user_to_public(r)

    def reject_user(self, user_id: int) -> None:
        with self._connect() as con:
            con.execute("UPDATE users SET status='rejected', rejected_at=? WHERE id=?", (iso(), user_id))
            con.execute("UPDATE sessions SET active=0 WHERE user_id=?", (user_id,))

    def disable_user(self, user_id: int) -> None:
        with self._connect() as con:
            con.execute("UPDATE users SET status='disabled' WHERE id=?", (user_id,))
            con.execute("UPDATE sessions SET active=0 WHERE user_id=?", (user_id,))

    def reset_device(self, user_id: int) -> None:
        with self._connect() as con:
            con.execute("UPDATE users SET device_hash=NULL, device_label=NULL WHERE id=?", (user_id,))
            con.execute("UPDATE sessions SET active=0 WHERE user_id=?", (user_id,))

    def reset_session(self, user_id: int) -> None:
        with self._connect() as con:
            con.execute("UPDATE sessions SET active=0 WHERE user_id=?", (user_id,))

    def extend_user(self, user_id: int, days: int = 30) -> dict[str, Any]:
        with self._connect() as con:
            user = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not user:
                raise ValueError("User not found")
            base = parse_dt(user["expires_at"]) or utc_now()
            if base < utc_now():
                base = utc_now()
            exp = iso(base + timedelta(days=int(days)))
            con.execute("UPDATE users SET expires_at=?, status='active' WHERE id=?", (exp, user_id))
            r = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            return self._user_to_public(r)

    def set_admin_status(self, status: str) -> None:
        status = status if status in {"online", "away", "offline"} else "away"
        with self._connect() as con:
            con.execute("INSERT OR REPLACE INTO settings(key,value,updated_at) VALUES('admin_status',?,?)", (status, iso()))

    def admin_heartbeat(self) -> None:
        with self._connect() as con:
            con.execute("INSERT OR REPLACE INTO settings(key,value,updated_at) VALUES('admin_heartbeat',?,?)", (iso(), iso()))

    def get_admin_status(self) -> dict[str, Any]:
        with self._connect() as con:
            mode = con.execute("SELECT value, updated_at FROM settings WHERE key='admin_status'").fetchone()
            hb = con.execute("SELECT value FROM settings WHERE key='admin_heartbeat'").fetchone()
        manual = mode["value"] if mode else "away"
        hb_dt = parse_dt(hb["value"] if hb else None)
        auto_online = bool(hb_dt and (utc_now() - hb_dt).total_seconds() <= 150)
        effective = "online" if manual == "online" and auto_online else manual
        if manual == "online" and not auto_online:
            effective = "away"
        return {"manual": manual, "effective": effective, "auto_online": auto_online, "updated_at": mode["updated_at"] if mode else None}
