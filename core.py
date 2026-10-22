"""Configuration, database setup, translations, and schema compatibility."""
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from typing import Literal
import logging
from sqlalchemy import Boolean, Column, Integer, String, column, func, inspect, select, table, update

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Dedikka API"
    app_env: str = "development"
    api_prefix: str = "/api/v1"
    database_url: str = "sqlite:///./dedicapag.db"
    supabase_url: str = ""
    supabase_secret_key: str = ""
    supabase_bucket: str = "dedikka-images"
    public_backend_url: str = ""
    public_frontend_url: str = ""
    google_client_id: str = "1062493416051-ffjip651gosh7ddmobjlr292g2o1iiju.apps.googleusercontent.com"
    upload_dir: str = "uploads"
    frontend_origins: list[str] = [
        "http://127.0.0.1:5500", "http://localhost:5500",
        "http://127.0.0.1:8000", "http://localhost:8000",
        "http://127.0.0.1:8001", "http://localhost:8001",
        "https://dedikka.web.app",
        "https://dedikka.onrender.com",
    ]
    session_cookie_name: str = "dedicapag_session"
    csrf_cookie_name: str = "dedicapag_csrf"
    cookie_secure: bool = False
    session_days: int = 7
    free_page_duration_hours: int = Field(default=12, gt=0)
    page_expiration_reminder_minutes: int = Field(default=60, gt=0)
    rate_limit_login_attempts: int = 5
    rate_limit_window_seconds: int = 300
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_use_ssl: bool = False
    smtp_starttls: bool = True
    email_api_url: str = ""
    email_api_key: str = ""
    pywagate_base_url: str = ""
    pywagate_api_key: str = ""
    pywagate_session_id: str = "default"
    # Passerelle de paiement Chariow & Contrôle Admin
    chariow_base_url: str = "https://api.chariow.com/v1"
    chariow_api_key: str = ""
    chariow_webhook_secret: str = ""
    payments_enabled: bool = True  # Interrupteur général (Activé / Désactivé pour promotions)
    # Comma-separated allowlist of accounts permitted to use the admin API.
    admin_emails: str = ""

    @field_validator("public_frontend_url", mode="before")
    @classmethod
    def select_one_public_frontend_url(cls, value):
        """Accept legacy list/CSV env values but store one canonical public URL."""
        if isinstance(value, (list, tuple)):
            candidates = [str(item).strip() for item in value]
        else:
            raw = str(value or "").strip()
            candidates = []
            if raw.startswith("["):
                try:
                    decoded = json.loads(raw)
                    candidates = [str(item).strip() for item in decoded] if isinstance(decoded, list) else [raw]
                except (json.JSONDecodeError, TypeError):
                    candidates = [raw]
            elif "," in raw:
                candidates = [part.strip().strip("\"'") for part in raw.split(",")]
            elif raw:
                candidates = [raw]

        for candidate in candidates:
            try:
                parsed = urlsplit(candidate)
            except ValueError:
                continue
            if parsed.scheme == "https" and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}"
        for candidate in candidates:
            try:
                parsed = urlsplit(candidate)
            except ValueError:
                continue
            if parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}:
                return f"{parsed.scheme}://{parsed.netloc}"
        return ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()

class Base(DeclarativeBase):
    pass


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db():
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()

Lang = Literal["fr"]

TRANSLATIONS: dict[str, dict[Lang, str]] = {
    "csrf_invalid": {
        "fr": "Jeton CSRF absent ou invalide.",
    },
    "password_mismatch": {
        "fr": "Les mots de passe ne correspondent pas.",
    },
    "email_in_use": {
        "fr": "Cette adresse e-mail est déjà utilisée.",
    },
    "invalid_credentials": {
        "fr": "E-mail ou mot de passe incorrect.",
    },
    "auth_required": {
        "fr": "Connexion requise.",
    },
    "rate_limited": {
        "fr": "Trop de tentatives. Veuillez patienter un instant.",
    },
    "image_format_unsupported": {
        "fr": "Ce format d’image n’est pas pris en charge. Utilisez une photo JPEG, PNG, WebP, GIF, HEIC, HEIF ou AVIF.",
    },
    "image_too_large": {
        "fr": "Image limitée à 8 Mo.",
    },
    "image_invalid_content": {
        "fr": "Le contenu du fichier ne correspond pas à une image valide.",
    },
    "image_webp_invalid": {
        "fr": "Fichier WebP invalide.",
    },
    "storage_not_configured": {
        "fr": "Stockage Supabase non configuré. Renseigne SUPABASE_URL et SUPABASE_SECRET_KEY dans Render.",
    },
    "storage_upload_failed": {
        "fr": "Échec de l’envoi de l’image vers l’espace de stockage.",
    },
    "storage_unreachable": {
        "fr": "L’espace de stockage est actuellement inaccessible.",
    },
    "page_data_too_large": {
        "fr": "Les données de la page sont trop volumineuses.",
    },
    "image_ownership_invalid": {
        "fr": "Une image ne correspond pas à votre espace de stockage.",
    },
    "slug_in_use": {
        "fr": "Ce lien personnalisé est déjà utilisé.",
    },
    "page_not_found": {
        "fr": "Cette dédicace n’existe pas.",
    },
    "page_forbidden": {
        "fr": "Vous n’êtes pas autorisé à modifier cette dédicace.",
    },
    "page_deleted": {
        "fr": "Dédicace supprimée avec succès.",
    },
    "profile_updated": {
        "fr": "Profil mis à jour avec succès.",
    },
}


