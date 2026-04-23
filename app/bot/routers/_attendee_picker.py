"""Shared keyboards for the attendee-picker flow (meeting + free_slots)."""

from maxapi.types import CallbackButton
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

PICK_CB_PREFIX = "pick:"
ADD_ME_CB = "search:addme"
MORE_CB = "search:more"
DONE_CB = "search:done"


def cancel_kb(cancel_cb: str, *, show_add_me: bool = True):
    b = InlineKeyboardBuilder()
    if show_add_me:
        b.row(CallbackButton(text="+ Я", payload=ADD_ME_CB))
    b.row(CallbackButton(text="❌ Отмена", payload=cancel_cb))
    return [b.as_markup()]


def search_status_kb(
    cancel_cb: str,
    done_cb: str,
    done_label: str,
    *,
    show_add_me: bool = True,
):
    b = InlineKeyboardBuilder()
    first_row = []
    if show_add_me:
        first_row.append(CallbackButton(text="+ Я", payload=ADD_ME_CB))
    first_row.append(CallbackButton(text="+ Ещё участник", payload=MORE_CB))
    first_row.append(CallbackButton(text=done_label, payload=done_cb))
    b.row(*first_row)
    b.row(CallbackButton(text="❌ Отмена", payload=cancel_cb))
    return [b.as_markup()]


def search_results_kb(users: list[dict], cancel_cb: str):
    b = InlineKeyboardBuilder()
    for u in users:
        payload = f"{PICK_CB_PREFIX}{u['id']}:{u['name'][:40]}"
        b.row(CallbackButton(text=u["name"], payload=payload))
    b.row(CallbackButton(text="❌ Отмена", payload=cancel_cb))
    return [b.as_markup()]
