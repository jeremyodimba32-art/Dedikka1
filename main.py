"""FastAPI application entry point and dedication page routes."""

from contextlib import asynccontextmanager

import asyncio
import httpx

import logging

logger = logging.getLogger("dedikka")

from pathlib import Path

import re

from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query

from fastapi.middleware.cors import CORSMiddleware

from starlette.middleware.gzip import GZipMiddleware

from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from fastapi.staticfiles import StaticFiles

import html

import hashlib

import hmac

import json

import re

import time

import unicodedata

import uuid

from pathlib import Path

from collections import defaultdict

from typing import Annotated, Literal

from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Cookie, Depends, File, Header, HTTPException, Request, UploadFile


from pydantic import BaseModel, Field

from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String, case, func, or_, select, update

from sqlalchemy.exc import IntegrityError

from sqlalchemy.orm import Mapped, Session, mapped_column



from accounts import User, UserSession, _require_csrf, current_user, router as accounts_router

from core import Base, SessionLocal, engine, ensure_schema_compatibility, get_db, get_lang_from_header, settings, translate

from services import generate_heart_qr, is_valid_user_audio, is_valid_user_image, save_audio, save_image





router = APIRouter(tags=["pages"])

public_router = APIRouter(tags=["published-pages"])

_page_code_attempts: dict[str, list[float]] = defaultdict(list)

AVAILABLE_TEMPLATE_NAMES = frozenset({
    "dedicate",
    "el-camino-juntos",
    "galaxia-infinita",
    "galaxy",
    "galaxy-gallery",
    "galaxy-of-love",
    "heart",
    "heart-shooter",
    "latidos-de-luz",
    "lector-huellas",
    "memori-corazon",
    "net",
    "propose",
    "puertas-del-corazon",
    "spotify-amor",
})

FREE_MONTHLY_PAGE_LIMIT = 3

TEMPLATE_IMAGE_KEYS = {

    "galaxia-infinita": frozenset({"image_1", "image_2", "image_3"}),

    "puertas-del-corazon": frozenset({"image_1", "image_2", "image_3", "image_4", "image_5"}),

    # The last two photos belong to the timeline fields in this form.

    "lector-huellas": frozenset({"image_1", "image_2", "image_3", "image_19", "image_20"}),

    "dedicate": frozenset({"cover_image", "image_1", "image_2"}),

    "galaxy-gallery": frozenset({"image_1", "image_2", "image_3", "image_4"}),

    "memori-corazon": frozenset({"image_1", "image_2", "image_3"}),

    "galaxy": frozenset({"image_1", "image_2", "image_3", "image_4"}),

    "spotify-amor": frozenset({"image_1", "image_2", "image_3", "image_4", "image_5", "image_21", "image_22", "image_23"}),

    "spotify": frozenset({"image_1", "image_2", "image_3", "image_4", "image_5", "image_21", "image_22", "image_23"}),

    "net": frozenset({"image_1", "image_2", "image_3", "image_4", "image_5", "image_6", "image_7", "image_8", "image_9"}),

    "netflix": frozenset({"image_1", "image_2", "image_3", "image_4", "image_5", "image_6", "image_7", "image_8", "image_9"}),

    "heart": frozenset(),



    "heart-shooter": frozenset({"image_1"}),

    "el-camino-juntos": frozenset({"image_1"}),

    "propose": frozenset({"image_1", "image_2"}),

    "flor-galaxy": frozenset({"image_1", "image_2", "image_3", "image_4"}),

    "galaxy-of-love": frozenset({"image_1", "image_2", "image_3", "image_4"}),

    "latidos-de-luz": frozenset({"image_1", "image_2", "image_3", "image_4", "image_5", "image_6"}),

}

TEMPLATE_IMAGE_LIMITS = {name: len(keys) for name, keys in TEMPLATE_IMAGE_KEYS.items()}

TEMPLATE_FILES = {

    "dedicate": "Special Dedication.html",

    "lector-huellas": "lector-huellas.html",

    "memori-corazon": "memori-corazon.html",

    "galaxy-gallery": "galaxy-gallery.html",

    "galaxia-infinita": "galaxia-infinita-3d.html",

    "galaxy": "fleur galaxy.html",

    "fleur-galaxy": "fleur galaxy.html",

    "flor-galaxy": "fleur galaxy.html",

    "universe": "galaxia-infinita-3d.html",

    "puertas-del-corazon": "porte du cieux.html",

    "spotify-amor": "spotify-amor.html",

    "spotify": "spotify-amor.html",

    "net": "net.html",

    "netflix": "net.html",

    "heart": "heart.html",



    "heart-shooter": "heart-shooter.html",

    "el-camino-juntos": "el-camino-juntos.html",

    "propose": "propose.html",

    "galaxy-of-love": "galaxy-of-love.html",

    "latidos-de-luz": "beats-of-light.html",

}





def _require_available_template(template_name: str) -> str:

    normalized = template_name.strip().lower() or "dedicate"

    normalized = {
        "fleur-galaxy": "galaxy",
        "flor-galaxy": "galaxy",
        "spotify": "spotify-amor",
        "netflix": "net",
    }.get(normalized, normalized)

    if normalized not in AVAILABLE_TEMPLATE_NAMES:

        raise HTTPException(status_code=422, detail="Ce modèle n’est pas encore disponible.")

    return normalized





def _validate_template_images(

    template_name: str,

    images: dict[str, str],

    user_id: int,

    base_url: str,

    max_photos: int | None = None,

    allow_extra_photos: bool = False,

) -> None:

    allowed_keys = TEMPLATE_IMAGE_KEYS[template_name]

    limit = max_photos if max_photos is not None else TEMPLATE_IMAGE_LIMITS[template_name]

    if len(images) > limit:

        raise HTTPException(status_code=422, detail=f"Cette formule autorise {limit} photos au maximum pour une dédicace.")

    for key, url in images.items():

        is_extra = bool(re.fullmatch(r"extra_image_\d{1,3}", key))

        if key not in allowed_keys and not is_extra:

            raise HTTPException(status_code=422, detail="Une clé photo ne correspond pas à ce modèle.")

        if is_extra and not allow_extra_photos:

            raise HTTPException(status_code=403, detail="Les photos supplémentaires nécessitent un crédit de formule.")

        if len(url) > 2048 or url.startswith("data:image/") or not is_valid_user_image(url, user_id, base_url):

            raise HTTPException(status_code=422, detail=translate("image_ownership_invalid", "fr"))





class PageCodeInput(BaseModel):

    code: str = Field(min_length=1, max_length=32)





@router.post("/public/pages/{slug}/verify-code", tags=["published-pages"])

def verify_page_code(slug: str, payload: PageCodeInput, request: Request, db: Session = Depends(get_db)):

    """Verify a protected-page code without ever sending the expected code to the browser."""

    page = db.scalar(select(Dedication).where(Dedication.slug == slug))

    if page is None:

        raise HTTPException(status_code=404, detail="Page introuvable")

    _require_active_page(page)



    forwarded = request.headers.get("x-forwarded-for")
    client_ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")

    key = f"{client_ip}:{slug}"

    now = time.time()

    attempts = [stamp for stamp in _page_code_attempts[key] if now - stamp < 300]

    _page_code_attempts[key] = attempts

    if len(attempts) >= 10:

        raise HTTPException(status_code=429, detail="Trop de tentatives. Réessayez plus tard.")



    expected = str(

        page.form_fields.get("page_password")

        or page.form_fields.get("data[page_password]")

        or "0000"

    )

    if not hmac.compare_digest(payload.code, expected):

        attempts.append(now)

        raise HTTPException(status_code=401, detail="Code incorrect.")

    _page_code_attempts.pop(key, None)

    return {"verified": True}





class Dedication(Base):

    __tablename__ = "dedications"



    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)

    title: Mapped[str] = mapped_column(String(120), nullable=False)

    recipient_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")

    template_name: Mapped[str] = mapped_column(String(50), nullable=False, default="dedicate")

    form_fields: Mapped[dict] = mapped_column(JSON, nullable=False)

    images: Mapped[dict] = mapped_column(JSON, nullable=False)

    created_at: Mapped[int] = mapped_column(Integer, nullable=False)

    updated_at: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    is_free: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    expires_at: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    expiration_reminder_sent_at: Mapped[int | None] = mapped_column(Integer, nullable=True)

    plan_key: Mapped[str] = mapped_column(String(30), nullable=False, default="legacy")

    max_photos: Mapped[int] = mapped_column(Integer, nullable=False, default=21)

    allow_custom_mp3: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    allow_custom_link: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    allow_qr: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)





class PlanCreditGrant(Base):

    """A purchase grant preserves its plan rights as pages are consumed."""

    __tablename__ = "plan_credit_grants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)

    plan_key: Mapped[str] = mapped_column(String(30), nullable=False)

    pages_granted: Mapped[int] = mapped_column(Integer, nullable=False)

    pages_remaining: Mapped[int] = mapped_column(Integer, nullable=False)

    page_duration_days: Mapped[int] = mapped_column(Integer, nullable=False, default=365)

    max_photos: Mapped[int] = mapped_column(Integer, nullable=False)

    allow_custom_mp3: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    allow_custom_link: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    allow_qr: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    expires_at: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)





class AppSetting(Base):

    """Small persistent key/value store for server-controlled switches."""

    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    key: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)

    value: Mapped[str] = mapped_column(String, nullable=False, default="")

    description: Mapped[str | None] = mapped_column(String, nullable=True)

    updated_at: Mapped[int] = mapped_column(Integer, nullable=False, default=0)





class AdminAuditLog(Base):

    __tablename__ = "admin_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    actor_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)

    target_type: Mapped[str] = mapped_column(String(40), nullable=False)

    target_id: Mapped[str] = mapped_column(String(80), nullable=False, default="")

    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[int] = mapped_column(Integer, nullable=False, index=True)





class AdminEmailCampaign(Base):

    __tablename__ = "admin_email_campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    subject: Mapped[str] = mapped_column(String(180), nullable=False)

    body: Mapped[str] = mapped_column(String, nullable=False)

    created_by: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    created_at: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")

    recipient_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    sent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)





class ChariowPulseDelivery(Base):

    """Signed Chariow deliveries credited at most once per delivery and sale."""

    __tablename__ = "chariow_pulse_deliveries"

    delivery_id: Mapped[str] = mapped_column(String(255), primary_key=True)

    sale_id: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)

    pulse_id: Mapped[str] = mapped_column(String(160), nullable=False)

    product_id: Mapped[str] = mapped_column(String(160), nullable=False)

    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    pages_credited: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[int] = mapped_column(Integer, nullable=False, index=True)





class PublishInput(BaseModel):

    title: str = Field(min_length=1, max_length=120)

    recipient_name: str = Field(default="", max_length=100)

    template_name: str = Field(default="dedicate", max_length=50)

    form_fields: dict[str, str] = Field(default_factory=dict)

    images: dict[str, str] = Field(default_factory=dict)

    custom_slug: str = Field(default="", max_length=80)





class UpdatePageInput(BaseModel):

    title: str | None = Field(default=None, min_length=1, max_length=120)

    recipient_name: str | None = Field(default=None, max_length=100)

    template_name: str | None = Field(default=None, max_length=50)

    form_fields: dict[str, str] | None = None

    images: dict[str, str] | None = None

    custom_slug: str | None = Field(default=None, max_length=80)





class AdminCreatePageInput(PublishInput):

    user_id: int = Field(gt=0)

    grant_source: Literal["credit", "manual_payment", "gift"] = "credit"

    reason: str = Field(min_length=3, max_length=250)

    payment_reference: str = Field(default="", max_length=120)

    plan: Literal["starter", "pro", "premium", "mensuel"] = "starter"





class AdminActivatePageInput(BaseModel):

    grant_source: Literal["credit", "manual_payment", "gift"]

    reason: str = Field(min_length=3, max_length=250)

    payment_reference: str = Field(default="", max_length=120)

    plan: Literal["starter", "pro", "premium", "mensuel"] = "starter"





class AdminCreditInput(BaseModel):

    amount: int = Field(ge=1, le=50)

    plan: Literal["starter", "pro", "premium", "mensuel"] = "starter"

    reason: str = Field(min_length=3, max_length=250)


class AdminUserBlockInput(BaseModel):

    blocked: bool

    reason: str = Field(default="Modification du statut du compte par l’administration", max_length=250)





class AdminPaymentsInput(BaseModel):

    enabled: bool

    reason: str = Field(default="", max_length=250)





