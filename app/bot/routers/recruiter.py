import html as html_mod
import logging
import re

from maxapi import F, Router
from maxapi.context import MemoryContext, State, StatesGroup
from maxapi.types import CallbackButton, MessageCallback
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from app.bot.routers.start import MENU_KB
from app.config import settings
from app.services.potok_client import score_label
from app.services.resume_scorer import extract_recruiter_instructions, score_applicant

logger = logging.getLogger("arkadyjarvismax")
router = Router()


def _parse_allowed_ids(csv: str) -> set[int]:
    if not csv.strip():
        return set()
    return {int(x.strip()) for x in csv.split(",") if x.strip().isdigit()}


RECRUITER_ALLOWED = _parse_allowed_ids(settings.recruiter_allowed)


class Recruiter(StatesGroup):
    choosing_job = State()
    confirming = State()
    scoring = State()


def _format_result_message(
    job_name: str, idx: int, total: int, result, applicant_name: str,
) -> str:
    label = score_label(result.score)
    name = html_mod.escape(applicant_name)
    jname = html_mod.escape(job_name)

    lines = [
        f"👔 <b>{jname}</b> [{idx}/{total}]",
        "",
        f"<b>{name}</b>",
        f"Балл: <b>{result.score}/100</b> ({label})",
        "",
        html_mod.escape(result.reasoning),
    ]

    if result.breakdown:
        lines.append("")
        lines.append("📊 <b>Разбивка по критериям:</b>")
        for b in result.breakdown:
            criterion = html_mod.escape(b.criterion)
            comment = html_mod.escape(b.comment) if b.comment else ""
            lines.append(f"  {criterion}: <b>{b.score}</b> — {comment}")

    if result.strengths:
        lines.append("")
        lines.append("✅ <b>Сильные стороны:</b>")
        for s in result.strengths:
            lines.append(f"  • {html_mod.escape(s)}")

    if result.weaknesses:
        lines.append("")
        lines.append("⚠️ <b>Слабые стороны:</b>")
        for w in result.weaknesses:
            lines.append(f"  • {html_mod.escape(w)}")

    return "\n".join(lines)


@router.message_callback(F.callback.payload == "recruit:stop")
async def handle_recruit_stop(event: MessageCallback, context: MemoryContext):
    await context.update_data(stop=True)
    await event.answer(notification="Останавливаю после текущего кандидата...")


@router.message_callback(F.callback.payload == "recruit:exit")
async def handle_recruit_exit(event: MessageCallback, context: MemoryContext):
    await context.clear()
    await event.message.answer(
        "Выбери команду — покажу подсказку:",
        attachments=MENU_KB(),
    )
    await event.answer()


@router.message_callback(F.callback.payload.startswith("recruit:job:"), Recruiter.choosing_job)
async def handle_job_selected(event: MessageCallback, context: MemoryContext, potok):
    job_id = int(event.callback.payload.split(":")[-1])
    await event.answer()

    progress_msg = await event.message.answer("👔 Загружаю вакансию...")

    try:
        job = await potok.get_job(job_id)
    except Exception as e:
        logger.error("Potok error loading job %s: %s", job_id, e, exc_info=True)
        await progress_msg.edit(
            text=f"❌ Ошибка загрузки из Potok: {html_mod.escape(str(e))}",
            attachments=MENU_KB(),
        )
        await context.clear()
        return

    raw_desc = job.description or ""
    clean_desc, recruiter_instructions = extract_recruiter_instructions(raw_desc)
    job_name = html_mod.escape(job.name)

    info_lines = [f"👔 <b>{job_name}</b>", ""]
    if clean_desc:
        info_lines.append(f"📋 <b>Описание:</b>\n{html_mod.escape(clean_desc[:1500])}")
        info_lines.append("")
    if recruiter_instructions:
        info_lines.append(f"🎯 <b>Важно для CLAUDE:</b>\n{html_mod.escape(recruiter_instructions[:1500])}")
        info_lines.append("")
    info_lines.append("⏳ Считаю кандидатов...")

    try:
        await progress_msg.edit(text="\n".join(info_lines))
    except Exception:
        pass

    try:
        all_applicants = await potok.get_applicants_for_job(
            job_id, limit=0, skip_scored=False,
        )
        new_applicants = [
            a for a in all_applicants
            if not re.match(r"^\d{3}-", a.last_name or "")
        ]
    except Exception as e:
        logger.error("Potok error loading applicants: %s", e, exc_info=True)
        info_lines[-1] = f"❌ Ошибка загрузки кандидатов: {html_mod.escape(str(e))}"
        try:
            await progress_msg.edit(text="\n".join(info_lines), attachments=MENU_KB())
        except Exception:
            pass
        await context.clear()
        return

    total_all = len(all_applicants)
    total_new = len(new_applicants)
    logger.info("Recruiter job %s: %d total, %d new", job_id, total_all, total_new)

    if total_all == 0:
        info_lines[-1] = "Нет кандидатов на эту вакансию."
        try:
            await progress_msg.edit(text="\n".join(info_lines), attachments=MENU_KB())
        except Exception:
            pass
        await context.clear()
        return

    info_lines.pop()

    b = InlineKeyboardBuilder()
    if total_new > 0:
        b.row(CallbackButton(
            text=f"✅ Оценить новых ({total_new})",
            payload=f"recruit:score:{job_id}",
        ))
    b.row(CallbackButton(
        text=f"🔄 Переоценить всех ({total_all})",
        payload=f"recruit:rescore:{job_id}",
    ))
    b.row(CallbackButton(text="◀️ Меню", payload="recruit:exit"))

    try:
        await progress_msg.edit(text="\n".join(info_lines), attachments=[b.as_markup()])
    except Exception:
        pass

    await context.set_state(Recruiter.confirming)
    await context.update_data(
        job_id=job_id,
        job=job,
        all_applicants=all_applicants,
        new_applicants=new_applicants,
    )


