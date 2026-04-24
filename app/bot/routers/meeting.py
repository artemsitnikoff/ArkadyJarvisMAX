import asyncio
import html as html_mod
import logging
from datetime import datetime

from maxapi import F, Router
from maxapi.context import MemoryContext, State, StatesGroup
from maxapi.types import MessageCallback, MessageCreated

from app import db
from app.bot.routers._attendee_picker import (
    ADD_ME_CB,
    DONE_CB,
    MORE_CB,
    PICK_CB_PREFIX,
    cancel_kb as _picker_cancel_kb,
    search_results_kb as _picker_results_kb,
    search_status_kb as _picker_status_kb,
)
from app.bot.routers.start import MENU_KB
from app.config import settings
from app.db import DbUser
from app.utils import parse_attendees, parse_meeting_time

logger = logging.getLogger("arkadyjarvismax")
router = Router()


class MeetingSetup(StatesGroup):
    waiting_for_command = State()
    searching_attendee = State()
    waiting_for_title = State()


_CANCEL_CB = "mtg:cancel"
_DONE_LABEL = "📅 Создать встречу"


def _cancel_kb(show_add_me: bool = True):
    return _picker_cancel_kb(_CANCEL_CB, show_add_me=show_add_me)


def _search_status_kb(attendee_names: list[str], show_add_me: bool = True):
    return _picker_status_kb(_CANCEL_CB, DONE_CB, _DONE_LABEL, show_add_me=show_add_me)


def _search_results_kb(users: list[dict]):
    return _picker_results_kb(users, _CANCEL_CB)


def build_meeting_reply(
    dt: datetime,
    event_id,
    bitrix_url: str,
    *,
    found_names: list[str] | None = None,
    external_emails: list[str] | None = None,
    invite_emails: list[str] | None = None,
    not_found: list[str] | None = None,
    context: str = "",
) -> str:
    esc = html_mod.escape
    text = f"✅ Встреча создана: {dt:%d.%m.%Y} в {dt:%H:%M} (id: {esc(str(event_id))})\n🔗 {bitrix_url}"
    if found_names:
        text += f"\n👥 Участники: {esc(', '.join(found_names))}"
    if external_emails:
        text += f"\n👥 По email: {esc(', '.join(external_emails))}"
    if invite_emails:
        text += f"\n📧 В описании (пригласить вручную): {esc(', '.join(invite_emails))}"
    if not_found:
        text += f"\n⚠️ Не найден: {esc(', '.join(not_found))}"
    if context:
        text += f"\n📝 {esc(context)}"
    return text


async def commit_meeting(
    bitrix,
    owner_user_id: int,
    dt: datetime,
    title: str,
    description: str,
    attendee_ids: list[int],
    duration_minutes: int | None = None,
) -> tuple[object, str]:
    kwargs = dict(
        title=title,
        date=dt,
        owner_user_id=owner_user_id,
        description=description,
        attendee_ids=attendee_ids if attendee_ids else None,
    )
    if duration_minutes is not None:
        kwargs["duration_minutes"] = duration_minutes
    result = await bitrix.create_meeting(**kwargs)
    event_id = result.get("id", "?")
    bitrix_url = (
        f"https://{settings.bitrix_domain}/company/personal/user/"
        f"{owner_user_id}/calendar/?EVENT_ID={event_id}"
    )
    return event_id, bitrix_url


async def _do_create_meeting(
    msg,
    db_user: DbUser,
    bitrix,
    dt: datetime,
    context: str,
    attendee_ids: list[int],
    attendee_names: list[str],
):
    title = context[:80] if context else "Встреча"
    event_id, bitrix_url = await commit_meeting(
        bitrix, db_user["bitrix_user_id"], dt,
        title=title,
        description=context or "",
        attendee_ids=attendee_ids,
    )
    reply_text = build_meeting_reply(
        dt, event_id, bitrix_url,
        found_names=attendee_names,
        context=context,
    )
    await msg.reply(reply_text, attachments=MENU_KB())


