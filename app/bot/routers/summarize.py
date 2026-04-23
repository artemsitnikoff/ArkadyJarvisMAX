import logging

from maxapi import Router
from maxapi.types import Command, MessageCreated

from app.bot.routers.start import MENU_KB

logger = logging.getLogger("arkadyjarvismax")
router = Router()


@router.message_created(Command("summary"))
async def handle_summarize(event: MessageCreated, ai_client):
    msg = event.message
    chat_id = msg.recipient.chat_id
    logger.info("*** /summary in chat=%s from user=%s", chat_id, msg.sender.user_id)

    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.config import settings
    from app.summarizer import summarize_from_buffer

    tz = ZoneInfo(settings.timezone)
    start_of_day = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    summary = await summarize_from_buffer(chat_id, ai_client=ai_client, since=start_of_day)
    await msg.reply(f"📊 #summary\n\n{summary}", attachments=MENU_KB())
    logger.info("*** SENT summary reply to chat=%s", chat_id)