def _stop_kb():
    b = InlineKeyboardBuilder()
    b.row(CallbackButton(text="⏹ Остановить", payload="recruit:stop"))
    return [b.as_markup()]


async def _run_scoring(event, context, potok, ai_client, job, applicants):
    await context.set_state(Recruiter.scoring)

    job_id = job.id
    total = len(applicants)
    job_name = job.name
    scored = 0
    errors = 0

    for i, applicant in enumerate(applicants, 1):
        data = await context.get_data()
        if data.get("stop"):
            break

        name = applicant.display_name

        thinking_msg = await event.message.answer(
            f"👔 <b>{html_mod.escape(job_name)}</b> [{i}/{total}]\n\n"
            f"⏳ {html_mod.escape(name)}...",
            attachments=_stop_kb(),
        )

        try:
            result = await score_applicant(job, applicant, ai_client=ai_client)

            text = _format_result_message(job_name, i, total, result, name)
            if len(text) > 4096:
                text = text[:4090] + "\n…"
            try:
                await thinking_msg.edit(text=text)
            except Exception:
                await thinking_msg.delete()
                await event.message.answer(text)

            scored += 1

            try:
                await potok.push_scoring(
                    result, job_id,
                    original_last_name=applicant.last_name or "",
                )
            except Exception as e:
                logger.error("Potok push error for %s: %s", applicant.id, e)

        except Exception as e:
            logger.error("Scoring error for %s: %s", name, e, exc_info=True)
            try:
                await thinking_msg.edit(
                    text=f"👔 <b>{html_mod.escape(job_name)}</b> [{i}/{total}]\n\n"
                         f"❌ {html_mod.escape(name)} — ошибка: {html_mod.escape(str(e)[:200])}"
                )
            except Exception:
                pass
            errors += 1

    data = await context.get_data()
    stopped = data.get("stop", False)
    status = "остановлено" if stopped else "готово"
    summary = (
        f"👔 <b>{html_mod.escape(job_name)}</b> — {status}!\n\n"
        f"Оценено: {scored}/{total} | Ошибок: {errors}"
    )
    await event.message.answer(summary, attachments=MENU_KB())
    await context.clear()


@router.message_callback(F.callback.payload.startswith("recruit:score:"), Recruiter.confirming)
async def handle_score_new(event: MessageCallback, context: MemoryContext, potok, ai_client):
    await event.answer()
    data = await context.get_data()
    await _run_scoring(event, context, potok, ai_client, data["job"], data["new_applicants"])


@router.message_callback(F.callback.payload.startswith("recruit:rescore:"), Recruiter.confirming)
async def handle_rescore_all(event: MessageCallback, context: MemoryContext, potok, ai_client):
    await event.answer()
    data = await context.get_data()
    await _run_scoring(event, context, potok, ai_client, data["job"], data["all_applicants"])
