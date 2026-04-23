import logging
from datetime import datetime

from maxapi.types import MessageCallback

from app.bot.routers.start import MENU_KB
from app.utils import md_to_telegram_html

logger = logging.getLogger("arkadyjarvismax")

GREETING_PROMPT = (
    "Сгенерируй короткое (2-3 предложения) мотивирующее приветствие для сотрудника, "
    "который начинает рабочий день. Обращайся на 'ты'. "
    "Будь тёплым, искренним и вдохновляющим. Добавь один эмодзи в конце. "
    "Не используй клише. Каждый раз генерируй уникальное приветствие."
)


async def start_work_day(event: MessageCallback, bitrix, ai_client, db_user):
    """Handle work:office / work:remote callback."""
    payload = event.callback.payload
    remote = payload == "work:remote"

    if not db_user or not db_user.get("bitrix_user_id"):
        await event.message.answer("❌ Сначала авторизуйся: /start")
        await event.answer()
        return

    await event.answer()
    bitrix_id = db_user["bitrix_user_id"]

    status = await bitrix.get_work_status(bitrix_id)
    if status and status.get("status") == "OPENED":
        time_start = status.get("time_start", "")
        time_str = ""
        if time_start:
            try:
                if "T" in time_start:
                    dt = datetime.fromisoformat(time_start)
                else:
                    dt = datetime.strptime(time_start, "%d.%m.%Y %H:%M:%S")
                time_str = f" с {dt.strftime('%H:%M')}"
            except (ValueError, TypeError):
                pass
        await event.message.answer(f"✅ Вы уже работаете{time_str}")
        return

    result = await bitrix.start_work_day(bitrix_id)
    if not result.get("ok"):
        error = result.get("error", "Неизвестная ошибка")
        logger.error("Failed to start work day for user %s: %s", bitrix_id, error)
        await event.message.answer(f"❌ Не удалось начать день: {error}")
        return

    name = db_user.get("display_name") or "коллега"
    place = "удалённо" if remote else "в офисе"
    icon = "🏠" if remote else "🏢"

    try:
        greeting = await ai_client.complete(
            f"{GREETING_PROMPT}\nИмя сотрудника: {name}. Работает сегодня {place}."
        )
        html_greeting = md_to_telegram_html(greeting)
        await event.message.answer(
            f"{icon} Рабочий день начат\n\n{html_greeting}", attachments=MENU_KB(),
        )
    except Exception as e:
        logger.warning("Greeting generation failed: %s", e)
        await event.message.answer(
            f"{icon} Рабочий день начат ({place})", attachments=MENU_KB(),
        )
