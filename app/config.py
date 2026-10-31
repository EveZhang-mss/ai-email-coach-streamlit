from dataclasses import dataclass
import os
from dotenv import load_dotenv
load_dotenv()
@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./ai_email_coach.db")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-5.5")
    pass_threshold: int = int(os.getenv("PASS_THRESHOLD", "80"))
    reminder_after_days: int = int(os.getenv("REMINDER_AFTER_DAYS", "3"))
    email_enabled: bool = os.getenv("EMAIL_ENABLED", "false").lower() == "true"
    email_address: str = os.getenv("EMAIL_ADDRESS", "")
    email_auth_code: str = os.getenv("EMAIL_AUTH_CODE", "")
    imap_host: str = os.getenv("IMAP_HOST", "imap.qq.com")
    imap_port: int = int(os.getenv("IMAP_PORT", "993"))
    smtp_host: str = os.getenv("SMTP_HOST", "smtp.qq.com")
    smtp_port: int = int(os.getenv("SMTP_PORT", "465"))
settings = Settings()