def get_lang_from_header(accept_language: str | None) -> Lang:
    """The product currently serves French only, regardless of browser locale."""
    return "fr"


def translate(key: str, lang: Lang = "fr") -> str:
    """Returns the translation for the given key and language code."""
    entry = TRANSLATIONS.get(key)
    if not entry:
        return key
    return entry.get(lang, entry.get("fr", key))


# Table officielle des forfaits (inviolable, seul le backend fait autorité)
OFFICIAL_PLANS = {
    "starter": {
        "name": "Starter",
        "price_usd": 2.0,
        "price_cdf": 4500,
        "pages_included": 1,
        "duration_days": 365,
        "product_id": "prd_ch61w9ly",
        "max_photos": 4,
        "allow_custom_mp3": False,
        "allow_custom_link": False,
        "allow_qr": False,
    },
    "pro": {
        "name": "Pro",
        "price_usd": 5.0,
        "price_cdf": 9000,
        "pages_included": 3,
        "duration_days": 365,
        "product_id": "prd_94m17lir",
        "max_photos": 12,
        "allow_custom_mp3": True,
        "allow_custom_link": True,
        "allow_qr": True,
    },
    "premium": {
        "name": "Premium",
        "price_usd": 10.0,
        "price_cdf": 23000,
        "pages_included": 6,
        "duration_days": 365,
        "product_id": "prd_0b1tq081",
        "max_photos": 999,
        "allow_custom_mp3": True,
        "allow_custom_link": True,
        "allow_qr": True,
    },
    "mensuel": {
        "name": "Mensuel",
        "price_usd": 3.0,
        "price_cdf": 7500,
        "pages_included": 3,
        "duration_days": 30,
        "product_id": "prd_34bbrza9",
        "max_photos": 12,
        "allow_custom_mp3": True,
        "allow_custom_link": True,
        "allow_qr": True,
    },
}

try:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
except ImportError:
    MigrationContext = None
    Operations = None



logger = logging.getLogger(__name__)