class AdminAnnouncementInput(BaseModel):

    subject: str = Field(min_length=3, max_length=180)

    body: str = Field(min_length=3, max_length=8000)






def _field(fields: dict, name: str, default: str = "") -> str:

    return str(fields.get(f"data[{name}]", fields.get(name, default)) or default)





def _slugify(value: str) -> str:

    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()

    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")

    return value[:65].strip("-") or "dedication"





def _published_url(request: Request, slug: str) -> str:

    """Build a public URL on the frontend, which owns page presentation."""

    origin = _frontend_origin(request)

    if origin:

        return f"{origin}/p/{quote(slug)}"



    backend = settings.public_backend_url.rstrip("/") or str(request.base_url).rstrip("/")

    return f"{backend}/p/{quote(slug)}"





def _require_active_page(page: Dedication) -> None:

    if page.expires_at is not None and page.expires_at <= int(time.time()):

        raise HTTPException(status_code=410, detail="Cette dédicace gratuite a expiré.")





def _require_admin(user: User) -> User:

    allowed = {

        address.strip().lower()

        for address in settings.admin_emails.replace(";", ",").split(",")

        if address.strip()

    }

    if not allowed:

        raise HTTPException(status_code=503, detail="L’accès administrateur n’est pas configuré sur le serveur.")

    if user.email.strip().lower() not in allowed:

        raise HTTPException(status_code=403, detail="Accès administrateur refusé.")

    return user





def _grant_plan_credits(db: Session, user: User, plan_key: str, amount: int, created_at: int | None = None) -> PlanCreditGrant:

    from core import OFFICIAL_PLANS



    key = plan_key if plan_key in OFFICIAL_PLANS else "starter"

    plan = OFFICIAL_PLANS[key]

    now = int(created_at or time.time())

    expires_at = now + int(plan["duration_days"]) * 86400

    grant = PlanCreditGrant(

        user_id=user.id,

        plan_key=key,

        pages_granted=amount,

        pages_remaining=amount,

        page_duration_days=int(plan["duration_days"]),

        max_photos=int(plan["max_photos"]),

        allow_custom_mp3=bool(plan["allow_custom_mp3"]),

        allow_custom_link=bool(plan["allow_custom_link"]),

        allow_qr=bool(plan["allow_qr"]),

        created_at=now,

        expires_at=expires_at,

    )

    db.add(grant)

    db.execute(

        update(User)

        .where(User.id == user.id)

        .values(paid_pages_balance=User.paid_pages_balance + amount)

        .execution_options(synchronize_session=False)

    )

    db.expire(user, ["paid_pages_balance"])

    return grant





def _expire_plan_credits(db: Session, user_id: int | None = None, now: int | None = None) -> None:

    current = int(now or time.time())

    stmt = select(PlanCreditGrant).where(

        PlanCreditGrant.pages_remaining > 0,

        PlanCreditGrant.expires_at.is_not(None),

        PlanCreditGrant.expires_at <= current,

    )

    if user_id is not None:

        stmt = stmt.where(PlanCreditGrant.user_id == user_id)

    for grant in db.scalars(stmt).all():

        remaining = grant.pages_remaining

        changed = db.execute(

            update(PlanCreditGrant)

            .where(PlanCreditGrant.id == grant.id, PlanCreditGrant.pages_remaining == remaining)

            .values(pages_remaining=0)

            .execution_options(synchronize_session=False)

        )

        if changed.rowcount == 1:

            db.execute(

                update(User)

                .where(User.id == grant.user_id, User.paid_pages_balance >= remaining)

                .values(paid_pages_balance=User.paid_pages_balance - remaining)

                .execution_options(synchronize_session=False)

            )





def _ensure_legacy_credit_grant(db: Session, user: User) -> None:

    """Preserve older untyped balances as legacy credits instead of losing them."""

    outstanding = db.scalar(

        select(func.coalesce(func.sum(PlanCreditGrant.pages_remaining), 0)).where(

            PlanCreditGrant.user_id == user.id,

            PlanCreditGrant.pages_remaining > 0,

            or_(PlanCreditGrant.expires_at.is_(None), PlanCreditGrant.expires_at > int(time.time())),

        )

    ) or 0

    missing = max(0, int(user.paid_pages_balance) - int(outstanding))

    if not missing:

        return

    legacy = PlanCreditGrant(

        user_id=user.id,

        plan_key="legacy",

        pages_granted=missing,

        pages_remaining=missing,

        page_duration_days=365,

        max_photos=21,

        allow_custom_mp3=False,

        allow_custom_link=True,

        allow_qr=True,

        created_at=0,

        expires_at=None,

    )

    db.add(legacy)

    db.flush()





def _next_plan_credit(db: Session, user_id: int) -> PlanCreditGrant | None:

    now = int(time.time())

    return db.scalar(

        select(PlanCreditGrant)

        .where(

            PlanCreditGrant.user_id == user_id,

            PlanCreditGrant.pages_remaining > 0,

            or_(PlanCreditGrant.expires_at.is_(None), PlanCreditGrant.expires_at > now),

        )

        .order_by(PlanCreditGrant.created_at.asc(), PlanCreditGrant.id.asc())

        .limit(1)

    )





def _free_quota_state(user: User, now: int | None = None) -> dict:

    current = int(now or time.time())

    period = time.strftime("%Y-%m", time.gmtime(current))

    used = int(user.free_pages_used) if user.free_pages_period == period else 0

    return {

        "free_pages_limit": FREE_MONTHLY_PAGE_LIMIT,

        "free_pages_used": used,

        "free_pages_remaining": max(0, FREE_MONTHLY_PAGE_LIMIT - used),

        "free_page_duration_hours": settings.free_page_duration_hours,

        "free_pages_period": period,

    }





def _consume_free_page_slot(db: Session, user: User) -> dict:

    period = time.strftime("%Y-%m", time.gmtime())

    result = db.execute(

        update(User)

        .where(

            User.id == user.id,

            or_(User.free_pages_period != period, User.free_pages_used < FREE_MONTHLY_PAGE_LIMIT),

        )

        .values(

            free_pages_period=period,

            free_pages_used=case(

                (User.free_pages_period == period, User.free_pages_used + 1),

                else_=1,

            ),

        )

        .execution_options(synchronize_session=False)

    )

    if result.rowcount != 1:

        raise HTTPException(status_code=429, detail="Tu as atteint les 3 pages gratuites de ce mois. Choisis une formule pour en créer davantage.")

    db.refresh(user, attribute_names=["free_pages_period", "free_pages_used"])

    return _free_quota_state(user)





def _consume_plan_credit(db: Session, user: User) -> dict | None:

    _expire_plan_credits(db, user.id)

    db.refresh(user, attribute_names=["paid_pages_balance"])

    _ensure_legacy_credit_grant(db, user)

    grants = db.scalars(

        select(PlanCreditGrant)

        .where(PlanCreditGrant.user_id == user.id, PlanCreditGrant.pages_remaining > 0)

        .order_by(PlanCreditGrant.created_at.asc(), PlanCreditGrant.id.asc())

    ).all()

    for grant in grants:

        changed = db.execute(

            update(PlanCreditGrant)

            .where(PlanCreditGrant.id == grant.id, PlanCreditGrant.pages_remaining > 0)

            .values(pages_remaining=PlanCreditGrant.pages_remaining - 1)

            .execution_options(synchronize_session=False)

        )

        if changed.rowcount != 1:

            continue

        debited = db.execute(

            update(User)

            .where(User.id == user.id, User.paid_pages_balance > 0)

            .values(paid_pages_balance=User.paid_pages_balance - 1)

            .execution_options(synchronize_session=False)

        )

        if debited.rowcount != 1:

            raise HTTPException(status_code=409, detail="Le solde de crédits a changé. Actualise la page et réessaie.")

        return {

            "plan_key": grant.plan_key,

            "max_photos": grant.max_photos,

            "page_duration_days": grant.page_duration_days,

            "allow_custom_mp3": grant.allow_custom_mp3,

            "allow_custom_link": grant.allow_custom_link,

            "allow_qr": grant.allow_qr,

        }

    return None





def _user_entitlements(db: Session, user: User) -> dict:

    if not _payments_are_enabled(db):

        return {

            **_promotion_rights(),

            "paid_pages_balance": int(user.paid_pages_balance),

            **_free_quota_state(user),

        }

    _expire_plan_credits(db, user.id)

    db.refresh(user, attribute_names=["paid_pages_balance"])

    _ensure_legacy_credit_grant(db, user)

    grant = _next_plan_credit(db, user.id)

    db.flush()

    free_quota = _free_quota_state(user)

    if grant is None:

        return {

            "plan_key": "free", "paid_pages_balance": user.paid_pages_balance,

            "max_photos": 0, "allow_custom_mp3": False,

            "allow_custom_link": False, "allow_qr": False,

            **free_quota,

        }

    return {

        "plan_key": grant.plan_key, "paid_pages_balance": user.paid_pages_balance,

        "max_photos": grant.max_photos, "allow_custom_mp3": grant.allow_custom_mp3,

        "allow_custom_link": grant.allow_custom_link, "allow_qr": grant.allow_qr,

        "credit_expires_at": grant.expires_at,

        **free_quota,

    }





def _photo_limit(template_name: str, rights: dict | None) -> int:

    """Use the plan cap for paid pages and the existing form cap for free pages."""

    plan_limit = int((rights or {}).get("max_photos", 0))

    return plan_limit if plan_limit > 0 else TEMPLATE_IMAGE_LIMITS[template_name]





def _rights_for_plan(plan_key: str) -> dict:

    from core import OFFICIAL_PLANS



    plan = OFFICIAL_PLANS[plan_key]

    return {

        "plan_key": plan_key,

        "max_photos": int(plan["max_photos"]),

        "page_duration_days": int(plan["duration_days"]),

        "allow_custom_mp3": bool(plan["allow_custom_mp3"]),

        "allow_custom_link": bool(plan["allow_custom_link"]),

        "allow_qr": bool(plan["allow_qr"]),

    }





def _promotion_rights() -> dict:

    """Full access during an admin promotion; existing credits are preserved."""

    return {

        "plan_key": "promotion",

        "max_photos": 999,

        "page_duration_days": 365,

        "allow_custom_mp3": True,

        "allow_custom_link": True,

        "allow_qr": True,

        "promo_mode": True,

    }





def _resume_credit_expiry_after_promotion(db: Session, started_at: int, ended_at: int) -> int:

    """Pause the lifetime of unused paid credits for the time promotion was active."""

    grants = db.scalars(

        select(PlanCreditGrant).where(

            PlanCreditGrant.pages_remaining > 0,

            PlanCreditGrant.expires_at.is_not(None),

            PlanCreditGrant.expires_at > started_at,

        )

    ).all()

    extended = 0

    for grant in grants:

        paused_seconds = max(0, ended_at - max(started_at, grant.created_at))

        if not paused_seconds:

            continue

        db.execute(

            update(PlanCreditGrant)

            .where(PlanCreditGrant.id == grant.id, PlanCreditGrant.pages_remaining > 0)

            .values(expires_at=PlanCreditGrant.expires_at + paused_seconds)

            .execution_options(synchronize_session=False)

        )

        extended += 1

    return extended





def _validate_music_url(url: str, user_id: int, base_url: str, allow_custom_mp3: bool) -> None:

    if not url:

        return

    if is_valid_user_audio(url, user_id, base_url):

        if not allow_custom_mp3:

            raise HTTPException(status_code=403, detail="L’envoi de musique personnelle est réservé aux formules Pro, Premium et Mensuel.")

        return

    from urllib.parse import urlsplit

    parsed = urlsplit(url)

    trusted_origins = {settings.public_frontend_url.rstrip("/")} if settings.public_frontend_url else set()

    trusted_origins.update(value.rstrip("/") for value in settings.frontend_origins)

    origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")

    if origin in trusted_origins and parsed.path.startswith(("/assets/music/", "/frontend/assets/music/")) and parsed.path.lower().endswith((".mp3", ".m4a", ".ogg", ".wav")):

        return

    raise HTTPException(status_code=422, detail="La musique doit provenir du catalogue Dedikka ou de ton envoi personnel autorisé.")





def _setting_value(db: Session, key: str, fallback: str) -> str:

    row = db.scalar(select(AppSetting).where(AppSetting.key == key))

    return row.value if row else fallback





def _set_setting(db: Session, key: str, value: str, description: str) -> None:

    row = db.scalar(select(AppSetting).where(AppSetting.key == key))

    if row is None:

        row = AppSetting(key=key, value=value, description=description, updated_at=int(time.time()))

        db.add(row)

    else:

        row.value = value

        row.description = description

        row.updated_at = int(time.time())





