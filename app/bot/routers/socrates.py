"""Socrates — meeting analyser."""

import asyncio
import logging
import re
import shutil
import tempfile
from pathlib import Path
from typing import NoReturn

from maxapi import F, Router
from maxapi.context import MemoryContext, State, StatesGroup
from maxapi.types import InputMediaBuffer, MessageCreated

from app.bot.routers.start import MENU_KB
from app.config import settings
from app.services.ffmpeg_tool import FFmpegError, convert_to_opus, probe_duration
from app.services.meeting_downloader import DownloadError, download_meeting
from app.services.meeting_pipeline import process_meeting

logger = logging.getLogger("arkadyjarvismax")
router = Router()

URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)

_USER_LOCKS: dict[int, asyncio.Lock] = {}
_LOCKS_GUARD = asyncio.Lock()


async def _get_user_lock(user_id: int) -> asyncio.Lock:
    async with _LOCKS_GUARD:
        lock = _USER_LOCKS.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            _USER_LOCKS[user_id] = lock
        return lock


class Socrates(StatesGroup):
    waiting_for_url = State()


class _StageAbort(Exception):
    pass


@router.message_created(F.message.body.text, Socrates.waiting_for_url)
async def handle_meeting_url(
    event: MessageCreated, context: MemoryContext, openrouter, ai_client,
):
    msg = event.message
    url = (msg.body.text or "").strip()
    if not URL_RE.match(url):
        await msg.reply(
            "Пришли ссылку на запись (http(s)://...). "
            "Yandex.Диск / Telemost или прямой URL."
        )
        return

    user_id = msg.sender.user_id
    user_lock = await _get_user_lock(user_id)
    if user_lock.locked():
        await context.clear()
        await msg.reply(
            "⏳ Уже обрабатываю твою предыдущую запись. Дождись окончания "
            "и пришли следующую ссылку.",
            attachments=MENU_KB(),
        )
        return

    await context.clear()
    logger.info("*** SOCRATES: url=%s from user=%s", url[:120], user_id)

    async with user_lock:
        wait_msg = await msg.reply("📥 Скачиваю запись...")
        tmpdir = Path(tempfile.mkdtemp(prefix="socrates_"))
        raw_path = tmpdir / "source.bin"
        ogg_path = tmpdir / "audio.ogg"

        try:
            size_mb = await _download_stage(url, raw_path, wait_msg)
            duration_sec = await _convert_and_probe_stage(
                raw_path, ogg_path, size_mb, wait_msg,
            )
            artifacts = await _run_pipeline_stage(
                ogg_path, duration_sec, url, openrouter, ai_client, wait_msg,
            )
            await _deliver_artifacts(msg, wait_msg, artifacts)
        except _StageAbort:
            return
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


async def _download_stage(url: str, raw_path: Path, wait_msg) -> float:
    try:
        size_bytes = await download_meeting(url, raw_path)
    except DownloadError as e:
        await _abort(wait_msg, f"❌ Не смог скачать: {e}")
    size_mb = size_bytes / (1024 * 1024)
    await _safe_edit(wait_msg, f"🎚 Скачано {size_mb:.1f} МБ. Проверяю длительность...")
    return size_mb


async def _convert_and_probe_stage(raw_path, ogg_path, size_mb, wait_msg):
    try:
        duration_sec = await probe_duration(raw_path)
    except FFmpegError as e:
        logger.warning("probe_duration on raw file failed: %s — will retry on ogg", e)
        duration_sec = 0.0

    if duration_sec > 0:
        await _reject_if_too_long(duration_sec, wait_msg)

    if duration_sec > 0:
        duration_min = duration_sec / 60
        await _safe_edit(
            wait_msg,
            f"🎚 Скачано {size_mb:.1f} МБ, {duration_min:.1f} мин. "
            "Конвертирую аудио (ffmpeg)...",
        )
    else:
        await _safe_edit(
            wait_msg,
            f"🎚 Скачано {size_mb:.1f} МБ. Конвертирую аудио (ffmpeg)...",
        )

    try:
        await convert_to_opus(raw_path, ogg_path)
    except FFmpegError as e:
        logger.error("ffmpeg failed: %s", e)
        await _abort(wait_msg, f"❌ Не смог обработать аудио (ffmpeg): {e}")

    if duration_sec == 0.0:
        try:
            duration_sec = await probe_duration(ogg_path)
            await _reject_if_too_long(duration_sec, wait_msg)
        except FFmpegError:
            pass
    duration_min = (duration_sec or 0) / 60

    ogg_size_mb = ogg_path.stat().st_size / (1024 * 1024)
    await _safe_edit(
        wait_msg,
        f"🎙 Аудио {ogg_size_mb:.1f} МБ, длительность {duration_min:.1f} мин.\n"
        "Транскрибирую запись (диаризация)...",
    )
    return duration_sec


async def _run_pipeline_stage(ogg_path, duration_sec, url, openrouter, ai_client, wait_msg):
    async def on_progress(text: str):
        await _safe_edit(wait_msg, f"🧠 {text}")

    try:
        return await process_meeting(
            ogg_path,
            openrouter=openrouter,
            ai_client=ai_client,
            source_name=_source_name_from_url(url),
            duration_sec=duration_sec,
            on_progress=on_progress,
        )
    except Exception:
        logger.error("meeting pipeline failed", exc_info=True)
        await _abort(wait_msg, "❌ Пайплайн упал. Детали — в логах бота.")


async def _deliver_artifacts(msg, wait_msg, artifacts) -> None:
    await _safe_edit(wait_msg, "📎 Готово, отправляю артефакты...")

    for name, body in [
        ("1_transcript.md", artifacts.transcript_md),
        ("2_review.md", artifacts.review_md),
        ("3_brief.md", artifacts.brief_md),
    ]:
        buf = InputMediaBuffer(buffer=body.encode("utf-8"), filename=name)
        await msg.answer(attachments=[buf])

    await msg.answer(
        "✅ Готово — транскрипт, ревью и заготовка для аналитика отправлены файлами выше.",
        attachments=MENU_KB(),
    )
    try:
        await wait_msg.delete()
    except Exception as e:
        logger.debug("socrates final wait_msg.delete suppressed: %s", e)


async def _reject_if_too_long(duration_sec: float, wait_msg) -> None:
    duration_min = duration_sec / 60
    if duration_min > settings.meeting_max_minutes:
        await _abort(
            wait_msg,
            f"❌ Запись длиннее {settings.meeting_max_minutes} мин "
            f"({duration_min:.1f} мин). В текущей итерации такие длинные "
            f"встречи не обрабатываются.",
        )


async def _safe_edit(wait_msg, text: str) -> None:
    try:
        await wait_msg.edit(text=text)
    except Exception as e:
        logger.debug("socrates edit suppressed: %s", e)


async def _abort(wait_msg, user_message: str) -> NoReturn:
    try:
        await wait_msg.edit(text=user_message, attachments=MENU_KB())
    except Exception as e:
        logger.debug("socrates abort edit suppressed: %s", e)
    raise _StageAbort


def _source_name_from_url(url: str) -> str:
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    if host in {"disk.yandex.ru", "disk.yandex.com", "yadi.sk"}:
        return "Yandex.Disk"
    tail = url.split("?", 1)[0].rstrip("/").split("/")[-1]
    return tail or "meeting"