@router.message_created(F.message.body.text, MeetingSetup.waiting_for_command)
async def handle_meeting_fsm(
    event: MessageCreated, context: MemoryContext, db_user: DbUser, bitrix,
):
    msg = event.message
    text = (msg.body.text or "").strip()
    if not text:
        await msg.reply("Напиши время и участников, например:\n<code>14:00 @nick1 @nick2</code>")
        return

    fake_text = f"создай встречу {text}"
    dt, err = parse_meeting_time(fake_text)
    if err:
        await msg.reply(err)
        return

    await context.clear()

    ctx_text = ""
    try:
        linked = msg.link
        if linked and linked.message and linked.message.text:
            ctx_text = linked.message.text
    except Exception:
        pass

    nicknames, emails = parse_attendees(fake_text)

    if not nicknames and not emails:
        await context.update_data(
            dt=dt.isoformat(),
            context=ctx_text,
            attendee_ids=[],
            attendee_names=[],
        )
        await context.set_state(MeetingSetup.searching_attendee)
        await msg.reply(
            "Напиши имя или фамилию коллеги:",
            attachments=_cancel_kb(show_add_me=True),
        )
        return

    await _create_meeting_with_nicks(
        msg, db_user, bitrix, dt, ctx_text, nicknames, emails,
    )


async def _create_meeting_with_nicks(msg, db_user, bitrix, dt, ctx_text, nicknames, emails):
    attendee_ids: list[int] = []
    found_names: list[str] = []
    not_found: list[str] = []
    external_emails: list[str] = []

    nick_results = await asyncio.gather(
        *(bitrix.find_user_by_nickname(nick) for nick in nicknames)
    )
    for nick, (uid, full_name) in zip(nicknames, nick_results):
        if uid:
            attendee_ids.append(uid)
            found_names.append(full_name or nick)
        else:
            not_found.append(f"@{nick}")

    invite_emails: list[str] = []
    email_results = await asyncio.gather(
        *(bitrix.resolve_email_user(email) for email in emails),
        return_exceptions=True,
    )
    for email, result in zip(emails, email_results):
        if isinstance(result, Exception):
            logger.error("Failed to find user by email %s: %s", email, result)
            invite_emails.append(email)
        else:
            uid, name = result
            if uid:
                attendee_ids.append(uid)
                external_emails.append(f"{name} ({email})" if name else email)
            else:
                invite_emails.append(email)

    title = ctx_text[:80] if ctx_text else "Встреча"
    description = ctx_text or ""
    if invite_emails:
        description += "\n\nПригласить по email: " + ", ".join(invite_emails)

    event_id, bitrix_url = await commit_meeting(
        bitrix, db_user["bitrix_user_id"], dt,
        title=title,
        description=description,
        attendee_ids=attendee_ids,
    )

    reply_text = build_meeting_reply(
        dt, event_id, bitrix_url,
        found_names=found_names,
        external_emails=external_emails,
        invite_emails=invite_emails,
        not_found=not_found,
        context=ctx_text,
    )
    await msg.reply(reply_text, attachments=MENU_KB())


@router.message_callback(F.callback.payload == "mtg:cancel", MeetingSetup.searching_attendee)
async def handle_mtg_cancel(event: MessageCallback, context: MemoryContext):
    await context.clear()
    await event.message.edit(text="Создание встречи отменено.", attachments=MENU_KB())
    await event.answer()


@router.message_created(F.message.body.text, MeetingSetup.searching_attendee)
async def handle_mtg_search_input(
    event: MessageCreated, context: MemoryContext, db_user: DbUser, bitrix,
):
    msg = event.message
    query = (msg.body.text or "").strip()
    data = await context.get_data()
    attendee_ids: list[int] = data.get("attendee_ids", [])
    add_me = db_user["bitrix_user_id"] not in attendee_ids

    if not query:
        await msg.reply(
            "Напиши имя или фамилию коллеги:",
            attachments=_cancel_kb(show_add_me=add_me),
        )
        return

    users = await bitrix.search_users(query)
    if not users:
        await msg.reply(
            "Никого не нашёл, попробуй другое имя:",
            attachments=_cancel_kb(show_add_me=add_me),
        )
        return

    await msg.reply("Выбери коллегу:", attachments=_search_results_kb(users))