def _payments_are_enabled(db: Session) -> bool:

    return _setting_value(db, "payments_enabled", "true" if settings.payments_enabled else "false").lower() == "true"


def _get_maintenance_mode(db: Session) -> str:
    """Renvoie le mode de maintenance actif: none, full, app_only, dedications_only."""
    val = _setting_value(db, "maintenance_mode", "none").strip().lower()
    return val if val in {"none", "full", "app_only", "dedications_only"} else "none"


def _render_maintenance_page() -> str:
    """Page minimaliste de maintenance: spinner infini + texte 'Maintenance' en bas."""
    return """<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Maintenance · Dedikka</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: #050814;
            color: #ffffff;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            min-height: 100vh;
            min-height: 100dvh;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            overflow: hidden;
            user-select: none;
            -webkit-user-select: none;
        }
        .center-wrap {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            flex: 1;
        }
        .spinner {
            width: 44px;
            height: 44px;
            border: 3px solid rgba(255, 255, 255, 0.12);
            border-top-color: #a855f7;
            border-right-color: #ec4899;
            border-radius: 50%;
            animation: spin 0.9s cubic-bezier(0.55, 0.15, 0.45, 0.85) infinite;
        }
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        .footer-text {
            padding-bottom: max(28px, env(safe-area-inset-bottom));
            font-size: 13px;
            letter-spacing: 0.18em;
            text-transform: uppercase;
            font-weight: 500;
            color: rgba(255, 255, 255, 0.45);
        }
    </style>
</head>
<body>
    <div class="center-wrap">
        <div class="spinner" aria-label="Chargement"></div>
    </div>
    <div class="footer-text">Maintenance</div>
</body>
</html>"""





def _audit_admin_action(

    db: Session,

    actor: User,

    action: str,

    target_type: str,

    target_id: str = "",

    details: dict | None = None,

) -> None:

    db.add(AdminAuditLog(

        actor_user_id=actor.id,

        action=action,

        target_type=target_type,

        target_id=str(target_id),

        details=details or {},

        created_at=int(time.time()),

    ))





def _admin_page_url(slug: str, request: Request) -> str:

    origin = settings.public_frontend_url.rstrip("/") or _frontend_origin(request)

    return f"{origin}/p/{quote(slug)}" if origin else _published_url(request, slug)





def _frontend_origin(request: Request) -> str:

    origin = (request.headers.get("origin") or "").rstrip("/")

    allowed_origins = {value.rstrip("/") for value in settings.frontend_origins}

    if origin not in allowed_origins:

        origin = settings.public_frontend_url.rstrip("/")

    return origin





def _inject_template_page_data(template: str, data: dict) -> str:

    marker = "<!-- DEDIKKA_PAGE_DATA -->"

    if marker not in template:

        raise HTTPException(status_code=503, detail="Template data marker missing")

    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    encoded = encoded.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")

    rendered = template.replace(marker, f"<script>window.DEDIKKA_PAGE_DATA={encoded};</script>", 1)

    page_images = data.get("images") or {}
    has_extra_photos = any(re.fullmatch(r"extra_image_\d{1,3}", key) for key in page_images)
    has_path_photo = data.get("template_name") == "el-camino-juntos" and bool(page_images.get("image_1"))

    if has_extra_photos or has_path_photo:

        extra_gallery = """<style>

          #dedikka-extra-photos{max-width:1100px;margin:48px auto;padding:24px;width:min(100% - 32px,1100px);border-radius:24px;background:rgba(20,16,38,.88);color:#fff;font-family:system-ui,sans-serif}

          #dedikka-extra-photos h2{text-align:center;margin:0 0 18px;font-size:clamp(1.4rem,4vw,2rem)}

          #dedikka-extra-photos .dedikka-extra-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px}

          #dedikka-extra-photos figure{margin:0;overflow:hidden;border-radius:16px;background:rgba(255,255,255,.08)}

          #dedikka-extra-photos img{display:block;width:100%;aspect-ratio:1;object-fit:cover}

        </style><script>

          (function(){var data=window.DEDIKKA_PAGE_DATA||{},images=data.images||{};

          var entries=[];if(data.template_name==='el-camino-juntos'&&typeof images.image_1==='string'&&images.image_1)entries.push('image_1');

          entries=entries.concat(Object.keys(images).filter(function(k){return /^extra_image_\\d{1,3}$/.test(k)&&typeof images[k]==='string'&&images[k];})).sort(function(a,b){if(a==='image_1')return -1;if(b==='image_1')return 1;return Number(a.split('_').pop())-Number(b.split('_').pop());});

          if(!entries.length)return;var section=document.createElement('section');section.id='dedikka-extra-photos';

          var heading=document.createElement('h2');heading.textContent='Photos de notre histoire';section.appendChild(heading);

          var grid=document.createElement('div');grid.className='dedikka-extra-grid';section.appendChild(grid);

          entries.forEach(function(key,index){var figure=document.createElement('figure'),image=document.createElement('img');image.src=images[key];image.alt='Photo supplémentaire '+(index+1);image.loading='lazy';image.decoding='async';figure.appendChild(image);grid.appendChild(figure);});

          document.body.appendChild(section);})();

        </script>"""

        if "</body>" in rendered.lower():

            index = rendered.lower().rfind("</body>")

            rendered = rendered[:index] + extra_gallery + rendered[index:]

    return rendered





def _render_page(title: str, recipient: str, fields: dict, images: dict, template: str = "dedicate", lang: str = "fr") -> str:

    safe_title = html.escape(title)

    safe_recipient = html.escape(recipient)

    intro = html.escape(_field(fields, "intro_message"), quote=False).replace("\n", "<br>")

    quote = html.escape(_field(fields, "quote"), quote=False)

    letter = html.escape(_field(fields, "letter_content"), quote=False).replace("\n", "<br>")

    closing = html.escape(_field(fields, "end_message"), quote=False).replace("\n", "<br>")

    music_url = _field(fields, "music_url")



    sources = ([images["cover_image"]] if images.get("cover_image") else [])

    sources.extend(value for key, value in sorted(images.items()) if key.startswith("image_") and value)



    gallery = "".join(

        f'<img src="{html.escape(src, quote=True)}" alt="Photo souvenir" loading="lazy" class="gallery-photo">'

        for src in sources[:21]

    )

    image_section = f'<section class="gallery">{gallery}</section>' if gallery else ""



    music_section = ""

    if music_url and (music_url.startswith("http://") or music_url.startswith("https://") or music_url.startswith("/uploads/")):

        music_section = f'''<div class="audio-box">

            <audio controls loop preload="metadata">

                <source src="{html.escape(music_url, quote=True)}">

            </audio>

        </div>'''



    tagline = "Une dédicace spéciale"

    for_text = f"Pour <strong>{safe_recipient or 'une personne spéciale'}</strong>"

    footer_text = closing if closing else "Créé avec Dedikka"

    html_lang = "fr"



    return f"""<!doctype html>

<html lang="{html_lang}">

<head>

    <meta charset="utf-8">

    <meta name="viewport" content="width=device-width, initial-scale=1">

    <title>{safe_title} · Dedikka</title>

    <meta name="description" content="{safe_title}">

    <style>

        * {{ box-sizing: border-box; }}

        body {{

            margin: 0;

            background: linear-gradient(135deg, #fff1f2 0%, #fdf4ff 50%, #f5f3ff 100%);

            color: #3b2042;

            font: 17px/1.75 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;

            min-height: 100vh;

        }}

        main {{ max-width: 820px; margin: 4vh auto; padding: 20px; }}

        article {{

            background: rgba(255, 255, 255, 0.95);

            backdrop-filter: blur(10px);

            border-radius: 28px;

            padding: clamp(24px, 6vw, 64px);

            box-shadow: 0 25px 60px -15px rgba(120, 53, 115, 0.15);

            text-align: center;

        }}

        .tagline {{ text-transform: uppercase; letter-spacing: 2px; font-size: 0.82rem; color: #a21caf; font-weight: 700; }}

        h1 {{ font-size: clamp(2.2rem, 7vw, 4rem); line-height: 1.15; color: #b42370; margin: 12px 0 16px; font-weight: 800; }}

        .recipient {{ font-size: 1.25rem; color: #581c87; margin-bottom: 28px; }}

        .message {{ text-align: left; white-space: pre-wrap; margin: 28px auto; max-width: 660px; line-height: 1.8; }}

        .quote {{

            font-size: 1.35rem; font-style: italic; color: #6b21a8; background: #faf5ff;

            border-left: 4px solid #c084fc; padding: 20px 24px; border-radius: 0 16px 16px 0;

            margin: 32px auto; max-width: 660px; text-align: left;

        }}

        .gallery {{

            display: grid;

            grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));

            gap: 14px;

            margin: 36px 0;

        }}

        .gallery img {{

            width: 100%;

            height: 240px;

            object-fit: cover;

            border-radius: 18px;

            transition: transform 0.3s ease, box-shadow 0.3s ease;

        }}

        .gallery img:hover {{

            transform: scale(1.03);

            box-shadow: 0 12px 24px -6px rgba(0, 0, 0, 0.2);

        }}

        .audio-box {{ margin: 28px auto; display: flex; justify-content: center; }}

        audio {{ border-radius: 30px; outline: none; }}

        footer {{

            margin-top: 40px;

            padding-top: 24px;

            border-top: 1px solid #f3e8ff;

            color: #86198f;

            font-size: 0.95rem;

        }}

    </style>

</head>

<body>

    <main>

        <article>

            <p class="tagline">{tagline}</p>

            <h1>{safe_title}</h1>

            <p class="recipient">{for_text}</p>

            {music_section}

            {f'<div class="message">{intro}</div>' if intro else ''}

            {f'<blockquote class="quote">“{quote}”</blockquote>' if quote else ''}

            {image_section}

            {f'<div class="message">{letter}</div>' if letter else ''}

            <footer>{footer_text}</footer>

        </article>

    </main>

</body>

</html>"""





@router.post("/uploads/images", tags=["storage"])

async def upload_image(

    file: Annotated[UploadFile, File()],

    request: Request,

    user: User = Depends(current_user),

    owner_user_id: int | None = Query(default=None, gt=0),

    extra_photo: bool = Query(default=False),

    plan: str | None = Query(default=None, max_length=20),

    grant_source: Literal["credit", "manual_payment", "gift"] = Query(default="credit"),

    db: Session = Depends(get_db),

    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),

    csrf_cookie: str | None = Cookie(default=None, alias=settings.csrf_cookie_name),

    accept_language: str | None = Header(default=None, alias="Accept-Language"),

):

    lang = get_lang_from_header(accept_language)

    _require_csrf(request, x_csrf_token, csrf_cookie, lang)

    owner_id = user.id

    if owner_user_id is not None:

        _require_admin(user)

        owner = db.get(User, owner_user_id)

        if owner is None:

            raise HTTPException(status_code=404, detail="Compte utilisateur introuvable.")

        owner_id = owner_user_id

        if extra_photo:

            if not _payments_are_enabled(db):

                rights = _promotion_rights()

            elif grant_source != "credit" and plan in {"starter", "pro", "premium", "mensuel"}:

                rights = _rights_for_plan(plan)

            else:

                rights = _user_entitlements(db, owner)

            if int(rights.get("max_photos", 0)) <= 0:

                raise HTTPException(status_code=403, detail="Les photos supplémentaires nécessitent une formule payante.")

    elif extra_photo:

        rights = _user_entitlements(db, user)

        db.commit()

        if int(rights.get("max_photos", 0)) <= 0:

            raise HTTPException(status_code=403, detail="Les photos supplémentaires nécessitent une formule payante.")

    return await save_image(file, owner_id, str(request.base_url), lang)





@router.post("/uploads/audio", tags=["storage"])

