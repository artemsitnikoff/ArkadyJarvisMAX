import asyncio
import html as html_mod
import logging

from maxapi import F, Router
from maxapi.context import MemoryContext, State, StatesGroup
from maxapi.types import CallbackButton, MessageCallback, MessageCreated
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from app.bot.routers.start import MENU_KB
from app.config import settings

logger = logging.getLogger("arkadyjarvismax")
router = Router()


def _parse_allowed_ids(csv: str) -> set[int]:
    if not csv.strip():
        return set()
    return {int(x.strip()) for x in csv.split(",") if x.strip().isdigit()}


GLAFIRA_ALLOWED = _parse_allowed_ids(settings.glafira_allowed)

MAX_HISTORY = 20


def glafira_exit_kb():
    b = InlineKeyboardBuilder()
    b.row(CallbackButton(text="◀️ Меню", payload="glafira:exit"))
    return b.as_markup()


class Glafira(StatesGroup):
    chatting = State()


@router.message_callback(F.callback.payload == "glafira:exit")
async def handle_glafira_exit(event: MessageCallback, context: MemoryContext):
    await context.clear()
    await event.message.answer(
        "Выбери команду — покажу подсказку:",
        attachments=MENU_KB(),
    )
    await event.answer()


@router.message_created(F.message.body.text, Glafira.chatting)
async def handle_glafira_message(
    event: MessageCreated, context: MemoryContext, openclaw,
):
    msg = event.message
    user_text = (msg.body.text or "").strip()
    if not user_text:
        return

    data = await context.get_data()
    conv_messages: list[dict] = data.get("messages", [])
    conv_messages.append({"role": "user", "content": user_text})

    if len(conv_messages) > MAX_HISTORY:
        conv_messages = conv_messages[-MAX_HISTORY:]

    wait_msg = await msg.reply("🤖 Думаю...")

    try:
        full_text = ""
        last_edit_len = 0
        edit_interval = 0.8
        last_edit_time = 0.0

        async for chunk in openclaw.stream_chat(
            conv_messages, user_id=msg.sender.user_id,
        ):
            full_text += chunk

            now = asyncio.get_event_loop().time()
            if (now - last_edit_time >= edit_interval
                    and len(full_text) - last_edit_len >= 20):
                try:
                    display = html_mod.escape(full_text[:4000])
                    await wait_msg.edit(
                        text=f"🤖 {display}",
                        attachments=[glafira_exit_kb()],
                    )
                    last_edit_len = len(full_text)
                    last_edit_time = now
                except Exception as e:
                    if "not modified" not in str(e):
                        logger.warning("Glafira stream edit failed: %s", e)

        if full_text.strip():
            display = html_mod.escape(full_text.strip()[:4000])
            final_msg = f"🤖 {display}"
            try:
                await wait_msg.edit(
                    text=final_msg, attachments=[glafira_exit_kb()],
                )
            except Exception as e:
                if "not modified" not in str(e):
                    logger.warning("Glafira final edit failed: %s", e)
        else:
            await wait_msg.edit(
                text="🤖 Глафира не ответила. Попробуй переформулировать.",
                attachments=[glafira_exit_kb()],
            )

        conv_messages.append({"role": "assistant", "content": full_text})
        await context.update_data(messages=conv_messages)

    except Exception as e:
        logger.error("Glafira error: %s", e, exc_info=True)
        await wait_msg.edit(
            text=f"❌ Ошибка связи с Глафирой: {html_mod.escape(str(e))}",
            attachments=[glafira_exit_kb()],
        )