@router.message_callback(
    F.callback.payload.startswith(PICK_CB_PREFIX), MeetingSetup.searching_attendee,
)
async def handle_mtg_pick_user(event: MessageCallback, context: MemoryContext):
    parts = event.callback.payload.split(":", 2)
    if len(parts) < 3:
        await event.answer(notification="Ошибка данных кнопки")
        return

    bitrix_id = int(parts[1])
    name = parts[2]

    # Instant toast — user sees acknowledgement in <100ms, before the
    # message body edit (~300ms RTT) completes.
    import asyncio as _asyncio
    _asyncio.create_task(event.answer(notification=f"✓ {name}"))

    data = await context.get_data()
    attendee_ids: list[int] = data.get("attendee_ids", [])
    attendee_names: list[str] = data.get("attendee_names", [])

    if bitrix_id not in attendee_ids:
        attendee_ids.append(bitrix_id)
        attendee_names.append(name)
        await context.update_data(
            attendee_ids=attendee_ids, attendee_names=attendee_names,
        )

    db_user = await db.get_user(event.callback.user.user_id)
    add_me = db_user and db_user["bitrix_user_id"] not in attendee_ids
    selected = ", ".join(attendee_names)
    await event.message.edit(
        text=f"Выбраны: {selected}",
        attachments=_search_status_kb(attendee_names, show_add_me=add_me),
    )
    # No trailing event.answer() — the toast above already ack'd the callback.


@router.message_callback(F.callback.payload == ADD_ME_CB, MeetingSetup.searching_attendee)
async def handle_mtg_add_me(event: MessageCallback, context: MemoryContext):
    db_user = await db.get_user(event.callback.user.user_id)
    bitrix_id = db_user["bitrix_user_id"]
    name = db_user["display_name"]

    data = await context.get_data()
    attendee_ids: list[int] = data.get("attendee_ids", [])
    attendee_names: list[str] = data.get("attendee_names", [])

    if bitrix_id not in attendee_ids:
        attendee_ids.append(bitrix_id)
        attendee_names.append(name)
        await context.update_data(
            attendee_ids=attendee_ids, attendee_names=attendee_names,
        )

    selected = ", ".join(attendee_names)
    await event.message.edit(
        text=f"Выбраны: {selected}",
        attachments=_search_status_kb(attendee_names, show_add_me=False),
    )
    await event.answer()


@router.message_callback(F.callback.payload == MORE_CB, MeetingSetup.searching_attendee)
async def handle_mtg_add_more(event: MessageCallback, context: MemoryContext):
    data = await context.get_data()
    attendee_ids: list[int] = data.get("attendee_ids", [])
    db_user = await db.get_user(event.callback.user.user_id)
    add_me = db_user and db_user["bitrix_user_id"] not in attendee_ids
    await event.message.edit(
        text="Напиши имя или фамилию коллеги:",
        attachments=_cancel_kb(show_add_me=add_me),
    )
    await event.answer()


@router.message_callback(F.callback.payload == DONE_CB, MeetingSetup.searching_attendee)
async def handle_mtg_done(event: MessageCallback, context: MemoryContext):
    data = await context.get_data()
    attendee_ids: list[int] = data.get("attendee_ids", [])

    if not attendee_ids:
        await event.answer(notification="Сначала выбери хотя бы одного участника")
        return

    await context.set_state(MeetingSetup.waiting_for_title)
    await event.message.edit(text="Напиши тему встречи:")
    await event.answer()


@router.message_created(F.message.body.text, MeetingSetup.waiting_for_title)
async def handle_mtg_title_input(
    event: MessageCreated, context: MemoryContext, bitrix,
):
    msg = event.message
    title = (msg.body.text or "").strip()
    if not title:
        await msg.reply("Напиши тему встречи текстом:")
        return

    data = await context.get_data()
    dt = datetime.fromisoformat(data["dt"])
    ctx_text = data.get("context", "")
    attendee_ids: list[int] = data.get("attendee_ids", [])
    attendee_names: list[str] = data.get("attendee_names", [])

    ctx_text = f"{title}\n\n{ctx_text}" if ctx_text else title

    db_user = await db.get_user(msg.sender.user_id)
    await _do_create_meeting(msg, db_user, bitrix, dt, ctx_text, attendee_ids, attendee_names)
    await context.clear()
    logger.info("*** Meeting created via interactive search: %s attendees=%s", title, attendee_ids)
