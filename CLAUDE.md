# ArkadyJarvisMAX

Multi-user MAX-messenger bot (copy of ArkadyJarvis adapted from aiogram/Telegram → `maxapi`/MAX) for team chat summarization, Bitrix24 calendar/CRM, Jira integration, AI assistants (general, legal, recruiter, office-manager), image generation, recruiter scoring (Potok.io), contract validation, voice-to-lead, and scheduled motivational content.

Bot: **@id5408285792_1_bot** (MAX).

## Tech Stack

- **Python 3.11+**, [maxapi](https://github.com/max-messenger/max-botapi-python) (MAX Bot API), FastAPI + Uvicorn
- **AI**: Claude CLI (subscription-based, no API tokens) via subprocess; OpenRouter (Gemini 3 Pro Image for image generation, Gemini 2.5 Pro for voice transcription)
- **Integrations**: Bitrix24 REST API, Jira REST API, Potok.io ATS API, OpenClaw (browser RPA via AI)
- Uvicorn owns the event loop; `maxapi.Dispatcher` polling runs as `asyncio.create_task()` in FastAPI lifespan
- APScheduler for cron jobs (daily summary, Wednesday frog, Monday poster)
- aiosqlite for persistence (users, message buffer, group chats, muted groups)
- pydantic-settings for config from `.env`
- pypdf + python-docx for document parsing (contract check, Cicero)

## Differences from ArkadyJarvis (Telegram)

| Aspect | Telegram (aiogram) | MAX (maxapi) |
|--------|--------------------|--------------|
| Event for new messages | `@router.message(...)` | `@router.message_created(...)` |
| Event for button presses | `@router.callback_query(...)` | `@router.message_callback(...)` |
| Inline keyboard | `reply_markup=InlineKeyboardMarkup(...)` | `attachments=[InlineKeyboardBuilder.as_markup()]` (keyboards are *attachments*) |
| Bot added/removed | `my_chat_member` filter | `bot_added` / `bot_removed` events |
| Chat types | `private` / `group` / `supergroup` / `channel` | `DIALOG` / `CHAT` |
| FSM | `FSMContext` (`state`) | `MemoryContext` (`context`) — auto-injected by annotation |
| `message.text` | `message.text` | `event.message.body.text` |
| `message.from_user.id` | `message.from_user.id` | `event.message.sender.user_id` |
| `message.chat.id` | `message.chat.id` | `event.message.recipient.chat_id` |
| Callback data | `callback.data` (64 bytes) | `event.callback.payload` (up to 1024 chars) |
| Callback reply | `callback.answer(..., show_alert=...)` | `event.answer(notification=...)` |
| File attachment ingest | `message.document` + `bot.download(...)` | iterate `message.body.attachments`, fetch `payload.url` via HTTP |
| File upload | `BufferedInputFile(bytes, filename=...)` | `InputMediaBuffer(buffer=bytes, filename=...)` |
| Webhook management | aiogram sets webhook via API | `bot.delete_webhook()` + `dp.handle_webhook(...)` |

User-facing behaviour is preserved: same MENU_KB layout (same emoji/labels), same FSM flows, same prompts, same authorization chain (@username → Bitrix lookup → CRM card).

## Project Structure

```
app/
  main.py                  # FastAPI app, lifespan, maxapi polling, APScheduler
  config.py                # pydantic-settings (Settings class, reads .env)
  db.py                    # aiosqlite: schema, CRUD (users keyed by max_user_id)
  utils.py                 # Parsers (time, attendees, Bitrix datetime), merge_intervals, md_to_telegram_html(), parse_json_response()
  summarizer.py            # Claude summarization (group chat + daily overview)
  version.py               # __version__
  bot/
    create.py              # create_bot() + create_dispatcher() — router registration order matters
    middlewares.py         # ErrorMiddleware + AuthMiddleware (handle MessageCreated + MessageCallback)
    attachments.py         # Helpers to download files/images/audio from MAX attachment URLs (no bot.download in maxapi)
    routers/
      start.py             # /start (auto-auth via @username → Bitrix), /help, MENU_KB, hint callbacks, Мои встречи, team, work:*
      summarize.py         # /summary command — on-demand chat summarization
      meeting.py           # FSM MeetingSetup — time/date/attendee parsing, Bitrix meeting creation
      free_slots.py        # FSM BookSlot — calendar accessibility + slot booking
      _attendee_picker.py  # Shared inline-keyboard helpers for meeting + free_slots attendee search
      jira_task.py         # FSM CreateTask — raw input reformatted via prompts/jira_task_template.md before ticket creation
      lead.py              # FSM CreateLead — text OR voice; voice transcribed via OpenRouter; AI extracts fields → Bitrix CRM
      image.py             # FSM ImageGen — image generation via Gemini 3 Pro Image, supports photo+caption editing
      ask_ai.py             # FSM AskAI — Claude answers, md_to_telegram_html conversion
      contract.py          # FSM ContractCheck — parse PDF/DOCX/TXT, check against rules in prompts/contract_check.md
      employee.py          # FSM FindEmployee + employee card display
      cicero.py            # FSM Cicero — legal consultant (RU law), persistent chat with optional document attachments
      socrates.py          # FSM Socrates — meeting analyser (Yandex.Disk/direct URL → ffmpeg → transcript → review → expertise)
      glafira.py           # Glafira (AI office manager) — FSM chatting mode, OpenClaw streaming
      recruiter.py         # Анатолий (AI recruiter) — Potok.io integration, candidate scoring via injected AIClient
      work.py              # Work day start logic (work:office / work:remote callback handler with AI greeting)
      group.py             # bot_added / bot_removed — tracks group chats in DB
      buffer.py            # Catch-all (LAST router): buffers all group CHAT messages to SQLite
  services/
    ai_client.py           # AIClient — Claude CLI wrapper (copied verbatim from ArkadyJarvis)
    claude_token.py        # Claude OAuth token auto-refresh (copied verbatim)
    bitrix_client/         # BitrixClient — refactored into package with mixins (copied verbatim)
    jira_client.py         # JiraClient — async context manager (copied verbatim)
    document_parser.py     # Extract text from .pdf/.docx/.txt (copied verbatim)
    ffmpeg_tool.py         # ffmpeg/ffprobe wrappers (copied verbatim)
    meeting_downloader.py  # Download recording from Yandex.Disk public API or direct URL (copied verbatim)
    meeting_pipeline.py    # Socrates orchestration (copied verbatim)
    openclaw_client.py     # OpenClawClient — HTTP SSE client for OpenClaw gateway (copied verbatim)
    openrouter_client.py   # OpenRouterClient — image generation + voice transcription (copied verbatim)
    prompts.py             # load_prompt(name) — loads templates from prompts/ directory (copied verbatim)
    potok_client.py        # PotokClient — Potok.io ATS API (copied verbatim)
    potok_models.py        # Pydantic models (copied verbatim)
    resume_scorer.py       # score_applicant (copied verbatim)
  scheduler/
    jobs.py                # daily_summary_job, wednesday_frog_job, monday_poster_job (+ FROG_STYLES list) — rewrites aiogram calls to bot.send_message(chat_id=..., attachments=[InputMediaBuffer])
  api/
    routes.py              # GET /api/health, POST /api/bitrix/notify, POST /api/bitrix/broadcast (webhook endpoints)
prompts/
  contract_check.md        # Contract validation checklist
  cicero.md                # Legal consultant system prompt (ГК, КоАП, АПК, НК, КонсультантПлюс)
  jira_task_template.md    # Meta-prompt for Jira ticket structure
  voice_transcribe.md      # Diarization prompt for voice messages
  wednesday_frog.md        # Meta-prompt for Wed 10:00 frog meme (with {style} placeholder)
  monday_poster.md         # Meta-prompt for Mon 09:00 Soviet-constructivist poster
  meeting_review.md        # Socrates stage 2 (review / protocol)
  meeting_brief.md         # Socrates stage 3 (analyst brief)
data/
  arkadyjarvismax.db       # SQLite database
  bitrix_tokens.json       # Bitrix OAuth tokens (auto-refreshed)
  .claude_token.json       # Claude OAuth tokens (auto-refreshed)
scripts/
  show_users.py            # CLI: all users + last activity
  show_groups.py           # CLI: all group chats + 7-day message counts
  test_wednesday_frog.py   # Manually fire Wednesday frog for a given chat_id
  test_monday_poster.py    # Manually fire Monday poster for a given chat_id
```

## Key Patterns

### maxapi Middleware Chain
The MAX dispatcher does **not** distinguish outer/inner middlewares like aiogram. All middlewares in `dp.middlewares + router.middlewares + handler.middlewares` are applied in order. We install three global middlewares in `main.py` lifespan, outermost first:
1. `ErrorMiddleware` — catches unhandled exceptions, replies with generic user message.
2. `AuthMiddleware` — loads `db_user`, gates `/summary`, handles muted groups.
3. `_InjectServicesMiddleware` — fills `data` with `ai_client`, `bitrix`, `openrouter`, `openclaw`, `potok`, `bot`. maxapi filters the dict into each handler based on the handler's function-argument annotations.

### Router Registration Order (in `create.py`)
Order matters — `buffer.py` must be last (catch-all):
1. start → 2. summarize → 3. meeting → 4. free_slots → 5. jira_task → 6. lead → 7. image → 8. ask_ai → 9. contract → 10. employee → 11. cicero → 12. socrates → 13. glafira → 14. recruiter → 15. group → 16. buffer

### Authorization Flow
1. User sends `/start` → bot looks up `@username` in Bitrix field (`BITRIX_MAX_FIELD`, default `UF_USR_1678964886664`).
2. If found → saves `(max_user_id, bitrix_user_id, display_name)` to `users` table.
3. `AuthMiddleware` blocks protected commands if user not authorized.
4. Public commands: `/start`, `/help` — always allowed.
5. Callback handlers receive `db_user` via middleware.

### MENU_KB (Inline Keyboard)
Defined in `start.py`. Inline keyboards in MAX are attachments, so `MENU_KB()` is a **function** that returns `[builder.as_markup()]` (a list containing one attachment). Always call it — don't cache the result across requests.

Layout (rows top → bottom):
- Начать день в офисе
- Начать день удалённо
- Сотрудник | Моя команда
- Встреча | Найди время
- Задача | Лид
- Мои встречи | Картинка
- Спроси AI | Суммаризация
- Проверь договор | Цицерон
- Сократ
- Глафира | Анатолий
- Все команды

### Attachment download (MAX-specific)
`app/bot/attachments.py` exposes `first_file()`, `first_image()`, `first_audio()`, `download_attachment()`, `download_to_path()`. MAX attachments carry a signed HTTPS URL directly on the payload, so downloading is just `httpx.stream("GET", url)`. The `Bot` object does **not** have an aiogram-style `bot.download(...)` — do NOT port that API naively.

### Database Schema

```sql
users (max_user_id PK, bitrix_user_id, bitrix_domain, display_name, is_active, created_at)
group_chats (chat_id PK, chat_title, added_at, summary_enabled)
message_buffer (id PK AUTO, chat_id, sender_id, sender_name, text, sent_at) + INDEX(chat_id, sent_at)
muted_groups (chat_id PK)
```

### Summarization, Scheduler, etc.
Same logic as ArkadyJarvis. Scheduler posts images via `bot.send_message(chat_id=..., attachments=[InputMediaBuffer(buffer=..., filename=...)])`. Daily-summary membership check uses `bot.get_chat_member(chat_id, max_uid)` which returns `None` instead of aiogram's `.status in ("left","kicked")`.

## Config (.env)

Required: `BOT_TOKEN`

AI: `CLAUDE_CODE_OAUTH_TOKEN`, `CLAUDE_REFRESH_TOKEN`, `CLAUDE_CLI_PATH` (default `claude`), `CLAUDE_MODEL` (optional override), `CLAUDE_OAUTH_CLIENT_ID` (default official Claude Code ID)

OpenRouter: `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` (default `google/gemini-2.5-pro`), `OPENROUTER_TIMEOUT` (default 300s)

Bitrix24: `BITRIX_CLIENT_ID`, `BITRIX_CLIENT_SECRET`, `BITRIX_DOMAIN`, `BITRIX_REFRESH_TOKEN`, `BITRIX_MAX_FIELD` (default `UF_USR_1678964886664`)

Potok.io: `POTOK_API_TOKEN`, `POTOK_BASE_URL` (default `https://app.potok.io`)

OpenClaw: `OPENCLAW_URL`, `OPENCLAW_TOKEN`, `OPENCLAW_AGENT_ID` (default `main`)

Jira: `JIRA_URL`, `JIRA_USERNAME`, `JIRA_PASSWORD`

Webhook: `WEBHOOK_TOKEN`

Access control: `GLAFIRA_ALLOWED` (comma-separated MAX user IDs), `RECRUITER_ALLOWED`

Scheduled content: `WEDNESDAY_FROG_CHAT_ID` (default 0 = disabled), `MONDAY_POSTER_CHAT_ID` (default 0 = disabled)

Socrates: `FFMPEG_BIN` (default `ffmpeg`), `MEETING_MAX_MINUTES` (default 90)

Other: `DB_PATH` (default `data/arkadyjarvismax.db`), `SUMMARY_HOUR` (default 19), `SUMMARY_MINUTE` (default 0), `TIMEZONE` (default `Asia/Novosibirsk`)

## Running

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .
uvicorn app.main:app --host 0.0.0.0 --port 8002
```

Health check: `curl localhost:8002/api/health`

Docker: `docker compose up --build` (exposes port 8002)

## Known issues / TODOs

- **maxapi is an unofficial/community library** (but verified by the MAX team). Occasional API gaps: no `reply_markup` on send, no `bot.download()` helper, no built-in typing indicator use in handlers. Work around via attachments + direct HTTP fetch.
- Reply-to-message (`message.link`) semantics differ — in MAX, `message.link` wraps a `LinkedMessage` only when the user explicitly quoted. We best-effort extract it in `meeting.py` and `jira_task.py`.
- `message.sender.username` may be absent for users who haven't set a MAX @-handle. `/start` tells them to set one.
- Callback payload can carry up to 1024 chars — much more than Telegram's 64. Our picker callbacks stay short anyway (`pick:<id>:<name[:40]>`), but future buttons can be more verbose safely.
- Socrates SSRF guard has a narrow TOCTOU DNS-rebinding window (inherited from Socrates in ArkadyJarvis). Compensating controls: authorised users only + internal Tailscale-only deployment.
- Bitrix field `BITRIX_MAX_FIELD` defaults to the same UF_USR as ArkadyJarvis's Telegram field — if you reuse the same Bitrix portal, add a separate custom field for MAX and set `BITRIX_MAX_FIELD` accordingly.
