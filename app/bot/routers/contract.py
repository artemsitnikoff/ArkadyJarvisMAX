import logging

from maxapi import F, Router
from maxapi.context import MemoryContext, State, StatesGroup
from maxapi.types import InputMediaBuffer, MessageCreated

from app.bot.attachments import download_attachment, first_file
from app.bot.routers.start import MENU_KB
from app.services.document_parser import UnsupportedDocumentError, extract_text
from app.services.prompts import load_prompt
from app.utils import md_to_telegram_html

logger = logging.getLogger("arkadyjarvismax")
router = Router()

MAX_DOC_CHARS = 120_000
TEXT_MSG_LIMIT = 4000


class ContractCheck(StatesGroup):
    waiting_for_document = State()


@router.message_created(ContractCheck.waiting_for_document)
async def handle_contract_document(event: MessageCreated, context: MemoryContext, ai_client):
    msg = event.message
    file_att = first_file(msg)
    if not file_att:
        await msg.reply("📄 Пришли файл договора (PDF, DOCX или TXT) как документ.")
        return

    filename = getattr(file_att, "filename", None) or "document"
    logger.info(
        "*** CONTRACT: file=%s size=%s from user=%s",
        filename, getattr(file_att, "size", None), msg.sender.user_id,
    )

    await context.clear()
    wait_msg = await msg.reply("📄 Читаю договор...")

    try:
        file_bytes = await download_attachment(file_att, max_bytes=50 * 1024 * 1024)
    except Exception as e:
        logger.error("*** ERROR downloading contract: %s", e, exc_info=True)
        await wait_msg.edit(text=f"❌ Не удалось скачать файл: {e}", attachments=MENU_KB())
        return

    try:
        text = extract_text(file_bytes, filename)
    except UnsupportedDocumentError as e:
        await wait_msg.edit(text=f"❌ {e}", attachments=MENU_KB())
        return
    except Exception as e:
        logger.error("*** ERROR parsing contract: %s", e, exc_info=True)
        await wait_msg.edit(text=f"❌ Не удалось прочитать файл: {e}", attachments=MENU_KB())
        return

    if not text.strip():
        await wait_msg.edit(
            text="❌ В файле не нашёл текста. Возможно, это скан без OCR.",
            attachments=MENU_KB(),
        )
        return

    if len(text) > MAX_DOC_CHARS:
        text = text[:MAX_DOC_CHARS]
        logger.warning("Contract truncated to %d chars", MAX_DOC_CHARS)

    await wait_msg.edit(text="🔍 Проверяю по правилам...")

    prompt_template = load_prompt("contract_check")
    full_prompt = f"{prompt_template}\n\n---\n\nТекст документа для проверки:\n\n{text}"

    try:
        answer = await ai_client.complete(full_prompt, timeout=300)
    except Exception as e:
        logger.error("*** ERROR checking contract: %s", e, exc_info=True)
        await wait_msg.edit(text=f"❌ Ошибка проверки: {e}", attachments=MENU_KB())
        return

    html_answer = md_to_telegram_html(answer)
    header = f"📄 <b>Проверка договора:</b> {filename}\n\n"
    body = header + html_answer
    if len(body) <= TEXT_MSG_LIMIT:
        await wait_msg.edit(text=body, attachments=MENU_KB())
        return

    # Long answer — send as .md attachment.
    preview = answer[:300].rstrip() + ("..." if len(answer) > 300 else "")
    buffer = InputMediaBuffer(buffer=answer.encode("utf-8"), filename="contract_check.md")
    await msg.answer(
        text=f"{header.strip()}\n\n{preview}",
        attachments=[buffer, *MENU_KB()],
    )
    await wait_msg.delete()
