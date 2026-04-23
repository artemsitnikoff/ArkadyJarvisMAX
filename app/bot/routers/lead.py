import html as html_mod
import logging
import os
import tempfile

from maxapi import Router
from maxapi.context import MemoryContext, State, StatesGroup
from maxapi.types import MessageCreated

from app.bot.attachments import download_to_path, first_audio
from app.bot.routers.start import MENU_KB
from app.config import settings
from app.utils import parse_json_response

logger = logging.getLogger("arkadyjarvismax")
router = Router()


class CreateLead(StatesGroup):
    waiting_for_info = State()

EXTRACT_PROMPT = """\
Из текста ниже извлеки данные для создания CRM-лида. Верни JSON (только JSON, без markdown).
Поля:
- TITLE (строка, обязательно) — краткое название лида
- NAME (строка|null) — имя контакта
- LAST_NAME (строка|null) — фамилия контакта
- COMPANY_TITLE (строка|null) — название компании
- PHONE (строка|null) — телефон (любой формат)
- EMAIL (строка|null) — email
- COMMENTS (строка|null) — дополнительная информация

Если поле не найдено — null. TITLE обязателен, если нет явного названия — сформулируй из контекста.

Текст:
{text}
"""


@router.message_created(CreateLead.waiting_for_info)
async def handle_lead_fsm(
    event: MessageCreated, context: MemoryContext,
    ai_client, bitrix, openrouter, db_user=None,
):
    msg = event.message
    audio = first_audio(msg)

    if audio:
        await _handle_voice(msg, audio, context, ai_client=ai_client, bitrix=bitrix,
                            openrouter=openrouter, db_user=db_user)
        return

    text = (msg.body.text or "").strip()
    if not text:
        await msg.reply("Напиши данные лида текстом или запиши голосовое.")
        return
    await context.clear()
    await _create_lead(msg, text, ai_client=ai_client, bitrix=bitrix, db_user=db_user)


async def _handle_voice(msg, audio, context, *, ai_client, bitrix, openrouter, db_user):
    logger.info("*** LEAD VOICE from user=%s", msg.sender.user_id)
    wait = await msg.reply("🎤 Расшифровываю голосовое...")

    ogg_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            ogg_path = tmp.name
        await download_to_path(audio, ogg_path, max_bytes=50 * 1024 * 1024)
        result = await openrouter.transcribe_voice(ogg_path)
    finally:
        if ogg_path and os.path.exists(ogg_path):
            try:
                os.unlink(ogg_path)
            except Exception as e:
                logger.warning("Failed to delete temp ogg %s: %s", ogg_path, e)

    if not result.success:
        await wait.edit(
            text=f"❌ Не смог расшифровать голосовое: {result.error}\n\n"
                 "Попробуй ещё раз или напиши текстом.",
        )
        return

    await wait.edit(
        text=f"✅ Расшифровка (спикеров: {result.speakers_count}):\n\n"
             f"<code>{html_mod.escape(result.full_text)}</code>\n\n"
             "Создаю лид...",
    )
    await context.clear()
    await _create_lead(msg, result.full_text, ai_client=ai_client, bitrix=bitrix, db_user=db_user)


async def _create_lead(msg, text: str, *, ai_client, bitrix, db_user=None):
    raw = await ai_client.complete(EXTRACT_PROMPT.format(text=text))
    parsed = parse_json_response(raw)

    fields: dict = {"TITLE": parsed.get("TITLE") or text[:100]}

    for key in ("NAME", "LAST_NAME", "COMPANY_TITLE", "COMMENTS"):
        if parsed.get(key):
            fields[key] = parsed[key]

    if parsed.get("PHONE"):
        fields["PHONE"] = [{"VALUE": parsed["PHONE"], "VALUE_TYPE": "WORK"}]
    if parsed.get("EMAIL"):
        fields["EMAIL"] = [{"VALUE": parsed["EMAIL"], "VALUE_TYPE": "WORK"}]

    fields["SOURCE_ID"] = "OTHER"
    fields["SOURCE_DESCRIPTION"] = "MAX-бот ArkadyJarvisMAX"

    sender = msg.sender
    if sender:
        username = sender.username or ""
        creator_name = sender.full_name or ""
        source_parts = [f"Создал: {creator_name}"]
        if username:
            source_parts.append(f"@{username}")
        existing_comments = fields.get("COMMENTS", "")
        tg_info = " | ".join(source_parts)
        fields["COMMENTS"] = f"{existing_comments}\n\n[MAX] {tg_info}".strip()

    result = await bitrix.create_lead(fields)

    lead_id = result.get("id", "?")
    bitrix_url = f"https://{settings.bitrix_domain}/crm/lead/details/{lead_id}/"

    esc = html_mod.escape
    reply_parts = [f"✅ Лид создан (id: {esc(str(lead_id))})"]
    reply_parts.append(f"📋 {esc(fields['TITLE'])}")
    reply_parts.append(f"🔗 {bitrix_url}")
    name = " ".join(filter(None, [fields.get("NAME"), fields.get("LAST_NAME")]))
    if name:
        reply_parts.append(f"👤 {esc(name)}")
    if fields.get("COMPANY_TITLE"):
        reply_parts.append(f"🏢 {esc(fields['COMPANY_TITLE'])}")
    if parsed.get("PHONE"):
        reply_parts.append(f"📞 {esc(parsed['PHONE'])}")
    if parsed.get("EMAIL"):
        reply_parts.append(f"📧 {esc(parsed['EMAIL'])}")

    await msg.reply("\n".join(reply_parts), attachments=MENU_KB())
    logger.info("*** Lead created: id=%s fields=%s", lead_id, fields)
