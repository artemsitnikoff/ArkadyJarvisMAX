import base64
import io
import logging

from PIL import Image as PILImage
from maxapi import Router
from maxapi.context import MemoryContext, State, StatesGroup
from maxapi.types import InputMediaBuffer, MessageCreated

from app.bot.attachments import download_attachment, first_image
from app.bot.routers.start import MENU_KB

logger = logging.getLogger("arkadyjarvismax")
router = Router()

MAX_IMAGE_SIZE = 1024  # max dimension in pixels


class ImageGen(StatesGroup):
    waiting_for_prompt = State()


async def _download_photo_b64(msg) -> str | None:
    """Download the message's image attachment, resize, return base64 PNG."""
    img_att = first_image(msg)
    if not img_att:
        return None
    raw = await download_attachment(img_att, max_bytes=20 * 1024 * 1024)
    img = PILImage.open(io.BytesIO(raw))
    if max(img.size) > MAX_IMAGE_SIZE:
        img.thumbnail((MAX_IMAGE_SIZE, MAX_IMAGE_SIZE))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


@router.message_created(ImageGen.waiting_for_prompt)
async def handle_image_fsm(event: MessageCreated, context: MemoryContext, openrouter):
    msg = event.message
    prompt = (msg.body.text or "").strip()
    image_b64 = None

    # If user attached a photo, use its caption as the prompt.
    if first_image(msg):
        try:
            image_b64 = await _download_photo_b64(msg)
        except Exception as e:
            logger.warning("Failed to download photo: %s", e)

    if not prompt:
        if image_b64:
            await msg.reply("Отправь фото с подписью — что сделать с картинкой.")
        else:
            await msg.reply("Напиши промпт текстом или отправь фото с подписью.")
        return

    await context.clear()
    await _generate_and_send(msg, prompt, openrouter=openrouter, image_b64=image_b64)


async def _generate_and_send(msg, prompt: str, *, openrouter, image_b64: str | None = None):
    logger.info(
        "*** IMAGE: prompt=%r has_photo=%s from user=%s",
        prompt, image_b64 is not None, msg.sender.user_id,
    )
    wait_msg = await msg.reply("🎨 Генерирую картинку...")
    try:
        image_bytes = await openrouter.generate_image(prompt, image_b64=image_b64)
        photo = InputMediaBuffer(buffer=image_bytes, filename="image.png")
        await msg.answer(attachments=[photo, *MENU_KB()])
        await wait_msg.delete()
    except Exception as e:
        logger.error("*** ERROR generating image: %s", e, exc_info=True)
        await wait_msg.edit(text=f"❌ Ошибка генерации: {e}")
