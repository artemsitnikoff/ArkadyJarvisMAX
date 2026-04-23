"""Helpers for extracting and downloading MAX message attachments.

MAX exposes attachment content via direct HTTPS URLs on the payload
(`attachment.payload.url`). Unlike Telegram, there is no `bot.download()` —
we just fetch the URL ourselves.
"""
from __future__ import annotations

import logging
from typing import Iterable

import httpx
from maxapi.enums.attachment import AttachmentType
from maxapi.types.attachments.attachment import Attachment
from maxapi.types.attachments.audio import Audio
from maxapi.types.attachments.file import File as FileAttachment
from maxapi.types.attachments.image import Image
from maxapi.types.message import Message

logger = logging.getLogger("arkadyjarvismax")

_DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def _iter_attachments(msg: Message) -> Iterable[Attachment]:
    if not msg.body or not msg.body.attachments:
        return []
    return msg.body.attachments


def first_image(msg: Message) -> Image | None:
    for a in _iter_attachments(msg):
        if isinstance(a, Image) or getattr(a, "type", None) == AttachmentType.IMAGE:
            return a  # type: ignore[return-value]
    return None


def first_file(msg: Message) -> FileAttachment | None:
    for a in _iter_attachments(msg):
        if isinstance(a, FileAttachment) or getattr(a, "type", None) == AttachmentType.FILE:
            return a  # type: ignore[return-value]
    return None


def first_audio(msg: Message) -> Audio | None:
    for a in _iter_attachments(msg):
        if isinstance(a, Audio) or getattr(a, "type", None) == AttachmentType.AUDIO:
            return a  # type: ignore[return-value]
    return None


def attachment_url(attachment: Attachment) -> str | None:
    payload = getattr(attachment, "payload", None)
    return getattr(payload, "url", None) if payload else None


async def download_attachment(
    attachment: Attachment,
    *,
    max_bytes: int | None = None,
) -> bytes:
    """Fetch attachment bytes via its public URL."""
    url = attachment_url(attachment)
    if not url:
        raise ValueError(f"attachment of type {attachment.type} has no URL")

    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if max_bytes and total > max_bytes:
                    raise ValueError(f"attachment exceeds {max_bytes} bytes")
                chunks.append(chunk)
            return b"".join(chunks)


async def download_to_path(
    attachment: Attachment,
    path: str,
    *,
    max_bytes: int | None = None,
) -> None:
    url = attachment_url(attachment)
    if not url:
        raise ValueError(f"attachment of type {attachment.type} has no URL")
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            total = 0
            with open(path, "wb") as f:
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if max_bytes and total > max_bytes:
                        raise ValueError(f"attachment exceeds {max_bytes} bytes")
                    f.write(chunk)
