from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App settings, read from environment variables or a .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://tracker:tracker@localhost:5432/tracker"
    telegram_bot_token: str = ""
    # Telegram echoes this back in a header on every webhook call, so we can
    # reject requests that didn't come from Telegram.
    telegram_webhook_secret: str = ""
    # Public HTTPS address, used for page links in messages and the webhook.
    public_base_url: str = "http://localhost:8000"
    bot_username: str = ""
    user_agent: str = "MYPriceTracker/1.0 (+https://github.com/hkm4lmn/my-price-tracker)"


settings = Settings()