async def upload_audio(

    file: Annotated[UploadFile, File()],

    request: Request,

    user: User = Depends(current_user),

    owner_user_id: int | None = Query(default=None, gt=0),

    plan: str | None = Query(default=None, max_length=20),

    grant_source: Literal["credit", "manual_payment", "gift"] = Query(default="credit"),

    db: Session = Depends(get_db),

    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),

    csrf_cookie: str | None = Cookie(default=None, alias=settings.csrf_cookie_name),

    accept_language: str | None = Header(default=None, alias="Accept-Language"),

):

    lang = get_lang_from_header(accept_language)

    _require_csrf(request, x_csrf_token, csrf_cookie, lang)

    owner = user

    if owner_user_id is not None:

        _require_admin(user)

        owner = db.get(User, owner_user_id)

        if owner is None:

            raise HTTPException(status_code=404, detail="Compte utilisateur introuvable.")

        if not _payments_are_enabled(db):

            rights = _promotion_rights()

        else:

            rights = _rights_for_plan(plan) if grant_source != "credit" and plan in {"starter", "pro", "premium", "mensuel"} else _user_entitlements(db, owner)

    else:

        rights = _user_entitlements(db, user)

        db.commit()

    if not rights.get("allow_custom_mp3"):

        raise HTTPException(status_code=403, detail="L’envoi d’une musique personnelle est réservé aux formules Pro, Premium et Mensuel.")

    return await save_audio(file, owner.id, str(request.base_url), lang)





@router.post("/pages/preview", response_class=HTMLResponse, tags=["pages"])

async def preview_page(

    request: Request,

    accept_language: str | None = Header(default=None, alias="Accept-Language"),

):

    lang = get_lang_from_header(accept_language)

    form = await request.form()

    fields = {key: str(value) for key, value in form.multi_items() if not hasattr(value, "filename")}

    title = fields.get("data[custom_celebration_title]") or fields.get("data[title]") or fields.get("title") or "Une dédicace spéciale"

    recipient = str(form.get("recipient_name") or fields.get("data[recipient_name]") or "")

    template_name = _require_available_template(str(form.get("template_name") or "dedicate"))



    images = {}

    cover = form.get("preview_cover_image")

    if isinstance(cover, str) and (cover.startswith("data:image/") or cover.startswith("http://") or cover.startswith("https://") or cover.startswith("/uploads/")):

        images["cover_image"] = cover



    for key, value in form.multi_items():

        match = re.fullmatch(r"preview_image_url\[(\d+)\]", key)

        if match and isinstance(value, str) and (value.startswith("data:image/") or value.startswith("http://") or value.startswith("https://") or value.startswith("/uploads/")):

            images[f"image_{match.group(1)}"] = value



    template_file = TEMPLATE_FILES.get(template_name)

    if template_file:

        template_path = Path(__file__).resolve().parent / "template" / template_file

        try:

            template = template_path.read_text(encoding="utf-8")

        except OSError as exc:

            raise HTTPException(status_code=503, detail="Template unavailable") from exc

        data = {

            "slug": "preview",

            "title": title,

            "recipient_name": recipient,

            "template_name": template_name,

            "form_fields": fields,

            "images": images,

            "backend_url": str(request.base_url).rstrip("/"),

            "frontend_url": settings.public_frontend_url.rstrip("/"),

        }

        return HTMLResponse(_inject_template_page_data(template, data), headers={"Cache-Control": "no-store"})

    return HTMLResponse(_render_page(title, recipient, fields, images, template_name, lang))





@router.post("/pages", status_code=201, tags=["pages"])

def publish_page(

    payload: PublishInput,

    request: Request,

    background_tasks: BackgroundTasks,

    db: Session = Depends(get_db),

    user: User = Depends(current_user),

    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),

    csrf_cookie: str | None = Cookie(default=None, alias=settings.csrf_cookie_name),

    accept_language: str | None = Header(default=None, alias="Accept-Language"),

):

    lang = get_lang_from_header(accept_language)

    _require_csrf(request, x_csrf_token, csrf_cookie, lang)



    if len(payload.form_fields) > 300 or any(len(k) > 150 or len(v) > 6000 for k, v in payload.form_fields.items()):

        raise HTTPException(status_code=422, detail=translate("page_data_too_large", lang))



    template_name = _require_available_template(payload.template_name)

    base_url = str(request.base_url)

    promo_mode = not _payments_are_enabled(db)

    credit_rights = _promotion_rights() if promo_mode else _consume_plan_credit(db, user)

    if credit_rights is None:

        _consume_free_page_slot(db, user)

    photo_limit = _photo_limit(template_name, credit_rights)

    _validate_template_images(

        template_name, payload.images, user.id, base_url, photo_limit,

        allow_extra_photos=credit_rights is not None,

    )

    _validate_music_url(

        _field(payload.form_fields, "music_url"), user.id, base_url,

        bool((credit_rights or {}).get("allow_custom_mp3")),

    )

    if payload.custom_slug and not (credit_rights or {}).get("allow_custom_link", False):

        raise HTTPException(status_code=403, detail="Le lien personnalisé est réservé aux formules Pro, Premium et Mensuel.")



    slug = _slugify(payload.custom_slug or payload.title)

    if db.scalar(select(Dedication.id).where(Dedication.slug == slug)):

        if payload.custom_slug:

            raise HTTPException(status_code=409, detail=translate("slug_in_use", lang))

        slug = f"{slug}-{uuid.uuid4().hex[:7]}"



    now = int(time.time())

    # `is_free` means the short 12-hour free tier; promo pages use the

    # promotional rights snapshot and its one-year duration instead.

    is_free_page = credit_rights is None

    duration_days = int((credit_rights or {}).get("page_duration_days", 0))

    duration_seconds = duration_days * 24 * 60 * 60 if duration_days else settings.free_page_duration_hours * 60 * 60



    page = Dedication(

        user_id=user.id,

        slug=slug,

        title=payload.title.strip(),

        recipient_name=payload.recipient_name.strip(),

        template_name=template_name,

        form_fields=payload.form_fields,

        images=payload.images,

        created_at=now,

        updated_at=now,

        is_free=is_free_page,

        expires_at=now + duration_seconds,

        plan_key=(credit_rights or {}).get("plan_key", "free"),

        max_photos=photo_limit,

        allow_custom_mp3=bool((credit_rights or {}).get("allow_custom_mp3", False)),

        allow_custom_link=bool((credit_rights or {}).get("allow_custom_link", False)),

        allow_qr=bool((credit_rights or {}).get("allow_qr", False)),

    )

    db.add(page)

    try:

        db.commit()

    except IntegrityError:

        db.rollback()

        raise HTTPException(status_code=409, detail=translate("slug_in_use", lang)) from None



    from services import send_page_created_notifications

    background_tasks.add_task(

        send_page_created_notifications,

        user.email,

        user.whatsapp_number,

        user.name,

        page.title,

        _published_url(request, slug),

        settings.public_backend_url or str(request.base_url).rstrip("/"),

        page.allow_qr,

        page.is_free,

        duration_days,

    )



    return {

        "id": page.id,

        "slug": slug,

        "url": _published_url(request, slug),

        "title": page.title,

        "recipient_name": page.recipient_name,

        "template_name": page.template_name,

        "is_free": page.is_free,

        "expires_at": page.expires_at,

        "plan_key": page.plan_key,

        "max_photos": page.max_photos,

        "allow_custom_mp3": page.allow_custom_mp3,

        "allow_custom_link": page.allow_custom_link,

        "allow_qr": page.allow_qr,

        "paid_pages_balance": user.paid_pages_balance,

        "promo_mode": promo_mode,

    }





@router.get("/pages/mine", tags=["pages"])

def my_pages(

    user: User = Depends(current_user),

    db: Session = Depends(get_db),

    request: Request = None,

):

    pages = db.scalars(select(Dedication).where(Dedication.user_id == user.id).order_by(Dedication.created_at.desc())).all()

    return [{

        "id": page.id,

        "slug": page.slug,

        "title": page.title,

        "recipient_name": page.recipient_name,

        "template_name": page.template_name,

        "url": _published_url(request, page.slug) if request else f"/p/{page.slug}",

        "created_at": page.created_at,

        "updated_at": page.updated_at,

        "is_free": page.is_free,

        "expires_at": page.expires_at,

        "is_expired": page.expires_at is not None and page.expires_at <= int(time.time()),

        "plan_key": page.plan_key,

        "allow_qr": page.allow_qr,

    } for page in pages]





@router.get("/pages/{page_id}", tags=["pages"])

def get_page(

    page_id: int,

    request: Request,

    user: User = Depends(current_user),

    db: Session = Depends(get_db),

    accept_language: str | None = Header(default=None, alias="Accept-Language"),

):

    lang = get_lang_from_header(accept_language)

    page = db.get(Dedication, page_id)

    if page is None:

        raise HTTPException(status_code=404, detail=translate("page_not_found", lang))

    if page.user_id != user.id:

        raise HTTPException(status_code=403, detail=translate("page_forbidden", lang))



    return {

        "id": page.id,

        "slug": page.slug,

        "title": page.title,

        "recipient_name": page.recipient_name,

        "template_name": page.template_name,

        "form_fields": page.form_fields,

        "images": page.images,

        "url": _published_url(request, page.slug),

        "created_at": page.created_at,

        "updated_at": page.updated_at,

        "is_free": page.is_free,

        "expires_at": page.expires_at,

        "plan_key": page.plan_key,

        "max_photos": page.max_photos,

        "allow_custom_mp3": page.allow_custom_mp3,

        "allow_custom_link": page.allow_custom_link,

        "allow_qr": page.allow_qr,

    }





@router.patch("/pages/{page_id}", tags=["pages"])

def update_page(

    page_id: int,

    payload: UpdatePageInput,

    request: Request,

    db: Session = Depends(get_db),

    user: User = Depends(current_user),

    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),

    csrf_cookie: str | None = Cookie(default=None, alias=settings.csrf_cookie_name),

    accept_language: str | None = Header(default=None, alias="Accept-Language"),

):

    lang = get_lang_from_header(accept_language)

    _require_csrf(request, x_csrf_token, csrf_cookie, lang)



    page = db.get(Dedication, page_id)

    if page is None:

        raise HTTPException(status_code=404, detail=translate("page_not_found", lang))

    if page.user_id != user.id:

        raise HTTPException(status_code=403, detail=translate("page_forbidden", lang))



    base_url = str(request.base_url)

    if payload.title is not None:

        page.title = payload.title.strip()

    if payload.recipient_name is not None:

        page.recipient_name = payload.recipient_name.strip()

    if payload.template_name is not None:

        requested_template = payload.template_name.strip().lower()

        if requested_template != page.template_name.strip().lower():

            page.template_name = _require_available_template(requested_template)

    if payload.form_fields is not None:

        if len(payload.form_fields) > 300 or any(len(k) > 150 or len(v) > 6000 for k, v in payload.form_fields.items()):

            raise HTTPException(status_code=422, detail=translate("page_data_too_large", lang))

        _validate_music_url(

            _field(payload.form_fields, "music_url"), user.id, base_url,

            page.allow_custom_mp3,

        )

        page.form_fields = payload.form_fields

    if payload.images is not None:

        template_name = page.template_name.strip().lower()

        if template_name in TEMPLATE_IMAGE_LIMITS:

            _validate_template_images(

                template_name, payload.images, user.id, base_url, page.max_photos,

                allow_extra_photos=page.plan_key != "free",

            )

        elif len(payload.images) > 21 or any(not is_valid_user_image(url, user.id, base_url) for url in payload.images.values()):

            raise HTTPException(status_code=422, detail=translate("image_ownership_invalid", lang))

        page.images = payload.images



    if payload.custom_slug:

        if not page.allow_custom_link:

            raise HTTPException(status_code=403, detail="Le lien personnalisé n’est pas inclus dans cette formule.")

        new_slug = _slugify(payload.custom_slug)

        existing = db.scalar(select(Dedication.id).where(Dedication.slug == new_slug, Dedication.id != page.id))

        if existing:

            raise HTTPException(status_code=409, detail=translate("slug_in_use", lang))

        page.slug = new_slug



    page.updated_at = int(time.time())

    db.commit()

    db.refresh(page)



    return {

        "id": page.id,

        "slug": page.slug,

        "url": _published_url(request, page.slug),

        "title": page.title,

        "recipient_name": page.recipient_name,

        "template_name": page.template_name,

        "form_fields": page.form_fields,

        "images": page.images,

        "updated_at": page.updated_at,

        "is_free": page.is_free,

        "expires_at": page.expires_at,

        "plan_key": page.plan_key,

        "max_photos": page.max_photos,

        "allow_custom_mp3": page.allow_custom_mp3,

        "allow_custom_link": page.allow_custom_link,

        "allow_qr": page.allow_qr,

    }





@router.delete("/pages/{page_id}", tags=["pages"])

