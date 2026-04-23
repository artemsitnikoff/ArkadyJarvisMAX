import html as html_mod
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from maxapi import F, Router
from maxapi.context import MemoryContext, State, StatesGroup
from maxapi.types import CallbackButton, MessageCallback, MessageCreated
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

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
from app.bot.routers.meeting import build_meeting_reply, commit_meeting
from app.bot.routers.start import MENU_KB
from app.config import settings
from app.db import DbUser
from app.utils import DAY_NAMES_RU, merge_intervals, parse_bitrix_dt

logger = logging.getLogger("arkadyjarvismax")
router = Router()


class BookSlot(StatesGroup):
    searching_attendee = State()
    waiting_for_title = State()
    waiting_for_slot = State()
    waiting_for_topic = State()


_CANCEL_CB = "book:cancel"
_DONE_LABEL = "🔍 Искать слоты"


def _cancel_kb(show_add_me: bool = True):
    return _picker_cancel_kb(_CANCEL_CB, show_add_me=show_add_me)


def _search_status_kb(attendee_names: list[str], show_add_me: bool = True):
    return _picker_status_kb(_CANCEL_CB, DONE_CB, _DONE_LABEL, show_add_me=show_add_me)


def _search_results_kb(users: list[dict]):
    return _picker_results_kb(users, _CANCEL_CB)


