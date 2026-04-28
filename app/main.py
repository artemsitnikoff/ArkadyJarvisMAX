import asyncio
import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI

# maxapi compat patches — must run before create_bot/create_dispatcher
# so the patched classes are picked up by the rest of app code.
from app.bot.maxapi_compat import apply_patches as _apply_maxapi_patches
_apply_maxapi_patches()

from app.api.routes import router as api_router
from app.bot.create import create_bot, create_dispatcher
from app.bot.middlewares import AuthMiddleware, ErrorMiddleware
from app.config import settings
from app.db import close_db, init_db
from app.scheduler.jobs import daily_summary_job, monday_poster_job, wednesday_frog_job
from app.services.ai_client import AIClient
from app.services.bitrix_client import BitrixClient
from app.services.openclaw_client import OpenClawClient
from app.services.openrouter_client import OpenRouterClient
from app.services.claude_token import init_token_file
from app.services.potok_client import PotokClient
from app.version import __version__


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("arkadyjarvismax")

scheduler = AsyncIOScheduler()
bot = create_bot()
dp = create_dispatcher()


# Service instances and extra kwargs propagated into every handler via the
# dispatcher's data-dict (maxapi filters by function annotation names).
services: dict = {}


class _InjectServicesMiddleware:
    """Prepend services dict into every handler's data. Implements
    BaseMiddleware's protocol without subclassing so instantiation is simple."""

    def __init__(self, services: dict):
        self.services = services

    async def __call__(self, handler, event_object, data):
        for k, v in self.services.items():
            data.setdefault(k, v)
        return await handler(event_object, data)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("Database ready")

    init_token_file()

    ai_client = AIClient()
    bitrix = BitrixClient()
    openrouter = OpenRouterClient()
    openclaw = OpenClawClient()
    potok = PotokClient()

    services.update({
        "ai_client": ai_client,
        "bitrix": bitrix,
        "openrouter": openrouter,
        "openclaw": openclaw,
        "potok": potok,
        "bot": bot,
    })

    # maxapi middleware chain is "dp.middlewares + router.middlewares + handler.middlewares".
    # Our inject mw fills kwargs, auth gates requests, error wraps everything.
    # Order: error (outermost) → auth → services → handler.
    dp.middlewares.append(ErrorMiddleware())
    dp.middlewares.append(AuthMiddleware())
    dp.middlewares.append(_InjectServicesMiddleware(services))

    # Any previous webhook subscription blocks long-polling. Wipe it.
    try:
        await bot.delete_webhook()
    except Exception as e:
        logger.warning("delete_webhook failed: %s", e)

    polling_task = asyncio.create_task(dp.start_polling(bot))
    logger.info("Bot polling started")

    scheduler.add_job(
        daily_summary_job,
        CronTrigger(
            hour=settings.summary_hour,
            minute=settings.summary_minute,
            timezone=settings.timezone,
        ),
        id="daily_summary",
        args=[bot, ai_client],
    )
    if settings.wednesday_frog_chat_id:
        scheduler.add_job(
            wednesday_frog_job,
            CronTrigger(
                day_of_week="wed", hour=10, minute=0, timezone=settings.timezone,
            ),
            id="wednesday_frog",
            args=[bot, ai_client, openrouter],
        )
        logger.info(
            "Scheduler: wednesday_frog at Wed 10:00 [%s] -> chat %s",
            settings.timezone, settings.wednesday_frog_chat_id,
        )

    if settings.monday_poster_chat_id:
        scheduler.add_job(
            monday_poster_job,
            CronTrigger(
                day_of_week="mon", hour=9, minute=0, timezone=settings.timezone,
            ),
            id="monday_poster",
            args=[bot, ai_client, openrouter],
        )
        logger.info(
            "Scheduler: monday_poster at Mon 09:00 [%s] -> chat %s",
            settings.timezone, settings.monday_poster_chat_id,
        )

    scheduler.start()
    logger.info(
        "Scheduler started: daily at %02d:%02d [%s]",
        settings.summary_hour,
        settings.summary_minute,
        settings.timezone,
    )

    yield

    scheduler.shutdown()
    dp.polling = False
    polling_task.cancel()
    try:
        await polling_task
    except asyncio.CancelledError:
        pass
    await bot.close_session()
    await ai_client.close()
    await bitrix.close()
    await openrouter.close()
    await openclaw.close()
    await potok.close()
    await close_db()
    logger.info("Shutdown complete")


app = FastAPI(
    title="ArkadyJarvisMAX",
    description="MAX-мессенджер бот для команды: Bitrix24, Jira, AI, рекрутинг",
    version=__version__,
    docs_url="/docs",
    lifespan=lifespan,
)
app.state.bot = bot
app.include_router(api_router, prefix="/api")
