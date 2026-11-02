import json
import os
import secrets
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict

import asyncpg
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

ph = PasswordHasher()
DATABASE_URL = os.environ["DATABASE_URL"]

clients: Dict[str, WebSocket] = {}
RESET_TOKEN_TTL_MINUTES = 15


async def get_db() -> asyncpg.Connection:
    return await asyncpg.connect(DATABASE_URL)


@app.on_event("startup")
async def startup() -> None:
    attempts = 10
    for attempt in range(1, attempts + 1):
        try:
            conn = await asyncpg.connect(DATABASE_URL, timeout=10)
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    reset_token TEXT,
                    reset_expires TIMESTAMPTZ
                )
                """
            )
            await conn.close()
            print("Successfully connected to database and created tables.")
            return
        except (asyncpg.exceptions.ConnectionDoesNotExistError, ConnectionResetError, OSError) as e:
            print(f"Database connection attempt {attempt}/{attempts} failed: {e}")
            if attempt == attempts:
                raise e
            await asyncio.sleep(3)


class AuthPayload(BaseModel):
    username: str
    password: str


class ForgotPayload(BaseModel):
    username: str


class ResetPayload(BaseModel):
    username: str
    token: str
    new_password: str


@app.get("/")
async def root():
    return {"status": "ok", "service": "python-instant-messaging"}


@app.post("/register")
async def register(p: AuthPayload):
    conn = await get_db()
    exists = await conn.fetchval("SELECT 1 FROM users WHERE username=$1", p.username)
    if exists:
        await conn.close()
        return {"ok": False, "code": "username_taken"}
    await conn.execute(
        "INSERT INTO users (username, password_hash) VALUES ($1, $2)",
        p.username,
        ph.hash(p.password),
    )
    await conn.close()
    return {"ok": True, "code": "ok"}


@app.post("/login")
async def login(p: AuthPayload):
    conn = await get_db()
    row = await conn.fetchrow(
        "SELECT password_hash FROM users WHERE username=$1", p.username
    )
    await conn.close()
    if not row:
        return {"ok": False, "code": "invalid_credentials"}
    try:
        ph.verify(row["password_hash"], p.password)
    except (VerifyMismatchError, InvalidHashError):
        return {"ok": False, "code": "invalid_credentials"}
    return {"ok": True, "code": "ok"}


@app.post("/forgot-password")
async def forgot_password(p: ForgotPayload):
    conn = await get_db()
    row = await conn.fetchrow("SELECT 1 FROM users WHERE username=$1", p.username)
    if not row:
        await conn.close()
        return {"ok": True, "code": "ok"}
    token = secrets.token_hex(16)
    expires = datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_TTL_MINUTES)
    await conn.execute(
        "UPDATE users SET reset_token=$1, reset_expires=$2 WHERE username=$3",
        token,
        expires,
        p.username,
    )
    await conn.close()
    return {"ok": True, "code": "ok", "reset_token": token}


@app.post("/reset-password")
async def reset_password(p: ResetPayload):
    conn = await get_db()
    row = await conn.fetchrow(
        "SELECT reset_token, reset_expires FROM users WHERE username=$1", p.username
    )
    if not row or row["reset_token"] != p.token:
        await conn.close()
        return {"ok": False, "code": "invalid_token"}
    now = datetime.now(timezone.utc)
    if row["reset_expires"] and now > row["reset_expires"]:
        await conn.close()
        return {"ok": False, "code": "token_expired"}
    await conn.execute(
        "UPDATE users SET password_hash=$1, reset_token=NULL, reset_expires=NULL WHERE username=$2",
        ph.hash(p.new_password),
        p.username,
    )
    await conn.close()
    return {"ok": True, "code": "ok"}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    username = None
    try:
        raw = await ws.receive_text()
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await ws.send_text(json.dumps({"type": "error", "code": "bad_json"}))
            await ws.close()
            return

        if msg.get("type") != "login":
            await ws.send_text(json.dumps({"type": "error", "code": "login_required"}))
            await ws.close()
            return

        conn = await get_db()
        row = await conn.fetchrow(
            "SELECT password_hash FROM users WHERE username=$1", msg.get("user")
        )
        await conn.close()

        if not row:
            await ws.send_text(json.dumps({"type": "error", "code": "invalid_credentials"}))
            await ws.close()
            return

        try:
            ph.verify(row["password_hash"], msg.get("pass", ""))
        except (VerifyMismatchError, InvalidHashError):
            await ws.send_text(json.dumps({"type": "error", "code": "invalid_credentials"}))
            await ws.close()
            return

        username = msg["user"]
        if username in clients:
            await ws.send_text(json.dumps({"type": "error", "code": "already_online"}))
            await ws.close()
            return

        clients[username] = ws
        await ws.send_text(json.dumps({"type": "welcome", "user": username}))

        for other in clients:
            if other != username:
                try:
                    await clients[other].send_text(
                        json.dumps({"type": "presence", "user": username, "status": "online"})
                    )
                except Exception:
                    pass

        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            await handle_client_message(username, msg)

    except WebSocketDisconnect:
        pass
    finally:
        if username:
            clients.pop(username, None)
            for other in clients:
                try:
                    await clients[other].send_text(
                        json.dumps({"type": "presence", "user": username, "status": "offline"})
                    )
                except Exception:
                    pass


async def handle_client_message(username: str, msg: dict) -> None:
    mtype = msg.get("type")

    if mtype == "msg":
        target = msg.get("to")
        if target in clients:
            await clients[target].send_text(
                json.dumps({
                    "type": "msg",
                    "from": username,
                    "id": msg.get("id"),
                    "body": msg.get("body", ""),
                    "ts": msg.get("ts"),
                })
            )

    elif mtype in ("webrtc_offer", "webrtc_answer", "webrtc_ice"):
        target = msg.get("to")
        if target in clients:
            relay = dict(msg)
            relay["from"] = username
            await clients[target].send_text(json.dumps(relay))
