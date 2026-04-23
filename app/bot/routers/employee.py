import html as html_mod
import logging

from maxapi import F, Router
from maxapi.context import MemoryContext, State, StatesGroup
from maxapi.types import CallbackButton, MessageCallback, MessageCreated
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from app.bot.routers.start import BACK_MENU_KB, MENU_KB

logger = logging.getLogger("arkadyjarvismax")
router = Router()


class FindEmployee(StatesGroup):
    waiting_for_name = State()


@router.message_created(F.message.body.text, FindEmployee.waiting_for_name)
async def handle_employee_search(event: MessageCreated, context: MemoryContext, bitrix):
    msg = event.message
    query = (msg.body.text or "").strip()
    if not query:
        await msg.answer("Напиши имя или фамилию сотрудника:", attachments=BACK_MENU_KB())
        return

    try:
        users = await bitrix.search_users(query, limit=10)
    except Exception as e:
        logger.error("Bitrix search error: %s", e, exc_info=True)
        await msg.answer("❌ Ошибка поиска в Bitrix", attachments=MENU_KB())
        await context.clear()
        return

    if not users:
        await msg.answer(
            f"🔍 По запросу «{html_mod.escape(query)}» никого не найдено.\n\nПопробуй другое имя:",
            attachments=BACK_MENU_KB(),
        )
        return

    b = InlineKeyboardBuilder()
    for u in users:
        b.row(CallbackButton(text=u["name"], payload=f"emp:card:{u['id']}"))
    b.row(CallbackButton(text="◀️ Меню", payload="back:menu"))
    await msg.answer(
        f"🔍 Найдено по «{html_mod.escape(query)}»:",
        attachments=[b.as_markup()],
    )


@router.message_callback(F.callback.payload.startswith("emp:card:"))
async def handle_employee_card(event: MessageCallback, bitrix):
    user_id = int(event.callback.payload.split(":")[-1])
    await event.answer()

    try:
        card = await bitrix.get_employee_card(user_id)
    except Exception as e:
        logger.error("Bitrix employee card error: %s", e, exc_info=True)
        await event.message.answer("❌ Не удалось загрузить карточку", attachments=MENU_KB())
        return

    if not card:
        await event.message.answer("❌ Сотрудник не найден", attachments=MENU_KB())
        return

    lines = [f"👤 <b>{html_mod.escape(card['name'])}</b>"]
    if card.get("position"):
        lines.append(f"💼 {html_mod.escape(card['position'])}")
    if card.get("departments"):
        dept_str = ", ".join(html_mod.escape(d) for d in card["departments"])
        lines.append(f"🏢 {dept_str}")
    if card.get("telegram"):
        tg = card["telegram"].lstrip("@")
        lines.append(f"💬 @{html_mod.escape(tg)}")
    if card.get("email"):
        lines.append(f"📧 {html_mod.escape(card['email'])}")
    if card.get("phone"):
        phone_raw = card["phone"]
        lines.append(f"📱 {html_mod.escape(phone_raw)}")
    if card.get("supervisor"):
        sup = card["supervisor"]
        sup_text = html_mod.escape(sup["name"])
        if sup.get("position"):
            sup_text += f" ({html_mod.escape(sup['position'])})"
        lines.append(f"\n👆 <b>Руководитель:</b> {sup_text}")

    await event.message.answer("\n".join(lines), attachments=BACK_MENU_KB())
