import html as html_mod
import logging
import re

from maxapi import F, Router
from maxapi.context import MemoryContext, State, StatesGroup
from maxapi.types import MessageCreated

from app.bot.routers.start import MENU_KB
from app.config import settings
from app.db import DbUser
from app.services.jira_client import JiraClient
from app.services.prompts import load_prompt

logger = logging.getLogger("arkadyjarvismax")
router = Router()


class CreateTask(StatesGroup):
    waiting_for_input = State()


@router.message_created(F.message.body.text, CreateTask.waiting_for_input)
async def handle_task_fsm(
    event: MessageCreated, context: MemoryContext,
    db_user: DbUser, bitrix, ai_client,
):
    msg = event.message
    text = (msg.body.text or "").strip()
    if not text:
        await msg.reply("Напиши задачу текстом: <code>DC Описание задачи</code>")
        return
    await context.clear()

    # MAX: linked/replied messages aren't as easy to access as in Telegram's
    # reply_to_message — the `link` field contains the referenced message if
    # the user replied. Best-effort pull.
    reply_text = ""
    try:
        linked = msg.link
        if linked and linked.message and linked.message.text:
            reply_text = linked.message.text.strip()
    except Exception:
        pass

    await _create_task(
        msg, text, reply_text, db_user=db_user, bitrix=bitrix, ai_client=ai_client,
    )


def _extract_summary(structured: str, fallback: str) -> str:
    headline = ""
    for line in structured.splitlines():
        cleaned = line.strip().strip("*").strip()
        if cleaned.lower().startswith("задача:"):
            headline = cleaned.split(":", 1)[1].strip().strip("*").strip()
            if headline:
                break
    if not headline:
        headline = fallback
    headline = " ".join(headline.split())
    return headline[:200] or "Задача без названия"


async def _create_task(msg, body: str, reply_text: str, *, db_user: DbUser, bitrix, ai_client):
    try:
        key_match = re.search(r"\b([A-Z][A-Z0-9]{1,9})\b", body)
        if not key_match:
            await msg.reply("❌ Укажи проект: <code>DC Сделать landing page</code>")
            return
        project_key = key_match.group(1)

        inline_desc = body[key_match.end():].strip()
        full_text = "\n".join(filter(None, [inline_desc, reply_text]))
        if not full_text:
            await msg.reply(
                "❌ Укажи описание задачи:\n"
                "<code>DC Сделать landing page</code>"
            )
            return

        wait_msg = await msg.reply("📝 Оформляю задачу по шаблону...")

        template = load_prompt("jira_task_template")
        structured = await ai_client.complete(f"{template}\n{full_text}")

        short_fallback = full_text.split("\n")[0].split(". ")[0]
        summary = _extract_summary(structured, short_fallback)
        description = structured

        user_email = await bitrix.get_user_email(db_user["bitrix_user_id"])

        async with JiraClient() as jira:
            jira_username = None
            if user_email:
                jira_username = await jira.find_user_by_email(user_email)

            result = await jira.create_issue(
                project_key, summary, description,
                reporter_name=jira_username,
                assignee_name=jira_username,
            )

        issue_key = result["key"]
        jira_base = settings.jira_url.rstrip("/")
        esc = html_mod.escape
        await wait_msg.edit(
            text=f"✅ Задача создана: {esc(issue_key)}\n"
                 f"📝 {esc(summary)}\n"
                 f"🔗 {jira_base}/browse/{esc(issue_key)}",
            attachments=MENU_KB(),
        )
        logger.info("*** Jira issue created: %s", issue_key)
    except Exception as e:
        logger.error("*** ERROR creating Jira issue: %s", e, exc_info=True)
        await msg.reply(f"❌ Ошибка создания задачи: {html_mod.escape(str(e))}")
