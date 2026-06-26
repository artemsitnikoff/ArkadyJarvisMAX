import html as html_mod
import logging
from datetime import datetime

from maxapi import F, Router
from maxapi.context import MemoryContext
from maxapi.types import (
    CallbackButton,
    Command,
    CommandStart,
    LinkButton,
    MessageCallback,
    MessageCreated,
)
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from app import db
from app.config import settings
from app.version import __version__

logger = logging.getLogger("arkadyjarvismax")
router = Router()


def menu_kb():
    """Build the main menu keyboard. Rebuilt each time because MAX keyboards
    are attachments and cannot be safely reused across requests."""
    b = InlineKeyboardBuilder()
    b.row(CallbackButton(text="🏢 Начать день в офисе", payload="work:office"))
    b.row(CallbackButton(text="🏠 Начать день удалённо", payload="work:remote"))
    b.row(
        CallbackButton(text="👤 Сотрудник", payload="hint:employee"),
        CallbackButton(text="👥 Моя команда", payload="hint:team"),
    )
    b.row(
        CallbackButton(text="📅 Встреча", payload="hint:meeting"),
        CallbackButton(text="🕐 Найди время", payload="hint:freetime"),
    )
    b.row(
        CallbackButton(text="📝 Задача", payload="hint:task"),
        CallbackButton(text="💼 Лид", payload="hint:lead"),
    )
    b.row(
        CallbackButton(text="📋 Мои встречи", payload="hint:meetings"),
        CallbackButton(text="🎨 Картинка", payload="hint:image"),
    )
    b.row(
        CallbackButton(text="🧠 Спроси AI", payload="hint:askai"),
        CallbackButton(text="📊 Суммаризация", payload="hint:summary"),
    )
    b.row(
        CallbackButton(text="📄 Проверь договор", payload="hint:contract"),
        CallbackButton(text="⚖️ Цицерон", payload="hint:cicero"),
    )
    b.row(CallbackButton(text="🎓 Сократ", payload="hint:socrates"))
    b.row(
        CallbackButton(text="🤖 Глафира", payload="hint:glafira"),
        CallbackButton(text="👔 Анатолий", payload="hint:recruiter"),
    )
    b.row(CallbackButton(text="❓ Все команды", payload="hint:all"))
    return b.as_markup()


def back_menu_kb():
    b = InlineKeyboardBuilder()
    b.row(CallbackButton(text="◀️ Меню", payload="back:menu"))
    return b.as_markup()


def MENU_KB():
    return [menu_kb()]


def BACK_MENU_KB():
    return [back_menu_kb()]


HELP_TEXT = (
    "📖 <b>Как пользоваться</b>\n\n"
    "Всё работает через кнопки меню — жми нужную, бот подскажет что делать дальше.\n\n"

    "<b>🕘 Рабочий день</b>\n"
    "🏢 Начать день в офисе / 🏠 удалённо — открывает таймер в Bitrix24\n"
    "👤 Сотрудник — найти коллегу по имени (карточка с контактами)\n"
    "👥 Моя команда — список команды со статусами «работает / пауза / не начал»\n\n"

    "<b>📅 Календарь и задачи</b>\n"
    "📅 Встреча — создать встречу в Bitrix24 (время, дата, участники)\n"
    "🕐 Найди время — свободные слоты по календарям коллег на 5 рабочих дней\n"
    "📋 Мои встречи — сегодняшние встречи со ссылками в Bitrix\n"
    "📝 Задача — тикет в Jira (опиши сумбурно — AI оформит по шаблону)\n"
    "💼 Лид — CRM-лид в Bitrix24 (текстом или голосовым 🎤 — расшифрую и разберу)\n\n"

    "<b>🧠 AI-инструменты</b>\n"
    "🎨 Картинка — сгенерировать изображение по описанию (можно редактировать фото)\n"
    "🧠 Спроси AI — свободный вопрос к Claude\n"
    "📊 Суммаризация — отчёт по рабочим чатам за сегодня\n\n"

    "<b>📄 Документы</b>\n"
    "📄 Проверь договор — PDF/DOCX → AI сверит по чек-листу\n"
    "⚖️ Цицерон — AI-юрист (ГК, КоАП, АПК, НК РФ, КонсультантПлюс)\n"
    "🎓 Сократ — расшифровка встречи + ревью + экспертиза (ссылка на запись)\n\n"

    "<b>🤖 AI-сотрудники</b>\n"
    "🤖 Глафира — AI офис-менеджер через браузер (ограниченный доступ)\n"
    "👔 Анатолий — AI-рекрутёр, оценивает кандидатов в Potok.io (ограниченный доступ)\n\n"

    "<b>⚙️ Slash-команды</b>\n"
    "<code>/start</code> — авторизация в боте через Bitrix\n"
    "<code>/help</code> — это сообщение\n"
    "<code>/summary</code> — саммари текущего группового чата за сегодня"
)