def split_into_hourly_chunks(
    free_slots: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    chunks: list[tuple[datetime, datetime]] = []
    for start, end in free_slots:
        cursor = start
        while cursor < end:
            next_hour = cursor + timedelta(hours=1)
            chunk_end = min(next_hour, end)
            if (chunk_end - cursor) >= timedelta(minutes=30):
                chunks.append((cursor, chunk_end))
            cursor = next_hour
    return chunks


def build_slot_keyboard(
    days_with_chunks: list[tuple[date, list[tuple[datetime, datetime]]]],
):
    b = InlineKeyboardBuilder()
    for day, chunks in days_with_chunks:
        if not chunks:
            continue
        day_label = f"📅 {DAY_NAMES_RU[day.weekday()]}, {day.strftime('%d.%m')}"
        b.row(CallbackButton(text=day_label, payload=f"day:{day.strftime('%d%m')}"))
        row: list[CallbackButton] = []
        for s, e in chunks:
            label = f"{s.strftime('%H:%M')}–{e.strftime('%H:%M')}"
            cb = f"book:{day.strftime('%d%m')}:{s.strftime('%H%M')}:{e.strftime('%H%M')}"
            row.append(CallbackButton(text=label, payload=cb))
            if len(row) == 4:
                b.row(*row)
                row = []
        if row:
            b.row(*row)
    b.row(CallbackButton(text="❌ Отмена", payload="book:cancel"))
    return [b.as_markup()]


def _compute_free_slots_for_day(
    day: date,
    user_ids: list[int],
    accessibility: dict,
) -> list[tuple[datetime, datetime]]:
    day_start = datetime.combine(day, datetime.min.time().replace(hour=9))
    day_end = datetime.combine(day, datetime.min.time().replace(hour=19))

    busy_intervals: list[tuple[datetime, datetime]] = []
    for uid in user_ids:
        slots = accessibility.get(str(uid), [])
        for slot in slots:
            acc = slot.get("ACCESSIBILITY", "busy")
            if acc in ("free",):
                continue
            try:
                dt_from = parse_bitrix_dt(slot["DATE_FROM"])
                dt_to = parse_bitrix_dt(slot["DATE_TO"])
                offset_from = int(slot.get("~USER_OFFSET_FROM", 0))
                offset_to = int(slot.get("~USER_OFFSET_TO", 0))
                dt_from -= timedelta(seconds=offset_from)
                dt_to -= timedelta(seconds=offset_to)
            except Exception as e:
                logger.warning("Skip slot parse error: %s | %s", e, slot)
                continue
            if dt_to <= day_start or dt_from >= day_end:
                continue
            busy_intervals.append((max(dt_from, day_start), min(dt_to, day_end)))

    merged = merge_intervals(busy_intervals)

    free_slots: list[tuple[datetime, datetime]] = []
    cursor = day_start
    for b_start, b_end in merged:
        if cursor < b_start:
            free_slots.append((cursor, b_start))
        cursor = max(cursor, b_end)
    if cursor < day_end:
        free_slots.append((cursor, day_end))

    return [(s, e) for s, e in free_slots if (e - s) >= timedelta(minutes=30)]


async def _find_and_show_slots(
    msg,
    context: MemoryContext,
    bitrix,
    user_ids: list[int],
    user_names: list[str],
    not_found: list[str] | None = None,
):
    not_found = not_found or []

    today = datetime.now(ZoneInfo(settings.timezone)).date()
    work_days: list[date] = []
    d = today
    while len(work_days) < 5:
        if d.weekday() < 5:
            work_days.append(d)
        d += timedelta(days=1)

    date_from = work_days[0].strftime("%Y-%m-%d")
    date_to = work_days[-1].strftime("%Y-%m-%d")

    accessibility = await bitrix.get_users_accessibility(user_ids, date_from, date_to)

    lines: list[str] = [f"📅 Свободные слоты для {', '.join(user_names)}:"]
    if not_found:
        lines.append(f"⚠️ Не найден: {', '.join(not_found)}")
    lines.append("")

    days_with_chunks: list[tuple[date, list[tuple[datetime, datetime]]]] = []
    for day in work_days:
        free_slots = _compute_free_slots_for_day(day, user_ids, accessibility)
        chunks = split_into_hourly_chunks(free_slots)
        days_with_chunks.append((day, chunks))

    has_any_chunks = any(chunks for _, chunks in days_with_chunks)
    if not has_any_chunks:
        lines.append("Свободных слотов не найдено")
        await msg.reply("\n".join(lines).rstrip(), attachments=MENU_KB())
    else:
        keyboard = build_slot_keyboard(days_with_chunks)
        header = "\n".join(lines).rstrip()
        await msg.reply(
            f"{header}\n\nНажми на слот — создам встречу:",
            attachments=keyboard,
        )
        await context.update_data(
            attendee_ids=user_ids,
            attendee_names=user_names,
            year=work_days[0].year,
        )
        await context.set_state(BookSlot.waiting_for_slot)

    logger.info("*** SENT free slots for %s", user_names)


@router.message_callback(F.callback.payload == "book:cancel")
async def handle_cancel_booking(event: MessageCallback, context: MemoryContext):
    current = await context.get_state()
    if current not in (BookSlot.searching_attendee, BookSlot.waiting_for_title, BookSlot.waiting_for_slot):
        return
    await context.clear()
    await event.message.edit(text="Поиск слотов отменён.", attachments=MENU_KB())
    await event.answer()


@router.message_created(F.message.body.text, BookSlot.searching_attendee)
async def handle_search_input(
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
    F.callback.payload.startswith(PICK_CB_PREFIX), BookSlot.searching_attendee,
)
async def handle_pick_user(event: MessageCallback, context: MemoryContext):
    parts = event.callback.payload.split(":", 2)
    if len(parts) < 3:
        await event.answer(notification="Ошибка данных кнопки")
        return

    bitrix_id = int(parts[1])
    name = parts[2]

    data = await context.get_data()
    attendee_ids: list[int] = data.get("attendee_ids", [])
    attendee_names: list[str] = data.get("attendee_names", [])

    if bitrix_id not in attendee_ids:
        attendee_ids.append(bitrix_id)
        attendee_names.append(name)
        await context.update_data(attendee_ids=attendee_ids, attendee_names=attendee_names)

    db_user = await db.get_user(event.callback.user.user_id)
    add_me = db_user and db_user["bitrix_user_id"] not in attendee_ids
    selected = ", ".join(attendee_names)
    await event.message.edit(
        text=f"Выбраны: {selected}",
        attachments=_search_status_kb(attendee_names, show_add_me=add_me),
    )
    await event.answer()


@router.message_callback(F.callback.payload == ADD_ME_CB, BookSlot.searching_attendee)
async def handle_add_me(event: MessageCallback, context: MemoryContext):
    db_user = await db.get_user(event.callback.user.user_id)
    bitrix_id = db_user["bitrix_user_id"]
    name = db_user["display_name"]

    data = await context.get_data()
    attendee_ids: list[int] = data.get("attendee_ids", [])
    attendee_names: list[str] = data.get("attendee_names", [])

    if bitrix_id not in attendee_ids:
        attendee_ids.append(bitrix_id)
        attendee_names.append(name)
        await context.update_data(attendee_ids=attendee_ids, attendee_names=attendee_names)

    selected = ", ".join(attendee_names)
    await event.message.edit(
        text=f"Выбраны: {selected}",
        attachments=_search_status_kb(attendee_names, show_add_me=False),
    )
    await event.answer()


@router.message_callback(F.callback.payload == MORE_CB, BookSlot.searching_attendee)
async def handle_add_more(event: MessageCallback, context: MemoryContext):
    data = await context.get_data()
    attendee_ids: list[int] = data.get("attendee_ids", [])
    db_user = await db.get_user(event.callback.user.user_id)
    add_me = db_user and db_user["bitrix_user_id"] not in attendee_ids
    await event.message.edit(
        text="Напиши имя или фамилию коллеги:",
        attachments=_cancel_kb(show_add_me=add_me),
    )
    await event.answer()


@router.message_callback(F.callback.payload == DONE_CB, BookSlot.searching_attendee)
async def handle_search_done(event: MessageCallback, context: MemoryContext):
    data = await context.get_data()
    attendee_ids: list[int] = data.get("attendee_ids", [])

    if not attendee_ids:
        await event.answer(notification="Сначала выбери хотя бы одного участника")
        return

    await context.set_state(BookSlot.waiting_for_title)
    await event.message.edit(text="Напиши тему встречи:")
    await event.answer()


@router.message_created(F.message.body.text, BookSlot.waiting_for_title)
async def handle_title_then_search(
    event: MessageCreated, context: MemoryContext, bitrix,
):
    msg = event.message
    title = (msg.body.text or "").strip()
    if not title:
        await msg.reply("Напиши тему встречи текстом:")
        return

    data = await context.get_data()
    attendee_ids: list[int] = data.get("attendee_ids", [])
    attendee_names: list[str] = data.get("attendee_names", [])

    await context.update_data(topic=title)
    await msg.reply(f"Ищу слоты для {', '.join(attendee_names)}...")

    await _find_and_show_slots(msg, context, bitrix, attendee_ids, attendee_names)


@router.message_callback(F.callback.payload.startswith("day:"))
async def handle_day_header(event: MessageCallback):
    await event.answer()


@router.message_callback(F.callback.payload.startswith("book:"), BookSlot.waiting_for_slot)
async def handle_slot_selected(event: MessageCallback, context: MemoryContext, bitrix):
    payload = event.callback.payload
    if payload == "book:cancel":
        return  # handled by cancel router

    parts = payload.split(":")
    if len(parts) != 4:
        await event.answer(notification="Ошибка данных кнопки")
        return

    _, day_str, start_str, end_str = parts
    data = await context.get_data()
    year = data.get("year", datetime.now(ZoneInfo(settings.timezone)).year)

    day = int(day_str[:2])
    month = int(day_str[2:])
    sh, sm = int(start_str[:2]), int(start_str[2:])
    eh, em = int(end_str[:2]), int(end_str[2:])

    slot_start = datetime(year, month, day, sh, sm)
    slot_end = datetime(year, month, day, eh, em)
    duration = int((slot_end - slot_start).total_seconds() // 60)

    label = f"{day_str[:2]}.{day_str[2:]} {start_str[:2]}:{start_str[2:]}–{end_str[:2]}:{end_str[2:]}"

    topic = data.get("topic")
    if topic:
        await event.message.edit(text=f"Создаю встречу «{topic}» на {label}...")
        await event.answer()

        db_user = await db.get_user(event.callback.user.user_id)
        attendee_ids = data["attendee_ids"]
        attendee_names = data.get("attendee_names", [])

        await _book_slot_meeting(
            msg=event.message,
            bitrix=bitrix,
            owner_user_id=db_user["bitrix_user_id"],
            slot_start=slot_start,
            duration=duration,
            topic=topic,
            label=label,
            attendee_ids=attendee_ids,
            attendee_names=attendee_names,
        )
        await context.clear()
        return

    await context.update_data(
        slot_start=slot_start.isoformat(),
        slot_duration=duration,
        slot_label=label,
    )
    await context.set_state(BookSlot.waiting_for_topic)
    await event.message.edit(text=f"Выбрано: {label}. Напиши тему встречи:")
    await event.answer()


@router.message_created(F.message.body.text, BookSlot.waiting_for_topic)
async def handle_topic_input(
    event: MessageCreated, context: MemoryContext, db_user: DbUser, bitrix,
):
    msg = event.message
    topic = (msg.body.text or "").strip()
    if not topic:
        await msg.reply("Напиши тему встречи текстом")
        return

    data = await context.get_data()
    slot_start = datetime.fromisoformat(data["slot_start"])
    duration = data["slot_duration"]
    attendee_ids = data["attendee_ids"]
    attendee_names = data.get("attendee_names", [])
    label = data.get("slot_label", "")

    await _book_slot_meeting(
        msg=msg,
        bitrix=bitrix,
        owner_user_id=db_user["bitrix_user_id"],
        slot_start=slot_start,
        duration=duration,
        topic=topic,
        label=label,
        attendee_ids=attendee_ids,
        attendee_names=attendee_names,
    )
    await context.clear()


async def _book_slot_meeting(
    *,
    msg,
    bitrix,
    owner_user_id: int,
    slot_start: datetime,
    duration: int,
    topic: str,
    label: str,
    attendee_ids: list[int],
    attendee_names: list[str],
) -> None:
    event_id, bitrix_url = await commit_meeting(
        bitrix,
        owner_user_id=owner_user_id,
        dt=slot_start,
        title=topic,
        description=topic,
        attendee_ids=attendee_ids,
        duration_minutes=duration,
    )
    reply = build_meeting_reply(
        slot_start, event_id, bitrix_url,
        found_names=attendee_names,
    )
    esc = html_mod.escape
    reply = f"🗓 Слот: {esc(label)}\n" + reply
    await msg.reply(reply, attachments=MENU_KB())
    logger.info(
        "*** Meeting booked from free slots: %s %s attendees=%s",
        topic, label, attendee_ids,
    )
