import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from maxapi import F, Router
from maxapi.enums.chat_type import ChatType
from maxapi.types import MessageCreated

from app import db
from app.config import settings

logger = logging.getLogger("arkadyjarvismax")
router = Router()


@router.message_created(F.message.body.text)
async def buffer_message(event: MessageCreated):
    """Catch-all: buffer every text message from group chats into SQLite."""
    msg = event.message
    if msg.recipient.chat_type != ChatType.CHAT:
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
        text=msg.body.text,
        sent_at=sent_at,
    )