@router.message_created(CommandStart())
async def cmd_start(event: MessageCreated, bitrix):
    msg = event.message
    sender = msg.sender
    user_id = sender.user_id

    logger.info(
        "MAX /start: user_id=%s first_name=%r last_name=%r username=%r",
        user_id, sender.first_name, sender.last_name, sender.username,
    )

    existing = await db.get_user(user_id)
    if existing and existing.get("bitrix_user_id"):
        await msg.answer(
            f"✅ Ты авторизован как <b>{html_mod.escape(existing['display_name'] or '')}</b>.\n\n"
            "Выбери команду — покажу подсказку:",
            attachments=MENU_KB(),
        )
        return

    # In MAX, user.username is optional (unlike Telegram where it's a unique
    # @-handle). We accept either: a username if present, or the numeric
    # user_id as a fallback identifier — the Bitrix admin puts either value
    # into the MAX field on the employee card.
    lookup_values: list[str] = [str(user_id)]
    if sender.username:
        lookup_values.append(sender.username)

    bitrix_id: int | None = None
    full_name: str | None = None
    for value in lookup_values:
        bitrix_id, full_name = await bitrix.find_user_by_nickname(value)
        if bitrix_id:
            break

    if not bitrix_id:
        full_name_display = html_mod.escape(
            (sender.first_name or "") + " " + (sender.last_name or "")
        ).strip()
        hint_lines = [
            "❌ Не нашёл тебя в Bitrix24.",
            "",
            f"Твои данные MAX:",
            f"• <b>user_id</b>: <code>{user_id}</code>",
        ]
        if full_name_display:
            hint_lines.append(f"• <b>имя</b>: {full_name_display}")
        if sender.username:
            hint_lines.append(f"• <b>username</b>: @{html_mod.escape(sender.username)}")
        hint_lines += [
            "",
            "Попроси администратора Bitrix открыть твою карточку сотрудника "
            "и вписать в поле «MAX» твой <b>user_id</b> "
            f"(<code>{user_id}</code>). После этого /start снова.",
        ]
        await msg.answer("\n".join(hint_lines))
        return

    await db.upsert_user(
        max_user_id=user_id,
        bitrix_user_id=bitrix_id,
        display_name=full_name,
    )

    logger.info(
        "User authorized: max=%s → bitrix=%s (%s)",
        user_id, bitrix_id, full_name,
    )
    await msg.answer(
        f"✅ Ты авторизован как <b>{html_mod.escape(full_name or '')}</b>\n\n"
        "Выбери команду — покажу подсказку:",
        attachments=MENU_KB(),
    )


@router.message_created(Command("help"))
async def cmd_help(event: MessageCreated):
    await event.message.answer(HELP_TEXT, attachments=MENU_KB())


@router.message_callback(F.callback.payload == "noop")
async def handle_noop(event: MessageCallback):
    await event.answer()


@router.message_callback(F.callback.payload.startswith("work:"))
async def handle_work(event: MessageCallback, bitrix, ai_client, db_user=None):
    from app.bot.routers.work import start_work_day
    if db_user is None:
        db_user = await db.get_user(event.callback.user.user_id)
    await start_work_day(event, bitrix, ai_client, db_user)


