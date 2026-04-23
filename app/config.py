from zoneinfo import ZoneInfo

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # MAX Bot (from https://dev.max.ru)
    bot_token: SecretStr

    openrouter_api_key: SecretStr = SecretStr("")
    openrouter_model: str = "google/gemini-2.5-pro"
    openrouter_timeout: float = 300.0

    # Bitrix24 (shared app — singleton client)
    bitrix_client_id: str = ""
    bitrix_client_secret: SecretStr = SecretStr("")
    bitrix_domain: str = ""
    bitrix_refresh_token: str = ""
    # Custom Bitrix field holding the MAX/Telegram username (@handle).
    # Name kept as `bitrix_telegram_field` so that the same .env file can
    # be shared with ArkadyJarvis (same Bitrix portal, same UF_USR).
    bitrix_telegram_field: str = "UF_USR_1678964886664"
    bitrix_email_guests_scan_max: int = 2000
    bitrix_email_guests_multiplier: int = 3

    # Jira (integration user)
    jira_url: str = ""
    jira_username: str = ""
    jira_password: SecretStr = SecretStr("")

    # OpenClaw (Glafira)
    openclaw_url: str = ""
    openclaw_token: SecretStr = SecretStr("")
    openclaw_agent_id: str = "main"

    # Potok.io (Recruiter Anatoly)
    potok_api_token: SecretStr = SecretStr("")
    potok_base_url: str = "https://app.potok.io"

    # Claude CLI
    claude_cli_path: str = "claude"
    claude_model: str = ""
    claude_oauth_client_id: str = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
    claude_code_oauth_token: str = ""
    claude_refresh_token: str = ""

    # Webhook
    webhook_token: str = ""

    # Access control (comma-separated MAX user IDs)
    glafira_allowed: str = ""
    recruiter_allowed: str = ""

    # Database
    db_path: str = "data/arkadyjarvismax.db"

    # Scheduler
    summary_hour: int = 19
    summary_minute: int = 0
    timezone: str = "Asia/Novosibirsk"

    # Wednesday frog meme — 0 = disabled.
    wednesday_frog_chat_id: int = 0
    # Monday motivational poster — 0 = disabled.
    monday_poster_chat_id: int = 0

    # Socrates meeting analyser
    ffmpeg_bin: str = "ffmpeg"
    meeting_max_minutes: int = 90

    @field_validator("summary_hour")
    @classmethod
    def validate_summary_hour(cls, v: int) -> int:
        if not 0 <= v <= 23:
            raise ValueError(f"summary_hour must be 0–23, got {v}")
        return v

    @field_validator("summary_minute")
    @classmethod
    def validate_summary_minute(cls, v: int) -> int:
        if not 0 <= v <= 59:
            raise ValueError(f"summary_minute must be 0–59, got {v}")
        return v

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (KeyError, Exception):
            raise ValueError(f"Invalid IANA timezone: {v}")
        return v

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        # Tolerate unknown keys in .env — lets us share the same .env with
        # ArkadyJarvis / ArkadyConcierge without splitting configs.
        "extra": "ignore",
    }


settings = Settings()
