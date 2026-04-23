import logging

from maxapi import Router
from maxapi.types import BotAdded, BotRemoved

from app import db
from app.bot.routers.start import MENU_KB

logger = logging.getLogger("arkadyjarvismax")
router = Router()


@router.bot_added()
async def on_bot_added(event: BotAdded):
    """Bot was added to a chat — track it for summarization."""
    chat_id = event.chat_id
    try:
        chat = await event.bot.get_chat_by_id(chat_id)
        title = getattr(chat, "title", None) or str(chat_id)
    except Exception as e:
        logger.warning("Could not fetch chat title for %s: %s", chat_id, e)
        title = str(chat_id)

    await db.upsert_group_chat(chat_id, title)
    logger.info("Bot added to chat: %s (%s)", title, chat_id)

    try:
        await event.bot.send_message(
            chat_id=chat_id,
            text=(
                "👋 Привет! Я ArkadyJarvis для MAX.\n\n"
                "Собираю переписку для суммаризации и отвечаю по команде. "
                "Напишите мне в ЛС /start для авторизации."
            ),
            attachments=MENU_KB(),
        )
    except Exception as e:
        logger.warning("Could not send welcome message: %s", e)


@router.bot_removed()
async def on_bot_removed(event: BotRemoved):
    """Bot was removed from a chat — forget it."""
    chat_id = event.chat_id
    await db.remove_group_chat(chat_id)
    logger.info("Bot removed from chat: %s", chat_id)