def delete_page(

    page_id: int,

    request: Request,

    db: Session = Depends(get_db),

    user: User = Depends(current_user),

    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),

    csrf_cookie: str | None = Cookie(default=None, alias=settings.csrf_cookie_name),

    accept_language: str | None = Header(default=None, alias="Accept-Language"),

):

    lang = get_lang_from_header(accept_language)

    _require_csrf(request, x_csrf_token, csrf_cookie, lang)



    page = db.get(Dedication, page_id)

    if page is None:

        raise HTTPException(status_code=404, detail=translate("page_not_found", lang))

    if page.user_id != user.id:

        raise HTTPException(status_code=403, detail=translate("page_forbidden", lang))



    db.delete(page)

    db.commit()

    return {"status": "ok", "message": translate("page_deleted", lang)}





@public_router.get("/p/{slug}", tags=["pages"])

def view_page(

    slug: str,

    request: Request,

    db: Session = Depends(get_db),

    lang: str | None = None,

    accept_language: str | None = Header(default=None, alias="Accept-Language"),

):

    page = db.scalar(select(Dedication).where(Dedication.slug == slug))

    preferred_lang = get_lang_from_header(accept_language)

    if page is None:

        raise HTTPException(status_code=404, detail=translate("page_not_found", preferred_lang))

    _require_active_page(page)

    frontend_origin = _frontend_origin(request)

    if frontend_origin:

        return RedirectResponse(

            url=f"{frontend_origin}/p/{quote(page.slug)}",

            status_code=307,

        )

    return HTMLResponse(

        _render_page(

            title=page.title,

            recipient=page.recipient_name,

            fields=page.form_fields,

            images=page.images,

            template=page.template_name,

            lang=preferred_lang,

        )

    )





@router.get("/public/pages/{slug}", tags=["published-pages"])

def get_public_page(

    slug: str,

    db: Session = Depends(get_db),

    accept_language: str | None = Header(default=None, alias="Accept-Language"),

):

    """Return public page data; the frontend selects and renders the template."""

    lang = get_lang_from_header(accept_language)

    page = db.scalar(select(Dedication).where(Dedication.slug == slug))

    if page is None:

        raise HTTPException(status_code=404, detail=translate("page_not_found", lang))

    _require_active_page(page)

    public_fields = {

        key: value

        for key, value in page.form_fields.items()

        if not any(secret in key.lower() for secret in ("password", "csrf", "token", "secret", "combo", "combination"))

        and not key.startswith("_")

    }

    return {

        "slug": page.slug,

        "title": page.title,

        "recipient_name": page.recipient_name,

        "template_name": page.template_name,

        "form_fields": public_fields,

        "images": page.images,

        "created_at": page.created_at,

    }





@router.get("/public/pages/{slug}/render", response_class=HTMLResponse, tags=["published-pages"])

def render_public_template(

    slug: str,

    request: Request,

    db: Session = Depends(get_db),

):

    """Serve the original backend HTML template selected by this page."""

    page = db.scalar(select(Dedication).where(Dedication.slug == slug))

    if page is None:

        raise HTTPException(status_code=404, detail="Page not found")

    _require_active_page(page)

    template_file = TEMPLATE_FILES.get(page.template_name.strip().lower())

    if template_file is None:

        raise HTTPException(status_code=404, detail="Template not found")



    template_path = Path(__file__).resolve().parent / "template" / template_file

    try:

        template = template_path.read_text(encoding="utf-8")

    except OSError as exc:

        raise HTTPException(status_code=503, detail="Template unavailable") from exc



    public_fields = {

        key: value

        for key, value in page.form_fields.items()

        if not any(secret in key.lower() for secret in ("password", "csrf", "token", "secret", "combo", "combination"))

        and not key.startswith("_")

    }

    data = {

        "slug": page.slug,

        "title": page.title,

        "recipient_name": page.recipient_name,

        "template_name": page.template_name,

        "form_fields": public_fields,

        "images": page.images,

        "password_hint": page.form_fields.get("password_hint", ""),

        "password_title": page.form_fields.get("data[custom_password_title]", ""),

        "backend_url": str(request.base_url).rstrip("/"),

        "frontend_url": settings.public_frontend_url.rstrip("/"),

    }

    rendered = _inject_template_page_data(template, data)

    return HTMLResponse(rendered, headers={"Cache-Control": "no-store"})



@asynccontextmanager

async def lifespan(_: FastAPI):

    # Ensure local upload directory exists

    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)

    Base.metadata.create_all(bind=engine)

    ensure_schema_compatibility()

    with SessionLocal() as db:

        if not _payments_are_enabled(db) and not _setting_value(db, "promo_started_at", ""):

            _set_setting(db, "promo_started_at", str(int(time.time())), "Début de la période promotionnelle gratuite.")

            db.commit()

        legacy_admin_secret = db.scalar(select(AppSetting).where(AppSetting.key == "admin_secret_key"))

        if legacy_admin_secret is not None:

            db.delete(legacy_admin_secret)

            db.commit()

    yield





is_development = settings.app_env.lower() in {"development", "local"}

if not is_development and not settings.cookie_secure:

    raise RuntimeError("COOKIE_SECURE must be enabled in production")

app = FastAPI(

    title=settings.app_name,

    lifespan=lifespan,

    docs_url="/docs" if is_development else None,

    redoc_url="/redoc" if is_development else None,

    openapi_url="/openapi.json" if is_development else None,

)

# Published templates contain large inline assets. Compress HTML responses so
# mobile clients download them faster without changing the rendered document.
app.add_middleware(GZipMiddleware, minimum_size=1024)





@app.middleware("http")
async def maintenance_enforcement_middleware(request: Request, call_next):
    path = request.url.path
    api_prefix = settings.api_prefix.rstrip("/")
    admin_prefix = f"{api_prefix}/admin"
    auth_prefix = f"{api_prefix}/auth"
    maintenance_status_path = f"{api_prefix}/public/maintenance/status"

    # L'administration et les ressources nécessaires pour afficher l'interface restent accessibles.
    if (
        path == maintenance_status_path
        or path == "/admin.html"
        or path == "/login.html"
        or path == f"{api_prefix}/payments/webhook/chariow"
        or path.startswith(f"{admin_prefix}/")
        or path == admin_prefix
        or path.startswith(("/frontend/", "/build/", "/images/", "/assets/"))
        or path == "/backend/inicio.gif"
        or path in {"/favicon.ico", "/robots.txt", "/manifest.json"}
    ):
        return await call_next(request)

    # Vérification du mode de maintenance en DB
    mode = "none"
    try:
        with SessionLocal() as db:
            mode = _get_maintenance_mode(db)
    except Exception:
        mode = "none"

    if mode != "none":
        # Le compte admin peut continuer à utiliser l'application et ses routes d'administration.
        is_admin_user = False
        authorization = request.headers.get("authorization", "")
        bearer_token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        session_token = bearer_token or request.cookies.get(settings.session_cookie_name)
        if session_token:
            try:
                token_hash = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
                with SessionLocal() as db:
                    sess = db.scalar(select(UserSession).where(
                        UserSession.token_hash == token_hash,
                        UserSession.expires_at > int(time.time()),
                    ))
                    if sess and sess.user and sess.user.is_admin:
                        is_admin_user = True
            except Exception:
                is_admin_user = False

        if is_admin_user:
            return await call_next(request)

        # Le formulaire de connexion reste disponible pour que l'administrateur puisse revenir.
        # L'inscription et les changements de profil restent bloqués en maintenance.
        if mode in {"full", "app_only"} and (path.startswith(f"{auth_prefix}/") or path == auth_prefix):
            allowed_auth_calls = {
                ("GET", f"{auth_prefix}/csrf"),
                ("GET", f"{auth_prefix}/me"),
                ("POST", f"{auth_prefix}/login"),
                ("POST", f"{auth_prefix}/google"),
                ("POST", f"{auth_prefix}/logout"),
            }
            if (request.method.upper(), path) not in allowed_auth_calls:
                return JSONResponse(
                    status_code=503,
                    content={"detail": "Les comptes sont temporairement en maintenance.", "maintenance_mode": mode},
                )
            return await call_next(request)

        is_dedication_route = bool(
            re.match(rf"^{re.escape(api_prefix)}/public/pages/[^/]+(?:/(?:render|verify-code))?$", path)
            or re.match(r"^/(?:p|d)/[^/]+/?$", path)
            or path == "/published.html"
        )

        should_block = (
            mode == "full"
            or (mode == "app_only" and not is_dedication_route)
            or (mode == "dedications_only" and is_dedication_route)
        )

        if should_block:
            accept_header = request.headers.get("accept", "")
            if "text/html" in accept_header or not path.startswith(f"{api_prefix}/"):
                return HTMLResponse(content=_render_maintenance_page(), status_code=503)
            return JSONResponse(
                status_code=503,
                content={"detail": "Service temporairement en maintenance.", "maintenance_mode": mode},
            )

    return await call_next(request)


@app.middleware("http")

async def security_headers(request, call_next):

    response = await call_next(request)

    response.headers.setdefault("X-Content-Type-Options", "nosniff")

    response.headers.setdefault("X-Frame-Options", "DENY")

    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")

    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")

    if not is_development:

        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

    return response





@app.get("/backend/inicio.gif", include_in_schema=False)

def get_inicio_gif():

    """Serve the public intro GIF without exposing the rest of the backend."""

    gif_path = Path(__file__).resolve().parent / "template" / "inicio.gif"

    if not gif_path.is_file():

        raise HTTPException(status_code=404, detail="Resource not found")

    return FileResponse(

        path=gif_path,

        media_type="image/gif",

        headers={"Cache-Control": "public, max-age=86400"},

    )



# User uploads are served directly only by the local development server.

upload_path = Path(settings.upload_dir)

upload_path.mkdir(parents=True, exist_ok=True)

if is_development:

    app.mount("/uploads", StaticFiles(directory=str(upload_path)), name="uploads")



build_path = Path(__file__).resolve().parent.parent / "build"

if build_path.is_dir():

    app.mount("/build", StaticFiles(directory=str(build_path)), name="build")



images_path = Path(__file__).resolve().parent.parent / "images"

if images_path.is_dir():

    app.mount("/images", StaticFiles(directory=str(images_path)), name="images")



if is_development:

    app.add_middleware(

        CORSMiddleware,

        allow_origins=list(dict.fromkeys(settings.frontend_origins)),

        allow_credentials=True,

        allow_methods=["*"],

        allow_headers=["*"],

    )

else:

    # Keep production origins explicit; the exact loopback dev origin is also

    # permitted because the owner tests the hosted API from the local frontend.

    local_dev_origins = {"http://127.0.0.1:8000"}

    allowed_origins = list(dict.fromkeys(origin.rstrip("/") for origin in [

        *settings.frontend_origins,

        *([settings.public_frontend_url.rstrip("/")] if settings.public_frontend_url else []),

    ] if ((origin.startswith("https://") and "*" not in origin) or origin in local_dev_origins)))

    app.add_middleware(

        CORSMiddleware,

        allow_origins=allowed_origins,

        allow_credentials=True,

        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],

        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-Requested-With", "Accept-Language"],

    )



app.include_router(accounts_router, prefix=settings.api_prefix)

app.include_router(router, prefix=settings.api_prefix)

app.include_router(public_router)





@app.get(settings.api_prefix + "/health", tags=["health"])

def health():

    return {

        "status": "ok",

        "site": "Dedikka",

        "supported_languages": ["fr"],

        "storage": "supabase" if (settings.supabase_url and settings.supabase_secret_key) else ("unconfigured" if settings.app_env.lower() in {"production", "prod"} else "local"),

    }





@app.get(settings.api_prefix + "/qr-heart", tags=["qr"])

def get_qr_heart(

    url: str = Query(min_length=8, max_length=2048),

    color: str = Query(default="C2185B", min_length=6, max_length=7),

    bgcolor: str = Query(default="FFCCF0", min_length=6, max_length=7),

):

    import io

    from fastapi.responses import Response



    try:

        parsed_url = urlsplit(url)

    except ValueError:

        raise HTTPException(status_code=422, detail="L’adresse du QR code doit être une URL HTTP ou HTTPS valide.") from None

    if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname or parsed_url.username or parsed_url.password:

        raise HTTPException(status_code=422, detail="L’adresse du QR code doit être une URL HTTP ou HTTPS valide.")

    if not re.fullmatch(r"#?[0-9a-fA-F]{6}", color) or not re.fullmatch(r"#?[0-9a-fA-F]{6}", bgcolor):

        raise HTTPException(status_code=422, detail="La couleur du QR code doit être un code hexadécimal sur six caractères.")

    color = color.lstrip("#").upper()

    bgcolor = bgcolor.lstrip("#").upper()



    # The QR generator lives in qr_generator.py.



    buf = io.BytesIO()

    img = generate_heart_qr(url, size=512, fg_hex=f"#{color}", bg_hex=f"#{bgcolor}")

    img.save(buf, format="PNG")

    return Response(

        content=buf.getvalue(),

        media_type="image/png",

        headers={"Cache-Control": "no-store"}

    )





