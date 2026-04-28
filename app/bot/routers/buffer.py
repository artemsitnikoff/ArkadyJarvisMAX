import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from maxapi import F, Router
from maxapi.enums.chat_type import ChatType
from maxapi.types import MessageCallback, MessageCreated

from app import db
from app.config import settings

logger = logging.getLogger("arkadyjarvismax")
router = Router()


# Stale-callback catch-all. Registered on the LAST router so it fires only
# when no state-filtered picker handler matched (typical after a container
# restart wipes in-memory FSM state, or when the user clicks a button from
# an old message after completing a different flow).
_STALE_CALLBACK_PREFIXES = ("pick:", "book:", "mtg:", "search:", "day:", "recruit:")


@router.message_callback()
async def handle_stale_callback(event: MessageCallback):
    payload = event.callback.payload or ""
    if not any(payload.startswith(p) for p in _STALE_CALLBACK_PREFIXES):
        return
    logger.info("Stale callback from user=%s: %r", event.callback.user.user_id, payload)
    await event.answer(
        notification="Кнопки устарели — нажми ◀️ Меню и начни заново",
    )


@router.message_created(F.message.body.text)
async def buffer_message(event: MessageCreated):
    """Catch-all: buffer every text message from group chats into SQLite."""
    msg = event.message
    if msg.recipient.chat_type != ChatType.CHAT:
        return

    # Belt-and-braces against a NULL text slipping past the F.message.body.text
    # filter (sticker/poll/voice race) — message_buffer.text is NOT NULL and
    # would otherwise crash the INSERT.
    text = msg.body.text or ""
    if not text:
        return

    sender_name = ""
    if msg.sender:
        parts = [msg.sender.first_name or ""]
        if msg.sender.last_name:
            parts.append(msg.sender.last_name)
        sender_name = " ".join(parts).strip()

    tz = ZoneInfo(settings.timezone)
    # MAX sends timestamp as unix-ms; convert to local tz for buffer storage.
    try:
        dt_utc = datetime.fromtimestamp(msg.timestamp / 1000, tz=timezone.utc)
        sent_at = dt_utc.astimezone(tz)
    except Exception:
        sent_at = datetime.now(tz)

    await db.buffer_message(
        chat_id=msg.recipient.chat_id,
        sender_id=msg.sender.user_id if msg.sender else 0,
        sender_name=sender_name,
        text=text,
        sent_at=sent_at,
    )
