import logging
from typing import Any, Awaitable, Callable, Dict

from maxapi.filters.middleware import BaseMiddleware
from maxapi.types import MessageCallback, MessageCreated, UpdateUnion
from maxapi.enums.chat_type import ChatType

from app import db

logger = logging.getLogger("arkadyjarvismax")

# Slash commands that don't require auth.
PUBLIC_COMMANDS = {"/start", "/help"}
# /summary is the only text command that needs auth.
AUTH_COMMANDS = {"/summary"}


def _first_word(text: str | None) -> str:
    if not text:
        return ""
    return text.split()[0].split("@")[0]


def _extract_chat(event: UpdateUnion) -> tuple[int | None, str]:
    """Return (chat_id, chat_type_str). For MessageCreated/MessageCallback with a
    Message object the recipient tells us the chat. For lifecycle events it
    comes from `chat_id` + `is_channel` directly."""
    msg = getattr(event, "message", None)
    if msg is not None and getattr(msg, "recipient", None):
        r = msg.recipient
        return r.chat_id, (r.chat_type.value if r.chat_type else "")
    chat_id = getattr(event, "chat_id", None)
    return chat_id, ""


class ErrorMiddleware(BaseMiddleware):
    """Catch unhandled exceptions in handlers, log them, reply with a
    generic error to the user (for messages and callbacks)."""

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event_object: UpdateUnion,
        data: Dict[str, Any],
    ) -> Any:
        try:
            return await handler(event_object, data)
        except Exception:
            chat_id, _ = _extract_chat(event_object)
            user_id = None
            if isinstance(event_object, MessageCreated):
                user_id = event_object.message.sender.user_id
            elif isinstance(event_object, MessageCallback):
                user_id = event_object.callback.user.user_id

            logger.error(
                "Unhandled error in chat=%s user=%s", chat_id, user_id, exc_info=True,
            )

            try:
                if isinstance(event_object, MessageCreated):
                    await event_object.message.answer(
                        "❌ Произошла ошибка. Попробуй ещё раз."
                    )
                elif isinstance(event_object, MessageCallback):
                    await event_object.answer(
                        notification="❌ Произошла ошибка",
                    )
            except Exception:
                pass
            return None


class AuthMiddleware(BaseMiddleware):
    """Inject `db_user` into handler kwargs and gate auth-required commands.

    MessageCreated:
      - /start and /help pass through without auth;
      - in muted CHAT-type chats the bot stays silent for any slash command
        (replies once with a short mute notice);
      - /summary requires an authorized user.

    MessageCallback:
      - always get `db_user` injected; handlers enforce their own access rules.
    """

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event_object: UpdateUnion,
        data: Dict[str, Any],
    ) -> Any:
        # Callback queries — inject db_user and hand over.
        if isinstance(event_object, MessageCallback):
            uid = event_object.callback.user.user_id
            data["db_user"] = await db.get_user(uid)
            return await handler(event_object, data)

        if not isinstance(event_object, MessageCreated):
            return await handler(event_object, data)

        msg = event_object.message
        text = msg.body.text if msg.body else None
        first = _first_word(text)
        chat_type = msg.recipient.chat_type if msg.recipient else None

        # Public slash commands — always allow.
        if first in PUBLIC_COMMANDS:
            return await handler(event_object, data)

        # Muted groups — bot collects messages but refuses slash commands.
        if chat_type == ChatType.CHAT and msg.recipient.chat_id is not None:
            if await db.is_group_muted(msg.recipient.chat_id):
                if first.startswith("/"):
                    await msg.reply(
                        "Мне запретили отвечать в этой группе. "
                        "Группа внесена в список исключений."
                    )
                    return None
                return await handler(event_object, data)

        # Load user if exists.
        user = await db.get_user(msg.sender.user_id) if msg.sender else None
        data["db_user"] = user

        if first in AUTH_COMMANDS and (not user or not user.get("bitrix_user_id")):
            if chat_type == ChatType.CHAT:
                await msg.reply("Сначала авторизуйся: напиши мне /start в личку")
            else:
                await msg.answer("Сначала авторизуйся через /start")
            return None

        return await handler(event_object, data)
