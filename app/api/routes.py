import asyncio
import hmac
import json
import logging
import time
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app import db
from app.config import settings

# MAX Bot API caps at 30 requests/second. Throttle at 20/s for safety.
_BROADCAST_INTERVAL = 0.05

logger = logging.getLogger("arkadyjarvismax")
router = APIRouter()

TOKENS_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "bitrix_tokens.json"


@router.get("/health")
async def health():
    checks: dict = {}

    try:
        _db = db.get_db()
        async with _db.execute("SELECT 1") as cur:
            await cur.fetchone()
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = f"error: {e}"

    try:
        if TOKENS_FILE.exists():
            tokens = json.loads(TOKENS_FILE.read_text())
            expires_at = tokens.get("expires_at", 0)
            remaining = expires_at - int(time.time())
            checks["bitrix_token"] = "ok" if remaining > 60 else f"expires in {remaining}s"
        else:
            checks["bitrix_token"] = "no token file"
    except Exception as e:
        checks["bitrix_token"] = f"error: {e}"

    ok = all(v == "ok" for v in checks.values())
    return {"status": "ok" if ok else "degraded", "checks": checks}


class NotifyRequest(BaseModel):
    """Send a notification to a single MAX user by Bitrix24 ID."""

    bitrix_user_id: int = Field(..., description="Bitrix24 user ID", examples=[42])
    text: str = Field(
        ...,
        description="Message text (HTML: <b>, <i>, <a>)",
        examples=["✅ Ваш отпуск с 01.04 по 14.04 утверждён"],
    )


class NotifyResponse(BaseModel):
    ok: bool
    max_user_id: int | None = Field(None, description="MAX user ID (if found)")
    error: str | None = None


class BroadcastRequest(BaseModel):
    text: str = Field(
        ..., description="Message text (HTML: <b>, <i>, <a>)",
        examples=["📢 Завтра корпоратив в 18:00!"],
    )


class BroadcastResponse(BaseModel):
    ok: bool
    sent: int = 0
    failed: int = 0


def _check_token(token: str | None):
    if not settings.webhook_token:
        raise HTTPException(503, "WEBHOOK_TOKEN not configured on server")
    if not hmac.compare_digest(token or "", settings.webhook_token):
        raise HTTPException(403, "Invalid token")


@router.post(
    "/bitrix/notify",
    response_model=NotifyResponse,
    summary="Отправить уведомление",
    tags=["Bitrix24 Webhook"],
)
async def bitrix_notify(
    body: NotifyRequest,
    request: Request,
    x_webhook_token: str | None = Header(None),
):
    _check_token(x_webhook_token)

    bot = request.app.state.bot

    user = await db.get_user_by_bitrix_id(body.bitrix_user_id)
    if not user:
        return NotifyResponse(ok=False, error=f"User bitrix_id={body.bitrix_user_id} not found")

    max_user_id = user["max_user_id"]
    try:
        await bot.send_message(user_id=max_user_id, text=body.text)
        logger.info(
            "Webhook notify: bitrix=%s → max=%s, text=%r",
            body.bitrix_user_id, max_user_id, body.text[:80],
        )
        return NotifyResponse(ok=True, max_user_id=max_user_id)
    except Exception as e:
        logger.error("Webhook notify failed: %s", e)
        return NotifyResponse(ok=False, max_user_id=max_user_id, error=str(e))


@router.post(
    "/bitrix/broadcast",
    response_model=BroadcastResponse,
    summary="Рассылка всем",
    tags=["Bitrix24 Webhook"],
)
async def bitrix_broadcast(
    body: BroadcastRequest,
    request: Request,
    x_webhook_token: str | None = Header(None),
):
    _check_token(x_webhook_token)

    bot = request.app.state.bot
    users = await db.get_active_users()

    sent = 0
    failed = 0
    for user in users:
        max_user_id = user["max_user_id"]
        try:
            await bot.send_message(user_id=max_user_id, text=body.text)
            sent += 1
        except Exception as e:
            logger.warning("Broadcast failed for max=%s: %s", max_user_id, e)
            failed += 1
        await asyncio.sleep(_BROADCAST_INTERVAL)

    logger.info("Webhook broadcast: sent=%d, failed=%d", sent, failed)
    return BroadcastResponse(ok=True, sent=sent, failed=failed)