def _simple_fsm_hints() -> dict[str, tuple]:
    """(state, prompt_text, initial_fsm_data | None) for button-only FSM entries."""
    from app.bot.routers.ask_ai import AskAI
    from app.bot.routers.cicero import Cicero
    from app.bot.routers.contract import ContractCheck
    from app.bot.routers.employee import FindEmployee
    from app.bot.routers.free_slots import BookSlot
    from app.bot.routers.image import ImageGen
    from app.bot.routers.jira_task import CreateTask
    from app.bot.routers.lead import CreateLead
    from app.bot.routers.meeting import MeetingSetup
    from app.bot.routers.socrates import Socrates

    return {
        "employee": (
            FindEmployee.waiting_for_name,
            "👤 <b>Найди сотрудника</b>\n\nНапиши имя или фамилию:",
            None,
        ),
        "meeting": (
            MeetingSetup.waiting_for_command,
            "📅 <b>Создать встречу</b>\n\n"
            "Напиши время и участников:\n"
            "<code>14:00 @nick1 @nick2</code>\n\n"
            "Или просто время — найду коллег по имени.",
            None,
        ),
        "freetime": (
            BookSlot.searching_attendee,
            "🕐 <b>Найди время</b>\n\nНапиши имя или фамилию коллеги:",
            {"attendee_ids": [], "attendee_names": []},
        ),
        "task": (
            CreateTask.waiting_for_input,
            "📝 <b>Задача Jira</b>\n\n"
            "Опиши задачу своими словами: что делаем, для кого, к какому сроку, "
            "какие блокеры. Я переформулирую по нашему шаблону.\n\n"
            "Формат:\n"
            "<code>DC &lt;твоё описание&gt;</code>\n\n"
            "Где <b>DC</b> — ключ проекта в Jira.",
            None,
        ),
        "lead": (
            CreateLead.waiting_for_info,
            "💼 <b>Создать лид</b>\n\n"
            "Напиши данные контакта (имя, компания, телефон, email) "
            "или запиши голосовое 🎤 — расшифрую и сам разберу поля.",
            None,
        ),
        "image": (
            ImageGen.waiting_for_prompt,
            "🎨 Напиши что нарисовать:",
            None,
        ),
        "askai": (
            AskAI.waiting_for_question,
            "🧠 Задай вопрос:",
            None,
        ),
        "contract": (
            ContractCheck.waiting_for_document,
            "📄 <b>Проверка договора</b>\n\n"
            "Пришли файл (PDF, DOCX или TXT) — проверю по правилам "
            "и выдам список несоответствий.",
            None,
        ),
        "cicero": (
            Cicero.chatting,
            "⚖️ <b>Цицерон</b> — юридический консультант\n\n"
            "Задай вопрос текстом или приложи документ (PDF/DOCX/TXT) "
            "с вопросом в подписи. Отвечу по российскому законодательству.\n\n"
            "Можно задавать вопросы подряд. Выход — «◀️ Меню».",
            None,
        ),
        "socrates": (
            Socrates.waiting_for_url,
            "🎓 <b>Сократ</b> — ассистент аналитика\n\n"
            "Пришли <b>ссылку</b> на запись встречи (Яндекс.Диск / прямой URL). "
            "Верну транскрипт, ревью и заготовку для аналитика.\n\n"
            "⚠️ Встречи длиннее 90 минут пока не обрабатываю.",
            None,
        ),
    }


async def _enter_glafira(event: MessageCallback, context: MemoryContext):
    from app.bot.routers.glafira import GLAFIRA_ALLOWED, glafira_exit_kb, Glafira

    if event.callback.user.user_id not in GLAFIRA_ALLOWED:
        await event.message.answer(
            "🚧 Функция в тестовом режиме. Доступ ограничен.",
            attachments=MENU_KB(),
        )
        await event.answer()
        return
    await context.set_state(Glafira.chatting)
    await context.update_data(messages=[])
    await event.message.answer(
        "🤖 <b>Глафира</b> — AI офис-менеджер\n\n"
        "Напиши что нужно сделать. Я управляю браузером "
        "и могу выполнять задачи на сайтах.\n\n"
        "Для выхода нажми «◀️ Меню».",
        attachments=[glafira_exit_kb()],
    )
    await event.answer()


async def _enter_recruiter(event: MessageCallback, context: MemoryContext, potok):
    from app.bot.routers.recruiter import RECRUITER_ALLOWED, Recruiter

    if event.callback.user.user_id not in RECRUITER_ALLOWED:
        await event.message.answer(
            "🚧 Функция в тестовом режиме. Доступ ограничен.",
            attachments=MENU_KB(),
        )
        await event.answer()
        return
    await event.message.answer(
        "👔 <b>Анатолий</b> — AI-рекрутёр\n\n"
        "Оцениваю кандидатов по вакансиям из Potok.io: сравниваю резюме "
        "с описанием вакансии через Claude, ставлю балл 0–100, выделяю "
        "сильные и слабые стороны.\n\n"
        "Сейчас подтяну список вакансий — выбери нужную."
    )
    wait = await event.message.answer("👔 Загружаю вакансии...")
    try:
        jobs = await potok.get_jobs()
    except Exception as e:
        logger.error("Potok error: %s", e, exc_info=True)
        await wait.edit(text=f"❌ Potok недоступен: {e}", attachments=MENU_KB())
        await event.answer()
        return
    if not jobs:
        await wait.edit(text="👔 Нет активных вакансий.", attachments=MENU_KB())
        await event.answer()
        return
    b = InlineKeyboardBuilder()
    for j in jobs[:20]:
        b.row(CallbackButton(
            text=f"{j.name} ({j.total_applicants})",
            payload=f"recruit:job:{j.id}",
        ))
    b.row(CallbackButton(text="◀️ Меню", payload="recruit:exit"))
    await wait.edit(
        text="👔 Выбери вакансию для оценки кандидатов:",
        attachments=[b.as_markup()],
    )
    await context.set_state(Recruiter.choosing_job)
    await event.answer()


