"""maxapi 0.9.4 compatibility patches.

This module isolates every monkey-patch we apply to the maxapi library so:
1. Apps don't accidentally rely on patched behaviour without knowing.
2. A single `apply_patches()` call is the only side-effecting import.
3. Future maxapi upgrades are easier — diff lives here, version-guarded.

The three patches:

  * Dispatcher.call_handler — inject `data` into handlers by inspect.signature
    instead of __annotations__ (so un-annotated `bitrix`, `ai_client` etc. work).

  * SendedMessage.{edit,delete,reply,answer,forward,pin} — delegate to the
    inner Message so `wait = await msg.reply(...); await wait.edit(...)`
    works the same way it does in aiogram.

  * MessageCallback.answer — pure-ack mode when called without new_text/link
    (otherwise maxapi resends the original keyboard and clobbers any
    previous .edit() that changed the buttons).
"""

import inspect
import logging
from importlib.metadata import PackageNotFoundError, version as _pkg_version

from maxapi.dispatcher import Dispatcher as _Dispatcher
from maxapi.methods.types.sended_message import SendedMessage as _SendedMessage
from maxapi.types.updates.message_callback import MessageCallback as _MessageCallback

logger = logging.getLogger("arkadyjarvismax")

# The set of versions we've actually verified these patches against.
# When you bump maxapi, run the existing flows by hand AND update this set.
_TESTED_VERSIONS = {"0.9.4"}

_applied = False


def _check_version() -> None:
    try:
        installed = _pkg_version("maxapi")
    except PackageNotFoundError:
        logger.warning("maxapi package metadata not found — skipping version check")
        return
    if installed not in _TESTED_VERSIONS:
        logger.warning(
            "maxapi==%s is installed but compat patches were tested only on %s. "
            "Patches may apply to changed APIs and break in subtle ways. "
            "Verify start/menu/picker flows manually before relying on prod.",
            installed, sorted(_TESTED_VERSIONS),
        )


def _patch_dispatcher_call_handler() -> None:
    """Inject `data` into handlers by signature, not by type annotations."""
    async def _patched(self, handler, event_object, data):
        sig = inspect.signature(handler.func_event)
        param_names = set(sig.parameters.keys())
        kwargs = {k: v for k, v in data.items() if k in param_names}
        await handler.func_event(event_object, **kwargs)

    _Dispatcher.call_handler = _patched


def _patch_sended_message_methods() -> None:
    """Give SendedMessage the same edit/delete/reply/answer interface as Message."""
    def _delegate(method_name: str):
        async def _thunk(self, *args, **kwargs):
            return await getattr(self.message, method_name)(*args, **kwargs)
        _thunk.__name__ = method_name
        return _thunk

    for name in ("edit", "delete", "reply", "answer", "forward", "pin"):
        setattr(_SendedMessage, name, _delegate(name))


def _patch_message_callback_answer() -> None:
    """Pure-ack mode for MessageCallback.answer — don't reapply old keyboard
    when the caller only wants to acknowledge or show a toast."""
    original = _MessageCallback.answer

    async def _patched(self, notification=None, new_text=None, link=None,
                       notify=True, format=None):
        if new_text is None and link is None:
            if self.bot is None:
                raise RuntimeError("Bot не инициализирован")
            return await self.bot.send_callback(
                callback_id=self.callback.callback_id,
                message=None,
                notification=notification,
            )
        return await original(
            self, notification=notification, new_text=new_text,
            link=link, notify=notify, format=format,
        )

    _MessageCallback.answer = _patched


def apply_patches() -> None:
    """Apply all maxapi compat patches. Idempotent — safe to call twice."""
    global _applied
    if _applied:
        return
    _check_version()
    _patch_dispatcher_call_handler()
    _patch_sended_message_methods()
    _patch_message_callback_answer()
    _applied = True
    logger.info("maxapi 0.9.4 compat patches applied")
