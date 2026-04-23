import logging

from maxapi import F, Router
from maxapi.context import MemoryContext, State, StatesGroup
from maxapi.types import CallbackButton, InputMediaBuffer, MessageCreated
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from app.bot.attachments import download_attachment, first_file
from app.services.document_parser import UnsupportedDocumentError, extract_text
from app.services.prompts import load_prompt
from app.utils import md_to_telegram_html

logger = logging.getLogger("arkadyjarvismax")
router = Router()

MAX_DOC_CHARS = 120_000
TEXT_MSG_LIMIT = 4000


def exit_kb():
    b = InlineKeyboardBuilder()
    b.row(CallbackButton(text="◀️ Меню", payload="back:menu"))
    return [b.as_markup()]


class Cicero(StatesGroup):
    chatting = State()


@router.message_created(Cicero.chatting)
async def handle_cicero(event: MessageCreated, context: MemoryContext, ai_client):
    msg = event.message
    file_att = first_file(msg)

    if file_att:
        await _handle_document(msg, file_att, ai_client=ai_client)
    else:
        text = (msg.body.text or "").strip()
        if not text:
            await msg.reply("Задай вопрос текстом или приложи документ с вопросом в подписи.")
            return
        await _handle_text(msg, text, ai_client=ai_client)


async def _handle_document(msg, file_att, *, ai_client):
    filename = getattr(file_att, "filename", None) or "document"
    question = (msg.body.text or "").strip()

    logger.info(
        "*** CICERO DOC: file=%s question=%r from user=%s",
        filename, question, msg.sender.user_id,
    )

    wait_msg = await msg.reply("⚖️ Читаю документ...")

    try:
        file_bytes = await download_attachment(file_att, max_bytes=50 * 1024 * 1024)
    except Exception as e:
        logger.error("*** ERROR downloading cicero doc: %s", e, exc_info=True)
        await wait_msg.edit(text=f"❌ Не удалось скачать файл: {e}", attachments=exit_kb())
        return

    try:
        doc_text = extract_text(file_bytes, filename)
    except UnsupportedDocumentError as e:
        await wait_msg.edit(text=f"❌ {e}", attachments=exit_kb())
        return
    except Exception as e:
        logger.error("*** ERROR parsing cicero doc: %s", e, exc_info=True)
        await wait_msg.edit(text=f"❌ Не удалось прочитать файл: {e}", attachments=exit_kb())
        return

    if not doc_text.strip():
        await wait_msg.edit(
            text="❌ В файле не нашёл текста. Возможно, это скан без OCR.",
            attachments=exit_kb(),
        )
        return

    if len(doc_text) > MAX_DOC_CHARS:
        doc_text = doc_text[:MAX_DOC_CHARS]
        logger.warning("Cicero doc truncated to %d chars", MAX_DOC_CHARS)

    await wait_msg.edit(text="⚖️ Анализирую...")

    system = load_prompt("cicero")
    user_part = question or "Проанализируй приложенный документ: основные условия, риски, рекомендации."
    full_prompt = (
        f"{system}\n\n---\n\n"
        f"Текст приложенного документа ({filename}):\n\n{doc_text}\n\n---\n\n"
        f"Вопрос пользователя: {user_part}"
    )

    try:
        answer = await ai_client.complete(full_prompt, timeout=300)
    except Exception as e:
        logger.error("*** ERROR cicero doc: %s", e, exc_info=True)
        await wait_msg.edit(text=f"❌ Ошибка: {e}", attachments=exit_kb())
        return

    await _send_answer(msg, wait_msg, answer)


async def _handle_text(msg, question: str, *, ai_client):
    logger.info("*** CICERO TEXT: q=%r from user=%s", question, msg.sender.user_id)
    wait_msg = await msg.reply("⚖️ Думаю...")

    system = load_prompt("cicero")
    full_prompt = f"{system}\n\n---\n\nВопрос пользователя: {question}"

    try:
        answer = await ai_client.complete(full_prompt, timeout=300)
    except Exception as e:
        logger.error("*** ERROR cicero text: %s", e, exc_info=True)
        await wait_msg.edit(text=f"❌ Ошибка: {e}", attachments=exit_kb())
        return

    await _send_answer(msg, wait_msg, answer)


async def _send_answer(msg, wait_msg, answer: str):
    html_answer = md_to_telegram_html(answer)
    if len(html_answer) <= TEXT_MSG_LIMIT:
        await wait_msg.edit(text=html_answer, attachments=exit_kb())
        return

    preview = answer[:300].rstrip() + ("..." if len(answer) > 300 else "")
    buf = InputMediaBuffer(buffer=answer.encode("utf-8"), filename="cicero_answer.md")
    await msg.answer(
        text=f"⚖️ Ответ Цицерона (длинный, прикрепляю файлом):\n\n{preview}",
        attachments=[buf, *exit_kb()],
    )
    await wait_msg.delete()
