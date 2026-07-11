"""TSR Enterprises - application settings loaded from environment variables."""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


class Settings:
    APP_NAME = "TSR Enterprises"
    APP_SUBTITLE = "Fleet & Finance Management"

    # Google OAuth
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback")

    # Sessions
    SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")
    SESSION_MAX_AGE = int(os.getenv("SESSION_MAX_AGE", "86400"))

    # PostgreSQL
    DATABASE_URL = os.getenv("DATABASE_URL", "")

    # Google Drive
    GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
    SERVICE_ACCOUNT_FILE = os.getenv("SERVICE_ACCOUNT_FILE", "service_account.json")
    GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "")

    # Access control - comma separated list of emails allowed to bootstrap as admin
    ALLOWED_EMAILS = [e.strip().lower() for e in os.getenv("ALLOWED_EMAILS", "").split(",") if e.strip()]

    # Driver OTP SMS (legacy fallback when Firebase is not configured)
    FAST2SMS_API_KEY = os.getenv("FAST2SMS_API_KEY", "")

    # Firebase Phone Authentication (driver OTP login)
    FIREBASE_API_KEY = os.getenv("FIREBASE_API_KEY", "")
    FIREBASE_AUTH_DOMAIN = os.getenv("FIREBASE_AUTH_DOMAIN", "")
    FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "")


settings = Settings()
