import logging

from maxapi import F, Router
from maxapi.context import MemoryContext, State, StatesGroup
from maxapi.types import MessageCreated

from app.bot.routers.start import MENU_KB
from app.utils import md_to_telegram_html

logger = logging.getLogger("arkadyjarvismax")
router = Router()


class AskAI(StatesGroup):
    waiting_for_question = State()


@router.message_created(F.message.body.text, AskAI.waiting_for_question)
async def handle_askai_fsm(event: MessageCreated, context: MemoryContext, ai_client):
    msg = event.message
    question = (msg.body.text or "").strip()
    if not question:
        await msg.reply("Напиши вопрос текстом.")
        return
    await context.clear()
    await _ask_and_reply(msg, question, ai_client=ai_client)


async def _ask_and_reply(msg, question: str, *, ai_client):
    logger.info("*** ASK_AI: question=%r from user=%s", question, msg.sender.user_id)
    wait_msg = await msg.reply("🧠 Думаю...")
    try:
        answer = await ai_client.complete(question)
        html_answer = md_to_telegram_html(answer)
        await wait_msg.edit(text=html_answer, attachments=MENU_KB())
    except Exception as e:
        logger.error("*** ERROR asking AI: %s", e, exc_info=True)
        await wait_msg.edit(text=f"❌ Ошибка: {e}")