# ==========================================

# ROUTES PAIEMENT SÉCURISÉES & CONTRÔLE ADMIN

# ==========================================



class PaymentInitiateInput(BaseModel):

    plan: str = Field(min_length=2, max_length=20)

    phone: str = Field(min_length=8, max_length=20)





@app.get(settings.api_prefix + "/payments/status", tags=["payments"])

def get_payments_status(db: Session = Depends(get_db)):

    """Indique au frontend si les paiements sont activés ou en promotion/gratuits."""

    from core import OFFICIAL_PLANS

    payments_enabled = _payments_are_enabled(db)

    return {

        "payments_enabled": payments_enabled,

        "promo_mode": not payments_enabled,

        "plans": {

            k: {

                "name": v["name"],

                "price_usd": v["price_usd"],

                "price_cdf": v["price_cdf"],

                "pages_included": v["pages_included"],

                "max_photos": v["max_photos"],

                "allow_custom_mp3": v["allow_custom_mp3"],

                "allow_custom_link": v["allow_custom_link"],

                "allow_qr": v["allow_qr"],

            }

            for k, v in OFFICIAL_PLANS.items()

        }

    }





@app.get(settings.api_prefix + "/payments/entitlements", tags=["payments"])

def get_payment_entitlements(

    user: User = Depends(current_user),

    db: Session = Depends(get_db),

):

    result = _user_entitlements(db, user)

    db.commit()

    return result





@app.get(settings.api_prefix + "/admin/users/{user_id}/entitlements", tags=["admin"])

def admin_user_entitlements(

    user_id: int,

    plan: str | None = Query(default=None, max_length=20),

    grant_source: Literal["credit", "manual_payment", "gift"] = Query(default="credit"),

    user: User = Depends(current_user),

    db: Session = Depends(get_db),

):

    _require_admin(user)

    target = db.get(User, user_id)

    if target is None:

        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")

    if grant_source == "credit":

        result = _user_entitlements(db, target)

    elif plan in {"starter", "pro", "premium", "mensuel"}:

        result = _rights_for_plan(plan)

        result["paid_pages_balance"] = target.paid_pages_balance

    else:

        raise HTTPException(status_code=422, detail="Choisis une formule valide.")

    if not _payments_are_enabled(db):

        result = {**_promotion_rights(), "paid_pages_balance": int(target.paid_pages_balance)}

    db.commit()

    return result





@app.post(settings.api_prefix + "/payments/initiate", tags=["payments"])

async def initiate_payment(

    payload: PaymentInitiateInput,

    user: User = Depends(current_user),

    db: Session = Depends(get_db),

):

    """Initie un paiement Mobile Money sécurisé via Chariow."""

    from core import OFFICIAL_PLANS

    from services import create_chariow_checkout_session



    if not _payments_are_enabled(db):

        raise HTTPException(

            status_code=403,

            detail="Les paiements sont actuellement suspendus (Offre promotionnelle ou maintenance)."

        )



    plan_key = payload.plan.lower().strip()

    if plan_key not in OFFICIAL_PLANS:

        raise HTTPException(status_code=422, detail="Offre sélectionnée invalide.")



    res = await create_chariow_checkout_session(

        plan_key=plan_key,

        phone_number=payload.phone,

        user_email=user.email,

        user_name=user.name,

    )

    return res





@app.post(settings.api_prefix + "/payments/webhook/chariow", tags=["payments"])

async def chariow_webhook(request: Request, db: Session = Depends(get_db)):

    """Verify a Chariow Pulse signature and credit a completed sale exactly once."""

    from core import OFFICIAL_PLANS



    secret = settings.chariow_webhook_secret.strip()

    if not secret:

        logger.error("Chariow Pulse secret is not configured")

        raise HTTPException(status_code=503, detail="Le secret du webhook Chariow n’est pas configuré.")



    # Chariow signs the exact raw body bytes. Never parse and re-serialize before checking.

    raw_body = await request.body()

    received_signature = request.headers.get("x-chariow-signature", "")

    expected_signature = "sha256=" + hmac.new(

        secret.encode("utf-8"), raw_body, hashlib.sha256

    ).hexdigest()

    try:

        received_signature_bytes = received_signature.encode("ascii")

    except UnicodeEncodeError:

        received_signature_bytes = b""

    expected_signature_bytes = expected_signature.encode("ascii")

    if (

        len(received_signature_bytes) != len(expected_signature_bytes)

        or not hmac.compare_digest(expected_signature_bytes, received_signature_bytes)

    ):

        logger.warning("Rejected Chariow Pulse request with invalid signature")

        raise HTTPException(status_code=401, detail="Signature Chariow invalide.")



    delivery_id = request.headers.get("x-pulse-delivery-id", "").strip()

    pulse_id = request.headers.get("x-pulse-id", "").strip()

    header_event = request.headers.get("x-pulse-event", "").strip()

    if not delivery_id or not pulse_id or not header_event:

        raise HTTPException(status_code=400, detail="En-têtes Pulse Chariow manquants.")



    try:

        body = json.loads(raw_body)

    except (UnicodeDecodeError, json.JSONDecodeError):

        raise HTTPException(status_code=400, detail="Corps JSON Chariow invalide.") from None

    if not isinstance(body, dict):

        raise HTTPException(status_code=400, detail="Corps JSON Chariow invalide.")



    data = body.get("data") if isinstance(body.get("data"), dict) else {}

    event_type = str(body.get("event") or data.get("event") or "").strip()

    if not event_type or event_type != header_event:

        raise HTTPException(status_code=400, detail="Événement Pulse Chariow incohérent.")

    if event_type != "successful.sale":

        return {"status": "ignored", "event": event_type}



    # Current Chariow payloads place sale/product/customer at the root;

    # the nested fallback preserves compatibility with older deliveries.

    sale = body.get("sale") or data.get("sale") or {}

    product = body.get("product") or data.get("product") or {}

    customer = body.get("customer") or data.get("customer") or {}

    if not all(isinstance(item, dict) for item in (sale, product, customer)):

        raise HTTPException(status_code=400, detail="Données de vente Chariow invalides.")



    sale_id = str(sale.get("id") or "").strip()

    product_id = str(product.get("id") or data.get("product_id") or "").strip()

    customer_email = str(customer.get("email") or "").strip().lower()

    if not sale_id or not product_id or not customer_email:

        raise HTTPException(status_code=422, detail="La vente Chariow ne contient pas les données requises.")



    # Dashboard test Pulses are signed too, but must never grant real page credits.

    if sale_id.startswith("test_") or body.get("note") is not None:

        return {"status": "test_ignored"}

    sale_status = str(sale.get("status") or "").strip().lower()

    if sale_status and sale_status != "completed":

        return {"status": "ignored", "reason": "Sale is not completed"}



    matched_plan = next(

        (plan for plan in OFFICIAL_PLANS.values() if plan["product_id"] == product_id),

        None,

    )

    if matched_plan is None:

        logger.error("Signed Chariow sale %s has an unmapped product ID", sale_id)

        raise HTTPException(status_code=422, detail="Le produit Chariow n’est associé à aucune formule Dedikka.")



    if db.get(ChariowPulseDelivery, delivery_id) is not None:

        return {"status": "duplicate_delivery"}

    if db.scalar(select(ChariowPulseDelivery.delivery_id).where(ChariowPulseDelivery.sale_id == sale_id)):

        return {"status": "duplicate_sale"}



    user = db.scalar(select(User).where(func.lower(User.email) == customer_email))

    if user is None:

        logger.error("Signed Chariow sale %s has no matching Dedikka account", sale_id)

        raise HTTPException(status_code=503, detail="Le compte Dedikka associé à cette vente est introuvable.")



    pages_to_credit = int(matched_plan["pages_included"])

    db.add(ChariowPulseDelivery(

        delivery_id=delivery_id,

        sale_id=sale_id,

        pulse_id=pulse_id,

        product_id=product_id,

        user_id=user.id,

        pages_credited=pages_to_credit,

        created_at=int(time.time()),

    ))

    _grant_plan_credits(db, user, next(key for key, plan in OFFICIAL_PLANS.items() if plan["product_id"] == product_id), pages_to_credit)

    try:

        # Delivery record and balance update commit together; a retry can never re-credit it.

        db.commit()

    except IntegrityError:

        db.rollback()

        if (

            db.get(ChariowPulseDelivery, delivery_id) is not None

            or db.scalar(select(ChariowPulseDelivery.delivery_id).where(ChariowPulseDelivery.sale_id == sale_id))

        ):

            return {"status": "duplicate_delivery"}

        raise HTTPException(status_code=503, detail="La vente Chariow n’a pas pu être enregistrée.") from None



    logger.info("Credited %s pages for verified Chariow sale %s", pages_to_credit, sale_id)

    return {"status": "success", "credited": pages_to_credit}






@app.get(settings.api_prefix + "/public/maintenance/status", tags=["public"])
def public_maintenance_status(db: Session = Depends(get_db)):
    """Renvoie le statut public du mode maintenance pour le frontend Firebase."""
    return {"mode": _get_maintenance_mode(db)}


