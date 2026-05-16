from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Internal bridge URL (Docker service name)
    bridge_url: str = "http://bridge:3000"

    # Database (SQLite file on persistent volume)
    database_url: str = "sqlite:////data/invoice_curator.db"

    # Cloudflare R2 (S3-compatible)
    r2_endpoint: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = ""
    r2_public_url: str = ""  # optional CDN URL prefix

    # Google Gemini (vision / OCR only)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-1.5-pro"

    # Anthropic Claude (agent reasoning, tool use)
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-6"

    # Gmail SMTP
    gmail_user: str = ""
    gmail_app_password: str = ""

    # Comma-separated list of email addresses that the bot is allowed to send
    # reports to. If empty, falls back to GMAIL_USER (send only to self).
    # Example: "boss@company.com,accountant@company.com"
    report_email_allowlist: str = ""

    # App behaviour
    image_max_px: int = 1920
    image_jpeg_quality: int = 85
    confidence_flag_threshold: float = 0.6  # flag invoices below this score

    # WhatsApp platform
    bot_phone_number: str = ""
    admin_phone_number: str = ""
    legacy_group_jid: str = ""

    # Notion
    notion_api_key: str = ""
    notion_tasks_database_id: str = ""

    # Family accounting: JSON object mapping display name → phone
    # Example: '{"Eran": "972501234567", "Dana": "972509876543"}'
    family_members_json: str = ""


settings = Settings()
