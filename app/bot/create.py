from maxapi import Bot, Dispatcher
from maxapi.client.default import DefaultConnectionProperties
from maxapi.enums.parse_mode import ParseMode

from app.config import settings


def create_bot() -> Bot:
    """Create a MAX bot with an explicit Authorization header.

    maxapi 0.9.4 defaults to passing the token via the `?access_token=…`
    query-string. MAX Bot API now requires the `Authorization: <token>`
    header and returns 401 for query-string auth. We inject a default
    header into the aiohttp session via DefaultConnectionProperties —
    the query-param keeps going too but the header is what MAX reads.
    """
    token = settings.bot_token.get_secret_value()
    default_connection = DefaultConnectionProperties(
        headers={"Authorization": token},
    )
    return Bot(
        token=token,
        parse_mode=ParseMode.HTML,
        default_connection=default_connection,
    )


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher()

    # Import and include routers (order matters — catch-all last)
    from app.bot.routers.start import router as start_router
    from app.bot.routers.summarize import router as summarize_router
    from app.bot.routers.meeting import router as meeting_router
    from app.bot.routers.free_slots import router as free_slots_router
    from app.bot.routers.jira_task import router as jira_task_router
    from app.bot.routers.lead import router as lead_router
    from app.bot.routers.image import router as image_router
    from app.bot.routers.ask_ai import router as ask_ai_router
    from app.bot.routers.contract import router as contract_router
    from app.bot.routers.employee import router as employee_router
    from app.bot.routers.cicero import router as cicero_router
    from app.bot.routers.socrates import router as socrates_router
    from app.bot.routers.glafira import router as glafira_router
    from app.bot.routers.recruiter import router as recruiter_router
    from app.bot.routers.group import router as group_router
    from app.bot.routers.buffer import router as buffer_router

    dp.include_routers(
        start_router,
        summarize_router,
        meeting_router,
        free_slots_router,
        jira_task_router,
        lead_router,
        image_router,
        ask_ai_router,
        contract_router,
        employee_router,
        cicero_router,
        socrates_router,
        glafira_router,
        recruiter_router,
        group_router,
        buffer_router,  # catch-all — must be last
    )

    return dp