@app.get(settings.api_prefix + "/admin/maintenance", tags=["admin"])
def admin_maintenance_status(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Lecture authentifiée du mode maintenance courant."""
    _require_admin(user)
    return {"mode": _get_maintenance_mode(db)}


class AdminMaintenanceInput(BaseModel):
    mode: Literal["none", "full", "app_only", "dedications_only"]
    reason: str = Field(default="", max_length=250)


@app.post(settings.api_prefix + "/admin/maintenance", tags=["admin"])
def toggle_maintenance(
    payload: AdminMaintenanceInput,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    csrf_cookie: str | None = Cookie(default=None, alias=settings.csrf_cookie_name),
):
    """Bascule persistante du mode maintenance (accès réservé aux administrateurs)."""
    admin = _require_admin(user)
    _require_csrf(request, x_csrf_token, csrf_cookie)
    _set_setting(db, "maintenance_mode", payload.mode, f"Maintenance: {payload.mode} ({payload.reason.strip()})")
    _audit_admin_action(db, admin, "maintenance.toggle", "system", details={"mode": payload.mode, "reason": payload.reason.strip()})
    db.commit()
    labels = {
        "none": "Maintenance désactivée (mode normal).",
        "full": "Maintenance totale activée.",
        "app_only": "Maintenance de l'application activée (dédicaces en ligne).",
        "dedications_only": "Maintenance des dédicaces activée (application en ligne).",
    }
    return {
        "status": "ok",
        "mode": payload.mode,
        "message": labels.get(payload.mode, "Mode maintenance mis à jour.")
    }


@app.post(settings.api_prefix + "/admin/payments/toggle", tags=["admin"])

def toggle_payments(

    payload: AdminPaymentsInput,

    request: Request,

    background_tasks: BackgroundTasks,

    user: User = Depends(current_user),

    db: Session = Depends(get_db),

    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),

    csrf_cookie: str | None = Cookie(default=None, alias=settings.csrf_cookie_name),

):

    """Persistent payment switch; authorization and CSRF are checked server-side."""

    admin = _require_admin(user)

    _require_csrf(request, x_csrf_token, csrf_cookie)

    was_enabled = _payments_are_enabled(db)

    now = int(time.time())

    credits_extended = 0

    if was_enabled and not payload.enabled:

        _set_setting(db, "promo_started_at", str(now), "Début de la période promotionnelle gratuite.")

    elif not was_enabled and payload.enabled:

        started_at = int(_setting_value(db, "promo_started_at", "0") or 0)

        if started_at:

            credits_extended = _resume_credit_expiry_after_promotion(db, started_at, now)

            _set_setting(db, "promo_started_at", "", "Début de la période promotionnelle gratuite.")

    elif not was_enabled and not _setting_value(db, "promo_started_at", ""):

        _set_setting(db, "promo_started_at", str(now), "Début de la période promotionnelle gratuite.")

    _set_setting(db, "payments_enabled", "true" if payload.enabled else "false", "Active les paiements ou le mode promotion gratuit.")

    _audit_admin_action(db, admin, "payments.toggle", "payments", details={"enabled": payload.enabled, "reason": payload.reason.strip()})

    campaign_id = None

    mail_configured = bool((settings.email_api_url and settings.email_api_key) or (settings.smtp_host and settings.smtp_from_email))

    if was_enabled and not payload.enabled and mail_configured:

        campaign = AdminEmailCampaign(

            subject="Offre promotionnelle Dedikka activée",

            body=(

                "Les paiements Dedikka sont temporairement suspendus et toutes les options sont offertes. "

                "Créez gratuitement vos pages avec les photos supplémentaires, votre musique, un lien personnalisé et un QR code. "

                "Vos crédits payés sont conservés pour plus tard. Chaque page promotionnelle reste disponible pendant un an.\n\n"

                f"Créer une page : {settings.public_frontend_url.rstrip('/')}/templates.html"

            ),

            created_by=admin.id,

            created_at=now,

            status="queued",

        )

        db.add(campaign)

        db.flush()

        campaign_id = campaign.id

        _audit_admin_action(db, admin, "email.promotion.queued", "campaign", campaign.id)

    db.commit()

    if campaign_id is not None:

        background_tasks.add_task(_deliver_admin_campaign, campaign_id)

    return {

        "status": "ok",

        "payments_enabled": payload.enabled,

        "promo_mode": not payload.enabled,

        "email_campaign_id": campaign_id,

        "credit_grants_extended": credits_extended,

        "message": (

            f"Paiements réactivés. Les nouvelles créations utiliseront les crédits ou quotas habituels. {credits_extended} crédit(s) non utilisé(s) ont eu leur échéance prolongée pendant la promotion."

            if payload.enabled else

            (

                "Mode promotion gratuit activé : toutes les options sont offertes, les crédits sont conservés. "

                + ("Un e-mail est envoyé aux utilisateurs." if campaign_id is not None else "Service e-mail non configuré : aucun e-mail promotionnel n’a été envoyé.")

                if was_enabled else

                "Le mode promotion gratuit est déjà actif."

            )

        ),

    }





@app.get(settings.api_prefix + "/admin/overview", tags=["admin"])

def admin_overview(user: User = Depends(current_user), db: Session = Depends(get_db)):

    _require_admin(user)

    now = int(time.time())

    total_pages = db.scalar(select(func.count(Dedication.id))) or 0

    expired_pages = db.scalar(select(func.count(Dedication.id)).where(Dedication.expires_at.is_not(None), Dedication.expires_at <= now)) or 0

    return {

        "users": db.scalar(select(func.count(User.id))) or 0,

        "pages": total_pages,

        "active_pages": total_pages - expired_pages,

        "expired_pages": expired_pages,

        "payments_enabled": _payments_are_enabled(db),
        "maintenance_mode": _get_maintenance_mode(db),

        "email_provider_configured": bool((settings.email_api_url and settings.email_api_key) or (settings.smtp_host and settings.smtp_from_email)),

    }





@app.get(settings.api_prefix + "/admin/users", tags=["admin"])

def admin_users(

    query: str = Query(default="", max_length=120),

    page: int = Query(default=1, ge=1),

    limit: int = Query(default=50, ge=1, le=100),

    user: User = Depends(current_user),

    db: Session = Depends(get_db),

):

    _require_admin(user)

    stmt = select(User)

    if query.strip():

        pattern = f"%{query.strip()}%"

        stmt = stmt.where(or_(User.email.ilike(pattern), User.name.ilike(pattern)))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    rows = db.scalars(stmt.order_by(User.created_at.desc()).offset((page - 1) * limit).limit(limit)).all()

    return {

        "total": total,

        "items": [{

            "id": item.id,

            "name": item.name,

            "email": item.email,

            "whatsapp_number": item.whatsapp_number,

            "paid_pages_balance": item.paid_pages_balance,

            "is_blocked": item.is_blocked,

            "created_at": item.created_at,

            "pages_count": db.scalar(select(func.count(Dedication.id)).where(Dedication.user_id == item.id)) or 0,

        } for item in rows],

    }





@app.get(settings.api_prefix + "/admin/users/{user_id}/pages", tags=["admin"])
def admin_user_pages(
    user_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _require_admin(user)
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Compte utilisateur introuvable.")
    now = int(time.time())
    rows = db.scalars(select(Dedication).where(Dedication.user_id == target.id).order_by(Dedication.created_at.desc()).limit(200)).all()
    return {
        "user": {
            "id": target.id,
            "name": target.name,
            "email": target.email,
            "whatsapp_number": target.whatsapp_number,
            "paid_pages_balance": target.paid_pages_balance,
            "is_blocked": target.is_blocked,
            "created_at": target.created_at,
        },
        "items": [{
            "id": page.id,
            "title": page.title,
            "template_name": page.template_name,
            "slug": page.slug,
            "url": f"{settings.public_frontend_url.rstrip('/')}/p/{quote(page.slug)}" if settings.public_frontend_url else f"/p/{quote(page.slug)}",
            "created_at": page.created_at,
            "expires_at": page.expires_at,
            "is_expired": page.expires_at is not None and page.expires_at <= now,
        } for page in rows],
    }


@app.post(settings.api_prefix + "/admin/users/{user_id}/block", tags=["admin"])
def admin_set_user_block(
    user_id: int,
    payload: AdminUserBlockInput,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    csrf_cookie: str | None = Cookie(default=None, alias=settings.csrf_cookie_name),
):
    admin = _require_admin(user)
    _require_csrf(request, x_csrf_token, csrf_cookie)
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Compte utilisateur introuvable.")
    if target.id == admin.id or target.is_admin:
        raise HTTPException(status_code=409, detail="Un compte administrateur ne peut pas être bloqué depuis cette action.")
    target.is_blocked = payload.blocked
    if payload.blocked:
        for session in list(target.sessions):
            db.delete(session)
    _audit_admin_action(
        db, admin, "user.blocked" if payload.blocked else "user.unblocked", "user", target.id,
        {"email": target.email, "reason": payload.reason.strip() or "Action administrateur"},
    )
    db.commit()
    return {"user_id": target.id, "is_blocked": target.is_blocked, "message": "Compte bloqué." if target.is_blocked else "Compte débloqué."}


@app.delete(settings.api_prefix + "/admin/users/{user_id}", tags=["admin"])
async def admin_delete_user(
    user_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    csrf_cookie: str | None = Cookie(default=None, alias=settings.csrf_cookie_name),
):
    admin = _require_admin(user)
    _require_csrf(request, x_csrf_token, csrf_cookie)
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Compte utilisateur introuvable.")
    if target.id == admin.id or target.is_admin:
        raise HTTPException(status_code=409, detail="Un compte administrateur ne peut pas être supprimé depuis cette action.")

    target_id, target_email = target.id, target.email
    pages = db.scalars(select(Dedication).where(Dedication.user_id == target_id)).all()
    pages_count = len(pages)
    media_values: list[str] = []

    def collect_media(value):
        if isinstance(value, str):
            media_values.append(value)
        elif isinstance(value, dict):
            for nested in value.values():
                collect_media(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                collect_media(nested)

    for page in pages:
        collect_media(page.images)
        collect_media(page.form_fields)

    storage_paths: set[str] = set()
    local_files: set[Path] = set()
    storage_base = settings.supabase_url.rstrip("/")
    if storage_base.endswith("/rest/v1"):
        storage_base = storage_base[:-len("/rest/v1")]
    public_prefix = f"{storage_base}/storage/v1/object/public/{settings.supabase_bucket}/{target_id}/" if storage_base else ""
    local_prefix = f"/uploads/{target_id}/"
    for value in media_values:
        if public_prefix and value.startswith(public_prefix):
            object_name = value[len(public_prefix):].split("?", 1)[0]
            if object_name and ".." not in object_name.split("/"):
                storage_paths.add(object_name)
            continue
        path = urlsplit(value).path
        if path.startswith(local_prefix):
            filename = Path(path[len(local_prefix):]).name
            if filename and filename not in {".", ".."}:
                local_files.add(Path(settings.upload_dir) / str(target_id) / filename)

    # Delete dependent rows explicitly instead of relying only on ON DELETE
    # CASCADE: existing production databases may still have older FK rules.
    grants = db.scalars(
        select(PlanCreditGrant).where(PlanCreditGrant.user_id == target_id)
    ).all()
    for page in pages:
        db.delete(page)
    for grant in grants:
        db.delete(grant)

    db.delete(target)
    _audit_admin_action(db, admin, "user.deleted", "user", target_id, {"email": target_email, "pages_deleted": pages_count})
    db.commit()

    media_deleted = 0
    media_cleanup_failed = 0
    if storage_paths and storage_base and settings.supabase_secret_key:
        headers = {"apikey": settings.supabase_secret_key}
        if not settings.supabase_secret_key.startswith("sb_secret_"):
            headers["Authorization"] = f"Bearer {settings.supabase_secret_key}"
        semaphore = asyncio.Semaphore(8)

        async def delete_storage_object(client, object_name: str) -> bool:
            async with semaphore:
                endpoint = f"{storage_base}/storage/v1/object/{settings.supabase_bucket}/{quote(object_name, safe='/')}"
                try:
                    response = await client.delete(endpoint, headers=headers)
                    return response.status_code in {200, 204}
                except httpx.HTTPError:
                    return False

        async with httpx.AsyncClient(timeout=12.0) as client:
            results = await asyncio.gather(*(delete_storage_object(client, name) for name in storage_paths))
        media_deleted += sum(results)
        media_cleanup_failed += len(results) - sum(results)

    for file_path in local_files:
        try:
            file_path.unlink(missing_ok=True)
            media_deleted += 1
        except OSError:
            media_cleanup_failed += 1

    if media_cleanup_failed:
        logger.warning("Account deletion left %s media object(s) requiring cleanup for user %s", media_cleanup_failed, target_id)
    return {
        "user_id": target_id,
        "pages_deleted": pages_count,
        "media_deleted": media_deleted,
        "media_cleanup_failed": media_cleanup_failed,
        "message": "Compte et dédicaces associés supprimés.",
    }


@app.get(settings.api_prefix + "/admin/pages", tags=["admin"])

def admin_pages(

    query: str = Query(default="", max_length=120),

    status: Literal["all", "active", "expired"] = "all",

    page: int = Query(default=1, ge=1),

    limit: int = Query(default=50, ge=1, le=100),

    user: User = Depends(current_user),

    db: Session = Depends(get_db),

):

    _require_admin(user)

    now = int(time.time())

    stmt = select(Dedication, User).join(User, User.id == Dedication.user_id)

    if query.strip():

        pattern = f"%{query.strip()}%"

        stmt = stmt.where(or_(Dedication.title.ilike(pattern), Dedication.slug.ilike(pattern), User.email.ilike(pattern)))

    if status == "active":

        stmt = stmt.where(or_(Dedication.expires_at.is_(None), Dedication.expires_at > now))

    elif status == "expired":

        stmt = stmt.where(Dedication.expires_at.is_not(None), Dedication.expires_at <= now)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    rows = db.execute(stmt.order_by(Dedication.created_at.desc()).offset((page - 1) * limit).limit(limit)).all()

    return {

        "total": total,

        "items": [{

            "id": dedication.id,

            "title": dedication.title,

            "recipient_name": dedication.recipient_name,

            "template_name": dedication.template_name,

            "slug": dedication.slug,

            "url": f"{settings.public_frontend_url.rstrip('/')}/p/{quote(dedication.slug)}" if settings.public_frontend_url else f"/p/{quote(dedication.slug)}",

            "created_at": dedication.created_at,

            "expires_at": dedication.expires_at,

            "is_expired": dedication.expires_at is not None and dedication.expires_at <= now,

            "is_free": dedication.is_free,

            "owner_id": owner.id,

            "owner_name": owner.name,

            "owner_email": owner.email,

            "owner_balance": owner.paid_pages_balance,

        } for dedication, owner in rows],

    }





@app.post(settings.api_prefix + "/admin/users/{user_id}/credits", tags=["admin"])

def admin_add_credits(

    user_id: int,

    payload: AdminCreditInput,

    request: Request,

    user: User = Depends(current_user),

    db: Session = Depends(get_db),

    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),

    csrf_cookie: str | None = Cookie(default=None, alias=settings.csrf_cookie_name),

):

    admin = _require_admin(user)

    _require_csrf(request, x_csrf_token, csrf_cookie)

    target = db.get(User, user_id)

    if target is None:

        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")

    _grant_plan_credits(db, target, payload.plan, payload.amount)

    _audit_admin_action(db, admin, "user.credits.add", "user", target.id, {

        "amount": payload.amount, "plan": payload.plan, "reason": payload.reason.strip(),

    })

    db.commit()

    return {"user_id": target.id, "paid_pages_balance": target.paid_pages_balance}





@app.post(settings.api_prefix + "/admin/pages", status_code=201, tags=["admin"])

def admin_create_page(

    payload: AdminCreatePageInput,

    request: Request,

    background_tasks: BackgroundTasks,

    user: User = Depends(current_user),

    db: Session = Depends(get_db),

    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),

    csrf_cookie: str | None = Cookie(default=None, alias=settings.csrf_cookie_name),

):

    admin = _require_admin(user)

    _require_csrf(request, x_csrf_token, csrf_cookie)

    target = db.get(User, payload.user_id)

    if target is None:

        raise HTTPException(status_code=404, detail="Compte utilisateur introuvable.")

    if len(payload.form_fields) > 300 or any(len(k) > 150 or len(v) > 6000 for k, v in payload.form_fields.items()):

        raise HTTPException(status_code=422, detail=translate("page_data_too_large", "fr"))

    template_name = _require_available_template(payload.template_name)

    promo_mode = not _payments_are_enabled(db)

    if promo_mode:

        rights = _promotion_rights()

    elif payload.grant_source == "credit":

        rights = _consume_plan_credit(db, target)

        if rights is None:

            raise HTTPException(status_code=409, detail="Cet utilisateur n’a pas de crédit de page disponible.")

    else:

        rights = _rights_for_plan(payload.plan)

    if not promo_mode and payload.grant_source == "manual_payment" and not payload.payment_reference.strip():

        raise HTTPException(status_code=422, detail="Ajoutez la référence du paiement manuel.")

    photo_limit = _photo_limit(template_name, rights)

    base_url = str(request.base_url)

    _validate_template_images(template_name, payload.images, target.id, base_url, photo_limit, allow_extra_photos=True)

    _validate_music_url(

        _field(payload.form_fields, "music_url"), target.id, base_url,

        bool(rights.get("allow_custom_mp3")),

    )

    if payload.custom_slug and not rights.get("allow_custom_link"):

        raise HTTPException(status_code=403, detail="Le lien personnalisé n’est pas inclus dans cette formule.")



    slug = _slugify(payload.custom_slug or payload.title)

    if db.scalar(select(Dedication.id).where(Dedication.slug == slug)):

        if payload.custom_slug:

            raise HTTPException(status_code=409, detail=translate("slug_in_use", "fr"))

        slug = f"{slug}-{uuid.uuid4().hex[:7]}"

    now = int(time.time())

    page = Dedication(

        user_id=target.id, slug=slug, title=payload.title.strip(),

        recipient_name=payload.recipient_name.strip(), template_name=template_name,

        form_fields=payload.form_fields, images=payload.images,

        created_at=now, updated_at=now, is_free=False,

        expires_at=now + int(rights["page_duration_days"]) * 24 * 60 * 60,

        plan_key=rights["plan_key"], max_photos=photo_limit,

        allow_custom_mp3=rights["allow_custom_mp3"],

        allow_custom_link=rights["allow_custom_link"], allow_qr=rights["allow_qr"],

    )

    db.add(page)

    try:

        db.flush()

        _audit_admin_action(db, admin, "page.create_for_user", "page", page.id, {

            "user_id": target.id, "grant_source": payload.grant_source,

            "reason": payload.reason.strip(), "payment_reference": payload.payment_reference.strip(),

            "template_name": template_name,

        })

        db.commit()

    except IntegrityError:

        db.rollback()

        raise HTTPException(status_code=409, detail=translate("slug_in_use", "fr")) from None



    from services import send_page_created_notifications

    page_url = _admin_page_url(slug, request)

    background_tasks.add_task(

        send_page_created_notifications,

        target.email, target.whatsapp_number, target.name, page.title,

        page_url, settings.public_backend_url or str(request.base_url).rstrip("/"),

        page.allow_qr,

        page.is_free,

        int(rights["page_duration_days"]),

    )

    return {

        "id": page.id, "slug": slug, "url": page_url, "title": page.title,

        "template_name": page.template_name, "expires_at": page.expires_at,

        "plan_key": page.plan_key, "max_photos": page.max_photos,

        "allow_custom_mp3": page.allow_custom_mp3, "allow_custom_link": page.allow_custom_link,

        "allow_qr": page.allow_qr,

        "promo_mode": promo_mode,

        "owner_email": target.email,

    }





@app.post(settings.api_prefix + "/admin/pages/{page_id}/activate", tags=["admin"])

def admin_activate_page(

    page_id: int,

    payload: AdminActivatePageInput,

    request: Request,

    background_tasks: BackgroundTasks,

    user: User = Depends(current_user),

    db: Session = Depends(get_db),

    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),

    csrf_cookie: str | None = Cookie(default=None, alias=settings.csrf_cookie_name),

):

    admin = _require_admin(user)

    _require_csrf(request, x_csrf_token, csrf_cookie)

    page = db.get(Dedication, page_id)

    if page is None:

        raise HTTPException(status_code=404, detail="Dédicace introuvable.")

    owner = db.get(User, page.user_id)

    if owner is None:

        raise HTTPException(status_code=404, detail="Compte propriétaire introuvable.")

    promo_mode = not _payments_are_enabled(db)

    if promo_mode:

        rights = _promotion_rights()

    elif payload.grant_source == "credit":

        rights = _consume_plan_credit(db, owner)

        if rights is None:

            raise HTTPException(status_code=409, detail="Cet utilisateur n’a pas de crédit de page disponible.")

    else:

        rights = _rights_for_plan(payload.plan)

        if not promo_mode and payload.grant_source == "manual_payment" and not payload.payment_reference.strip():

            raise HTTPException(status_code=422, detail="Ajoutez la référence du paiement manuel.")



    now = int(time.time())

    page.expires_at = max(now, page.expires_at or now) + int(rights["page_duration_days"]) * 24 * 60 * 60

    page.updated_at = now

    page.is_free = False

    page.plan_key = rights["plan_key"]

    page.max_photos = _photo_limit(page.template_name, rights)

    page.allow_custom_mp3 = bool(rights["allow_custom_mp3"])

    page.allow_custom_link = bool(rights["allow_custom_link"])

    page.allow_qr = bool(rights["allow_qr"])

    _audit_admin_action(db, admin, "page.activate", "page", page.id, {

        "user_id": owner.id, "grant_source": payload.grant_source,

        "reason": payload.reason.strip(), "payment_reference": payload.payment_reference.strip(),

        "expires_at": page.expires_at,

    })

    db.commit()

    from services import send_page_created_notifications

    page_url = _admin_page_url(page.slug, request)

    background_tasks.add_task(

        send_page_created_notifications,

        owner.email, owner.whatsapp_number, owner.name, page.title,

        page_url, settings.public_backend_url or str(request.base_url).rstrip("/"),

        page.allow_qr,

        page.is_free,

        int(rights["page_duration_days"]),

    )

    return {"id": page.id, "slug": page.slug, "url": page_url, "expires_at": page.expires_at, "promo_mode": promo_mode}





def _deliver_admin_campaign(campaign_id: int) -> None:

    from services import send_admin_announcement_email

    with SessionLocal() as db:

        campaign = db.get(AdminEmailCampaign, campaign_id)

        if campaign is None:

            return

        campaign.status = "sending"

        recipients = [(item.email, item.name) for item in db.scalars(select(User).order_by(User.id)).all()]

        campaign.recipient_count = len(recipients)

        subject, body = campaign.subject, campaign.body

        db.commit()

    sent = failed = 0

    for email, name in recipients:

        if send_admin_announcement_email(email, name, subject, body):

            sent += 1

        else:

            failed += 1

    with SessionLocal() as db:

        campaign = db.get(AdminEmailCampaign, campaign_id)

        if campaign is not None:

            campaign.sent_count = sent

            campaign.failed_count = failed

            campaign.status = "complete" if not failed else "complete_with_errors"

            db.commit()





@app.post(settings.api_prefix + "/admin/announcements", status_code=202, tags=["admin"])

def admin_send_announcement(

    payload: AdminAnnouncementInput,

    request: Request,

    background_tasks: BackgroundTasks,

    user: User = Depends(current_user),

    db: Session = Depends(get_db),

    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),

    csrf_cookie: str | None = Cookie(default=None, alias=settings.csrf_cookie_name),

):

    admin = _require_admin(user)

    _require_csrf(request, x_csrf_token, csrf_cookie)

    if not ((settings.email_api_url and settings.email_api_key) or (settings.smtp_host and settings.smtp_from_email)):

        raise HTTPException(status_code=503, detail="Le service d’envoi d’e-mails n’est pas configuré.")

    campaign = AdminEmailCampaign(

        subject=payload.subject.strip(), body=payload.body.strip(), created_by=admin.id,

        created_at=int(time.time()), status="queued",

    )

    db.add(campaign)

    db.flush()

    _audit_admin_action(db, admin, "email.announcement.queued", "campaign", campaign.id, {"subject": campaign.subject})

    db.commit()

    background_tasks.add_task(_deliver_admin_campaign, campaign.id)

    return {"id": campaign.id, "status": campaign.status}





@app.get(settings.api_prefix + "/admin/announcements", tags=["admin"])

def admin_campaigns(user: User = Depends(current_user), db: Session = Depends(get_db)):

    _require_admin(user)

    rows = db.scalars(select(AdminEmailCampaign).order_by(AdminEmailCampaign.created_at.desc()).limit(30)).all()

    return [{

        "id": item.id, "subject": item.subject, "status": item.status,

        "recipient_count": item.recipient_count, "sent_count": item.sent_count,

        "failed_count": item.failed_count, "created_at": item.created_at,

    } for item in rows]





@app.get(settings.api_prefix + "/admin/audit", tags=["admin"])

def admin_audit(user: User = Depends(current_user), db: Session = Depends(get_db)):

    _require_admin(user)

    rows = db.scalars(select(AdminAuditLog).order_by(AdminAuditLog.created_at.desc()).limit(100)).all()

    return [{

        "id": item.id, "actor_user_id": item.actor_user_id, "action": item.action,

        "target_type": item.target_type, "target_id": item.target_id,

        "details": item.details, "created_at": item.created_at,

    } for item in rows]





@app.delete(settings.api_prefix + "/admin/pages/{page_id}", tags=["admin"])

def admin_delete_page(

    page_id: int,

    request: Request,

    user: User = Depends(current_user),

    db: Session = Depends(get_db),

    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),

    csrf_cookie: str | None = Cookie(default=None, alias=settings.csrf_cookie_name),

):

    admin = _require_admin(user)

    _require_csrf(request, x_csrf_token, csrf_cookie)

    page = db.get(Dedication, page_id)

    if page is None:

        raise HTTPException(status_code=404, detail="Dédicace introuvable.")

    owner_id = page.user_id

    page_slug = page.slug

    db.delete(page)

    _audit_admin_action(db, admin, "page.delete", "page", page_id, {"slug": page_slug, "owner_id": owner_id})

    db.commit()

    return {"status": "ok", "message": "Dédicace supprimée avec succès."}





@app.get(settings.api_prefix + "/admin/sales", tags=["admin"])

async def admin_get_chariow_sales(user: User = Depends(current_user)):

    _require_admin(user)

    if not settings.chariow_api_key:

        return {"items": []}

    headers = {

        "Authorization": f"Bearer {settings.chariow_api_key}",

        "Accept": "application/json",

    }

    try:

        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:

            resp = await client.get(f"{settings.chariow_base_url.rstrip('/')}/sales?per_page=20", headers=headers)

        if resp.status_code == 200:

            return {"items": resp.json().get("data", [])}

        return {"items": [], "error": f"Chariow API status {resp.status_code}"}

    except Exception as exc:

        return {"items": [], "error": str(exc)}





# Frontend and project files are served only by the local development server.

site_root = Path(__file__).resolve().parent.parent

if is_development and (site_root / "frontend").exists():

    app.mount("/frontend", StaticFiles(directory=str(site_root / "frontend")), name="frontend")

if is_development and (site_root / "build").exists():

    app.mount("/build", StaticFiles(directory=str(site_root / "build")), name="build")

galaxy_gallery_assets = Path(__file__).resolve().parent / "template" / "galaxy_gallery"

if galaxy_gallery_assets.exists():

    app.mount("/template/galaxy_gallery", StaticFiles(directory=str(galaxy_gallery_assets)), name="galaxy_gallery_assets")



if is_development and (site_root / "images").exists():

    app.mount("/images", StaticFiles(directory=str(site_root / "images")), name="images")

if is_development and site_root.exists():

    app.mount("/", StaticFiles(directory=str(site_root), html=True), name="site_root")