def ensure_schema_compatibility():
    """Bring older dedication tables up to the columns expected by the ORM."""
    if Operations is None:
        return

    schema = "public" if engine.dialect.name == "postgresql" else None

    with engine.begin() as connection:
        inspector = inspect(connection)
        operations = Operations(MigrationContext.configure(connection))

        if inspector.has_table("dedications", schema=schema):
            existing = {
                item["name"]
                for item in inspector.get_columns("dedications", schema=schema)
            }
            if "template_name" not in existing:
                operations.add_column(
                    "dedications",
                    Column(
                        "template_name",
                        String(50),
                        nullable=False,
                        server_default="'dedicate'",
                    ),
                    schema=schema,
                )
                logger.info("Applied migration: dedications.template_name")

            if "updated_at" not in existing:
                operations.add_column(
                    "dedications",
                    Column("updated_at", Integer, nullable=False, server_default="0"),
                    schema=schema,
                )
                dedications = table(
                    "dedications",
                    column("updated_at", Integer),
                    column("created_at", Integer),
                    schema=schema,
                )
                connection.execute(
                    update(dedications)
                    .where(dedications.c.updated_at == 0)
                    .values(updated_at=dedications.c.created_at)
                )
                logger.info("Applied migration: dedications.updated_at")

            if "expires_at" not in existing:
                operations.add_column(
                    "dedications",
                    Column("expires_at", Integer, nullable=True),
                    schema=schema,
                )
                logger.info("Applied migration: dedications.expires_at")

            if "is_free" not in existing:
                operations.add_column(
                    "dedications",
                    Column("is_free", Boolean, nullable=False, server_default="true"),
                    schema=schema,
                )
                logger.info("Applied migration: dedications.is_free")

            entitlement_columns = {
                "plan_key": (String(30), "'legacy'"),
                "max_photos": (Integer, "21"),
                "allow_custom_mp3": (Boolean, "false"),
                "allow_custom_link": (Boolean, "true"),
                "allow_qr": (Boolean, "true"),
            }
            for column_name, (column_type, default_value) in entitlement_columns.items():
                if column_name not in existing:
                    operations.add_column(
                        "dedications",
                        Column(column_name, column_type, nullable=False, server_default=default_value),
                        schema=schema,
                    )
                    logger.info("Applied migration: dedications.%s", column_name)

            if "expiration_reminder_sent_at" not in existing:
                operations.add_column(
                    "dedications",
                    Column("expiration_reminder_sent_at", Integer, nullable=True),
                    schema=schema,
                )
                logger.info("Applied migration: dedications.expiration_reminder_sent_at")

            existing_indexes = {
                item["name"] for item in inspector.get_indexes("dedications", schema=schema)
            }
            if "ix_dedications_expires_at" not in existing_indexes:
                operations.create_index(
                    "ix_dedications_expires_at",
                    "dedications",
                    ["expires_at"],
                    schema=schema,
                )
                logger.info("Applied migration: index on dedications.expires_at")

        if inspector.has_table("users", schema=schema):
            user_columns = {
                item["name"] for item in inspector.get_columns("users", schema=schema)
            }
            if "is_blocked" not in user_columns:
                operations.add_column(
                    "users",
                    Column("is_blocked", Boolean, nullable=False, server_default="false"),
                    schema=schema,
                )
                logger.info("Applied migration: users.is_blocked")

            if "whatsapp_number" not in user_columns:
                operations.add_column(
                    "users",
                    Column("whatsapp_number", String(16), nullable=True),
                    schema=schema,
                )
                logger.info("Applied migration: users.whatsapp_number")

            if "google_sub" not in user_columns:
                operations.add_column(
                    "users",
                    Column("google_sub", String(255), nullable=True),
                    schema=schema,
                )
                logger.info("Applied migration: users.google_sub")

            existing_user_indexes = {
                item["name"] for item in inspector.get_indexes("users", schema=schema)
            }
            if "ix_users_google_sub" not in existing_user_indexes:
                operations.create_index(
                    "ix_users_google_sub",
                    "users",
                    ["google_sub"],
                    unique=True,
                    schema=schema,
                )
                logger.info("Applied migration: unique index on users.google_sub")

            if "paid_pages_balance" not in user_columns:
                operations.add_column(
                    "users",
                    Column("paid_pages_balance", Integer, nullable=False, server_default="0"),
                    schema=schema,
                )
                logger.info("Applied migration: users.paid_pages_balance")

            free_quota_migration = "free_pages_period" not in user_columns or "free_pages_used" not in user_columns
            if "free_pages_period" not in user_columns:
                operations.add_column(
                    "users",
                    Column("free_pages_period", String(7), nullable=False, server_default=""),
                    schema=schema,
                )
                logger.info("Applied migration: users.free_pages_period")
            if "free_pages_used" not in user_columns:
                operations.add_column(
                    "users",
                    Column("free_pages_used", Integer, nullable=False, server_default="0"),
                    schema=schema,
                )
                logger.info("Applied migration: users.free_pages_used")

            if free_quota_migration and inspector.has_table("dedications", schema=schema):
                now = datetime.now(timezone.utc)
                period = now.strftime("%Y-%m")
                month_start = int(datetime(now.year, now.month, 1, tzinfo=timezone.utc).timestamp())
                users = table("users", column("id", Integer), column("free_pages_period", String(7)), column("free_pages_used", Integer), schema=schema)
                dedications = table("dedications", column("user_id", Integer), column("created_at", Integer), column("is_free", Boolean), schema=schema)
                for row in connection.execute(select(users.c.id)):
                    used = connection.scalar(
                        select(func.count()).select_from(dedications).where(
                            dedications.c.user_id == row.id,
                            dedications.c.is_free.is_(True),
                            dedications.c.created_at >= month_start,
                        )
                    ) or 0
                    connection.execute(
                        update(users).where(users.c.id == row.id).values(
                            free_pages_period=period,
                            free_pages_used=used,
                        )
                    )
                logger.info("Initialized current-month free-page quotas from existing pages")