@router.message_callback(F.callback.payload.startswith("hint:"))
async def handle_hint(
    event: MessageCallback, context: MemoryContext,
    bitrix, potok, ai_client, bot,
):
    key = event.callback.payload.split(":", 1)[1]

    if key == "team":
        await _show_team(event, bitrix)
        return
    if key == "meetings":
        await _show_meetings(event, bitrix)
        return
    if key == "summary":
        await event.answer()
        await _run_summary(event, ai_client, bot=bot)
        return
    if key == "glafira":
        await _enter_glafira(event, context)
        return
    if key == "recruiter":
        await _enter_recruiter(event, context, potok)
        return

    simple = _simple_fsm_hints()
    if key in simple:
        fsm_state, text, init_data = simple[key]
        await context.set_state(fsm_state)
        if init_data:
            await context.update_data(**init_data)
        await event.message.answer(text, attachments=BACK_MENU_KB())
        await event.answer()
        return

    if key == "all":
        text = f"{HELP_TEXT}\n\n<i>v{__version__}</i>"
    else:
        text = "🤷 Неизвестная команда"
    await event.message.answer(text, attachments=BACK_MENU_KB())
    await event.answer()


async def _run_summary(event: MessageCallback, ai_client, bot=None):
    """Run summarization. In DM: overview of all groups. In group: summarize current chat."""
    from zoneinfo import ZoneInfo
    from maxapi.enums.chat_type import ChatType

    from app.summarizer import build_daily_overview, summarize_from_buffer, summarize_messages

    await event.answer()
    tz = ZoneInfo(settings.timezone)
    start_of_day = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)

    try:
        if event.message.recipient.chat_type == ChatType.CHAT:
            summary = await summarize_from_buffer(
                event.message.recipient.chat_id, ai_client=ai_client, since=start_of_day,
            )
            await event.message.answer(f"📊 #summary\n\n{summary}", attachments=MENU_KB())
        else:
            wait_msg = await event.message.answer("📊 Собираю обзор дня...")
            groups = await db.get_all_group_chats()
            user_summaries: list[tuple[str, str]] = []

            for group in groups:
                chat_id = group["chat_id"]
                chat_title = group.get("chat_title") or str(chat_id)
                msgs = await db.get_buffered_messages(chat_id, since=start_of_day)
                if not msgs:
                    continue
                summary = await summarize_messages(msgs, ai_client=ai_client)
                user_summaries.append((chat_title, summary))

            if not user_summaries:
                await wait_msg.edit(
                    text="Нет сообщений в группах за сегодня.", attachments=MENU_KB(),
                )
                return

            db_user = await db.get_user(event.callback.user.user_id)
            user_name = db_user.get("display_name", "") if db_user else ""
            overview = await build_daily_overview(
                user_summaries, ai_client=ai_client, user_name=user_name,
            )
            await wait_msg.edit(
                text=f"#summary\n📊 <b>Обзор дня</b>\n\n{overview}",
                attachments=MENU_KB(),
            )
    except Exception as e:
        logger.error("Summary error: %s", e, exc_info=True)
        await event.message.answer(f"❌ Ошибка суммаризации: {e}", attachments=MENU_KB())


def _work_status_line(person: dict) -> str:
    name = html_mod.escape(person["name"])
    pos = html_mod.escape(person.get("position", ""))
    status = person.get("work_status", "")
    start = person.get("work_start", "")

    if status == "OPENED":
        icon = "\U0001f7e2"
        time_str = ""
        if start:
            try:
                if "T" in start:
                    dt = datetime.fromisoformat(start)
                else:
                    dt = datetime.strptime(start, "%d.%m.%Y %H:%M:%S")
                time_str = f" (с {dt.strftime('%H:%M')})"
            except (ValueError, TypeError):
                pass
        label = f"{icon} <b>{name}</b>"
        if pos:
            label += f" — {pos}"
        label += time_str
    elif status == "PAUSED":
        label = f"\U0001f7e1 <b>{name}</b>"
        if pos:
            label += f" — {pos}"
        label += " (пауза)"
    else:
        label = f"⚪ <b>{name}</b>"
        if pos:
            label += f" — {pos}"

    return label


async def _show_team(event: MessageCallback, bitrix):
    db_user = await db.get_user(event.callback.user.user_id)
    if not db_user or not db_user.get("bitrix_user_id"):
        await event.message.answer("❌ Сначала авторизуйся: /start")
        await event.answer()
        return

    await event.answer()
    wait_msg = await event.message.answer("👥 Загружаю команду...")

    try:
        team = await bitrix.get_my_team(db_user["bitrix_user_id"])
    except Exception as e:
        logger.error("Failed to fetch team: %s", e, exc_info=True)
        await wait_msg.edit(text="❌ Не удалось загрузить команду", attachments=MENU_KB())
        return

    if not team:
        await wait_msg.edit(
            text="❌ Информация о команде недоступна", attachments=MENU_KB(),
        )
        return

    dept = html_mod.escape(team.get("department", ""))
    lines = [f"👥 <b>Моя команда</b> — {dept}"] if dept else ["👥 <b>Моя команда</b>"]

    if team.get("supervisor"):
        sup = team["supervisor"]
        lines.append(f"\n👆 <b>Руководитель:</b> {_work_status_line(sup)}")

    if team.get("is_head") and team.get("subordinates"):
        lines.append("\n👇 <b>Подчинённые:</b>")
        for p in team["subordinates"]:
            lines.append(_work_status_line(p))
        if team.get("colleagues"):
            lines.append("\n👥 <b>Коллеги (руководители):</b>")
            for p in team["colleagues"]:
                lines.append(_work_status_line(p))
    elif team.get("colleagues"):
        lines.append("\n👥 <b>Коллеги:</b>")
        for p in team["colleagues"]:
            lines.append(_work_status_line(p))

    if not team.get("supervisor") and not team.get("colleagues") and not team.get("subordinates"):
        lines.append("\nНет данных о команде")

    await wait_msg.edit(text="\n".join(lines), attachments=BACK_MENU_KB())


async def _show_meetings(event: MessageCallback, bitrix):
    db_user = await db.get_user(event.callback.user.user_id)
    if not db_user or not db_user.get("bitrix_user_id"):
        await event.message.answer("❌ Сначала авторизуйся: /start")
        await event.answer()
        return

    try:
        events_list = await bitrix.get_user_events(db_user["bitrix_user_id"])
    except Exception as e:
        logger.error("Failed to fetch meetings: %s", e)
        await event.message.answer("❌ Не удалось загрузить встречи")
        await event.answer()
        return

    if not events_list:
        await event.message.answer(
            "📋 <b>Мои встречи</b>\n\nНет встреч на сегодня",
            attachments=BACK_MENU_KB(),
        )
        await event.answer()
        return

    domain = db_user.get("bitrix_domain") or settings.bitrix_domain
    uid = db_user["bitrix_user_id"]
    b = InlineKeyboardBuilder()
    for ev in events_list:
        try:
            dt = datetime.strptime(ev["date_from"], "%d.%m.%Y %H:%M:%S")
            time_str = dt.strftime("%H:%M")
        except (ValueError, KeyError):
            time_str = "??:??"
        name = ev["name"]
        label = f"{time_str} {name}"
        if len(label) > 45:
            label = label[:42] + "..."
        url = f"https://{domain}/company/personal/user/{uid}/calendar/?EVENT_ID={ev['id']}"
        b.row(LinkButton(text=label, url=url))

    b.row(CallbackButton(text="◀️ Меню", payload="back:menu"))
    await event.message.answer("📋 <b>Мои встречи</b>", attachments=[b.as_markup()])
    await event.answer()


@router.message_callback(F.callback.payload == "back:menu")
async def handle_back_menu(event: MessageCallback, context: MemoryContext):
    await context.clear()
    await event.message.answer(
        "Выбери команду — покажу подсказку:",
        attachments=MENU_KB(),
    )
    await event.answer()
