"""Image, messaging, and expiration reminder services."""
import asyncio
import logging
import re
import smtplib
import ssl
import time
import uuid
import warnings
from email.message import EmailMessage
from html import escape
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

import httpx
import pillow_avif  # noqa: F401 - registers AVIF decoding with Pillow
from fastapi import HTTPException, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener
from sqlalchemy import select

from accounts import User
from core import Base, Lang, SessionLocal, engine, ensure_schema_compatibility, settings, translate

from qr_generator import generate_heart_qr


logger = logging.getLogger("dedikka.expiration_reminders")
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_IMAGE_PIXELS = 60_000_000
MAX_IMAGE_EDGE = 2400
OUTPUT_IMAGE_BYTES = 3 * 1024 * 1024
MAX_AUDIO_BYTES = 15 * 1024 * 1024
EMAIL_TEMPLATE_DIR = Path(__file__).resolve().parent / "template"

register_heif_opener()
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


def _normalize_image(data: bytes, lang: Lang) -> tuple[bytes, str, str]:
    """Decode mobile and desktop photo formats, orient, resize and compress photos."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            source = Image.open(BytesIO(data))
        with source:
            if source.format not in {"JPEG", "PNG", "WEBP", "GIF", "BMP", "TIFF", "HEIF", "AVIF"}:
                raise HTTPException(status_code=415, detail=translate("image_format_unsupported", lang))
            source.seek(0)
            if source.format == "GIF" and getattr(source, "is_animated", False):
                source.load()
                return data, "image/gif", "gif"
            image = ImageOps.exif_transpose(source.copy())
            image.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.Resampling.LANCZOS)

            # Keep transparency when present; otherwise encode RGB to reduce storage.
            if "A" in image.getbands() or "transparency" in image.info:
                image = image.convert("RGBA")
            else:
                image = image.convert("RGB")

            for quality in (84, 76, 68):
                output = BytesIO()
                image.save(output, format="WEBP", quality=quality, method=5)
                encoded = output.getvalue()
                if len(encoded) <= OUTPUT_IMAGE_BYTES:
                    return encoded, "image/webp", "webp"
            if len(encoded) <= MAX_IMAGE_BYTES:
                return encoded, "image/webp", "webp"
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning, ValueError) as exc:
        raise HTTPException(status_code=415, detail=translate("image_invalid_content", lang)) from exc

    raise HTTPException(status_code=413, detail=translate("image_too_large", lang))


def _supabase_project_url() -> str:
    """Accept the project URL and normalize a copied /rest/v1 URL for Storage."""
    base = settings.supabase_url.rstrip("/")
    if base.endswith("/rest/v1"):
        base = base[:-len("/rest/v1")]
    return base


def get_public_image_prefix(base_url: str = "") -> str:
    """Returns the base URL prefix for images, depending on Supabase or local storage."""
    if settings.supabase_url and settings.supabase_secret_key:
        return f"{_supabase_project_url()}/storage/v1/object/public/{settings.supabase_bucket}/"
    base = (settings.public_backend_url or base_url).rstrip("/")
    return f"{base}/uploads/" if base else "/uploads/"


def is_valid_user_image(url: str, user_id: int, base_url: str = "") -> bool:
    """Checks whether an image URL belongs to the user's storage directory."""
    if not url:
        return False
    if settings.supabase_url:
        supa_prefix = f"{_supabase_project_url()}/storage/v1/object/public/{settings.supabase_bucket}/{user_id}/"
        if url.startswith(supa_prefix):
            return True
    local_prefix = f"/uploads/{user_id}/"
    if url.startswith(local_prefix):
        return True
    base = (settings.public_backend_url or base_url).rstrip("/")
    if base and url.startswith(f"{base}/uploads/{user_id}/"):
        return True
    return url.startswith("data:image/")


def is_valid_user_audio(url: str, user_id: int, base_url: str = "") -> bool:
    """Checks that a personal MP3 URL belongs to its owner's storage directory."""
    if not url or not url.lower().endswith(".mp3"):
        return False
    if settings.supabase_url:
        prefix = f"{_supabase_project_url()}/storage/v1/object/public/{settings.supabase_bucket}/{user_id}/"
        if url.startswith(prefix):
            return True
    prefix = f"/uploads/{user_id}/"
    if url.startswith(prefix):
        return True
    base = (settings.public_backend_url or base_url).rstrip("/")
    return bool(base and url.startswith(f"{base}/uploads/{user_id}/"))


async def save_image(file: UploadFile, user_id: int, base_url: str = "", lang: Lang = "fr") -> dict[str, str]:
    """Validate, normalize and save an uploaded photo as compact WebP."""
    data = await file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail=translate("image_too_large", lang))
    if not data:
        raise HTTPException(status_code=415, detail=translate("image_invalid_content", lang))

    data, content_type, extension = await asyncio.to_thread(_normalize_image, data, lang)

    filename = f"{uuid.uuid4().hex}.{extension}"
    object_path = f"{user_id}/{filename}"

    if settings.supabase_url and settings.supabase_secret_key:
        endpoint = f"{_supabase_project_url()}/storage/v1/object/{settings.supabase_bucket}/{object_path}"
        headers = {
            "apikey": settings.supabase_secret_key,
            "Content-Type": content_type,
            "x-upsert": "false",
        }
        # sb_secret keys are API keys, not JWTs: do not send them as Bearer tokens.
        if not settings.supabase_secret_key.startswith("sb_secret_"):
            headers["Authorization"] = f"Bearer {settings.supabase_secret_key}"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(endpoint, content=data, headers=headers)
            if response.status_code not in (200, 201):
                logger.error(
                    "Supabase Storage rejected image upload: status=%s",
                    response.status_code,
                )
                raise HTTPException(status_code=502, detail=translate("storage_upload_failed", lang))
        except (httpx.RequestError, httpx.TimeoutException) as exc:
            logger.exception("Supabase Storage image upload could not reach the project")
            raise HTTPException(status_code=502, detail=translate("storage_unreachable", lang)) from exc

        return {
            "url": f"{get_public_image_prefix()}{object_path}",
            "path": object_path,
        }

    if settings.app_env.lower() in {"production", "prod"}:
        raise HTTPException(
            status_code=503,
            detail=translate("storage_not_configured", lang),
        )

    # Persistent bucket storage is not configured in local development; use disk only there.
    upload_root = Path(settings.upload_dir)
    user_dir = upload_root / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    file_path = user_dir / filename
    with open(file_path, "wb") as f:
        f.write(data)

    base = (settings.public_backend_url or base_url).rstrip("/")
    url = f"{base}/uploads/{user_id}/{filename}" if base else f"/uploads/{user_id}/{filename}"
    return {"url": url, "path": object_path}


async def save_audio(file: UploadFile, user_id: int, base_url: str = "", lang: Lang = "fr") -> dict[str, str]:
    """Accept a bounded MP3 upload and store it under the uploader's directory."""
    filename = (file.filename or "").strip()
    if not filename.lower().endswith(".mp3"):
        raise HTTPException(status_code=415, detail="Seuls les fichiers MP3 sont acceptés.")
    data = await file.read(MAX_AUDIO_BYTES + 1)
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="La musique doit faire 15 Mo maximum.")
    is_mp3 = data.startswith(b"ID3") or (
        len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0
    )
    if not is_mp3:
        raise HTTPException(status_code=415, detail="Le fichier choisi ne semble pas être un MP3 valide.")

    object_path = f"{user_id}/music-{uuid.uuid4().hex}.mp3"
    if settings.supabase_url and settings.supabase_secret_key:
        endpoint = f"{_supabase_project_url()}/storage/v1/object/{settings.supabase_bucket}/{object_path}"
        headers = {
            "apikey": settings.supabase_secret_key,
            "Content-Type": "audio/mpeg",
            "x-upsert": "false",
        }
        if not settings.supabase_secret_key.startswith("sb_secret_"):
            headers["Authorization"] = f"Bearer {settings.supabase_secret_key}"
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(endpoint, content=data, headers=headers)
            if response.status_code not in (200, 201):
                logger.error("Supabase Storage rejected MP3 upload: status=%s", response.status_code)
                raise HTTPException(status_code=502, detail="Le stockage a refusé l’envoi de la musique.")
        except (httpx.RequestError, httpx.TimeoutException) as exc:
            logger.exception("Supabase Storage could not receive MP3 upload")
            raise HTTPException(status_code=502, detail="Le stockage est temporairement inaccessible.") from exc
        return {"url": f"{get_public_image_prefix()}{object_path}", "path": object_path}

    if settings.app_env.lower() in {"production", "prod"}:
        raise HTTPException(status_code=503, detail=translate("storage_not_configured", lang))
    user_dir = Path(settings.upload_dir) / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    file_path = user_dir / Path(object_path).name
    await asyncio.to_thread(file_path.write_bytes, data)
    base = (settings.public_backend_url or base_url).rstrip("/")
    url = f"{base}/uploads/{object_path}" if base else f"/uploads/{object_path}"
    return {"url": url, "path": object_path}




async def send_page_created_whatsapp(
    phone: str | None,
    name: str,
    title: str,
    page_url: str,
    backend_url: str,
    allow_qr: bool = True,
) -> None:
    """Send the published link and a direct PNG QR link through PyWAGate."""
    if not phone:
        return
    if not settings.pywagate_base_url or not settings.pywagate_api_key:
        logger.info("WhatsApp publication notification skipped: PyWAGate is not configured")
        return

    recipient = "".join(character for character in phone if character.isdigit())
    if not 8 <= len(recipient) <= 15:
        logger.warning("WhatsApp publication notification skipped: invalid saved phone number")
        return

    safe_name = " ".join(name.split())
    safe_title = " ".join(title.split())[:120] or "Votre dédicace"
    message = (
        f"Bonjour {safe_name}, votre dédicace « {safe_title} » est prête.\n\n"
        f"Lien : {page_url}"
    )
    if allow_qr:
        qr_url = f"{backend_url.rstrip('/')}/api/v1/qr-heart?{urlencode({'url': page_url})}"
        message += f"\nCode QR PNG : {qr_url}"
    endpoint = f"{settings.pywagate_base_url.rstrip('/')}/api/messages/send"
    payload = {
        "session_id": settings.pywagate_session_id,
        "to": recipient,
        "message": message,
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12.0)) as client:
            response = await client.post(
                endpoint,
                json=payload,
                headers={"X-API-Key": settings.pywagate_api_key},
            )
        if response.status_code != 200:
            logger.error("PyWAGate rejected a publication notification: HTTP %s", response.status_code)
            return
        result = response.json()
        if not isinstance(result, dict) or result.get("success") is not True:
            logger.error("PyWAGate did not confirm a publication notification")
    except (httpx.HTTPError, ValueError):
        logger.exception("Could not send publication notification through PyWAGate")


async def send_page_created_notifications(
    email: str,
    phone: str | None,
    name: str,
    title: str,
    page_url: str,
    backend_url: str,
    allow_qr: bool = True,
    is_free: bool = True,
    duration_days: int = 0,
) -> None:
    """Send the page-created e-mail and WhatsApp notification concurrently."""
    channels = ("e-mail", "WhatsApp")
    results = await asyncio.gather(
        send_page_created_email(email, name, title, page_url, backend_url, allow_qr, is_free, duration_days),
        send_page_created_whatsapp(phone, name, title, page_url, backend_url, allow_qr),
        return_exceptions=True,
    )
    for channel, result in zip(channels, results):
        if isinstance(result, BaseException):
            logger.error(
                "Publication %s notification task crashed",
                channel,
                exc_info=(type(result), result, result.__traceback__),
            )
        elif result is False:
            logger.warning("Publication %s notification was not delivered", channel)


def _qr_png(url: str) -> bytes:
    # A 512px source remains sharp for the 220px e-mail preview and is much
    # faster to render than the 1024px default on the web worker.
    image = generate_heart_qr(url, size=512)
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _duration_hours_label(hours: int) -> str:
    return "une heure" if hours == 1 else f"{hours} heures"


def _duration_minutes_label(minutes: int) -> str:
    if minutes == 60:
        return "une heure"
    hours, remainder = divmod(minutes, 60)
    if hours == 0:
        return f"{remainder} minutes"
    label = _duration_hours_label(hours)
    return f"{label} et {remainder} minutes" if remainder else label


def _duration_days_label(days: int) -> str:
    if days == 365:
        return "1 an"
    if days == 30:
        return "30 jours"
    return f"{days} jours"


def _read_email_template(file_name: str) -> str:
    """Lit le modèle en UTF-8, ou en ISO-8859-1 si le fichier est encore dans cet encodage."""
    raw = (EMAIL_TEMPLATE_DIR / file_name).read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("iso-8859-1")


def _render_email_template(file_name: str, values: dict[str, str]) -> str:
    """Remplace chaque {{CLE}} par sa valeur déjà échappée, en une seule passe."""
    template = _read_email_template(file_name)
    return re.sub(
        r"\{\{(\w+)\}\}",
        lambda match: values.get(match.group(1), ""),
        template,
    )


def _publication_email_content(
    name: str,
    title: str,
    page_url: str,
    backend_url: str,
    allow_qr: bool = True,
    is_free: bool = True,
    duration_days: int = 0,
) -> tuple[str, str, str, str]:
    """Construit l'e-mail « dédicace prête » à partir de template/email-page-created.html."""
    safe_name = " ".join(name.split())
    safe_title = " ".join(title.split())[:120] or "Votre dédicace"
    qr_url = (
        f"{backend_url.rstrip('/')}/api/v1/qr-heart?{urlencode({'url': page_url})}"
        if backend_url and allow_qr else ""
    )
    duration_label = _duration_hours_label(settings.free_page_duration_hours) if is_free else _duration_days_label(duration_days)
    reminder_label = _duration_minutes_label(settings.page_expiration_reminder_minutes) if is_free else ""
    duration_info = (
        f"Cette page gratuite reste disponible pendant <strong>{escape(duration_label)}</strong>. "
        f"Un rappel vous sera envoyé <strong>{escape(reminder_label)}</strong> avant son expiration."
        if is_free else
        f"Cette page reste disponible pendant <strong>{escape(duration_label)}</strong>, selon votre formule."
    )
    subject = f"Votre dédicace « {safe_title} » est prête"
    text_body = (
        f"Bonjour {safe_name},\n\n"
        f"Votre dédicace « {safe_title} » est publiée.\n"
        f"Lien : {page_url}\n"
        + (f"Cette page gratuite reste disponible pendant {duration_label}. Un rappel vous sera envoyé {reminder_label} avant son expiration.\n" if is_free else f"Cette page reste disponible pendant {duration_label}, selon votre formule.\n")
        + (f"Code QR PNG : {qr_url}\n" if qr_url else "")
        + "\nL’équipe Dedikka"
    )
    safe_qr_url = escape(qr_url, quote=True)
    qr_block = (
        f'<a href="{safe_qr_url}" style="display:inline-block;text-decoration:none;">'
        f'<img src="{safe_qr_url}" width="220" height="220" '
        'alt="Code QR de votre dédicace — ouvrir ou télécharger" '
        'style="display:block;margin:0 auto;width:220px;max-width:100%;height:auto;border:0;"></a>'
        f'<p style="margin:10px 0 0;font-size:13px;line-height:20px;">'
        f'<a href="{safe_qr_url}" style="color:#7454ef;">Afficher ou télécharger le code QR</a></p>'
        if qr_url else ""
    )
    html_body = _render_email_template(
        "email-page-created.html",
        {
            "NAME": escape(safe_name),
            "TITLE": escape(safe_title),
            "PAGE_URL": escape(page_url, quote=True),
            "REMINDER": escape(reminder_label),
            "DURATION": escape(duration_label),
            "DURATION_INFO": duration_info,
            "QR_BLOCK": qr_block,
        },
    )
    return subject, text_body, html_body, qr_url


def _send_email_message(
    recipient: str,
    subject: str,
    text_body: str,
    html_body: str,
    attachment_png: bytes | None = None,
    attachment_name: str = "dedikka-code-qr.png",
) -> bool:
    """Deliver one message through the configured API, or SMTP when API is unset."""
    if not recipient:
        logger.warning("Publication e-mail skipped: account has no e-mail address")
        return False

    if settings.email_api_url and settings.email_api_key:
        try:
            response = httpx.post(
                settings.email_api_url,
                json={"to": recipient, "subject": subject, "html": html_body, "text": text_body},
                headers={
                    "Authorization": f"Bearer {settings.email_api_key}",
                    "X-API-Key": settings.email_api_key,
                },
                timeout=httpx.Timeout(20.0),
            )
            if not 200 <= response.status_code < 300:
                logger.error("Email API rejected a notification: HTTP %s", response.status_code)
                return False
            try:
                result = response.json()
            except ValueError:
                result = None
            if isinstance(result, dict) and result.get("success") is False:
                logger.error("Email API did not confirm a notification")
                return False
            logger.info("Notification e-mail submitted to the configured API")
            return True
        except httpx.HTTPError:
            logger.exception("Could not send a notification through the configured e-mail API")
            return False

    if not settings.smtp_host or not settings.smtp_from_email:
        logger.warning("Notification e-mail skipped: no e-mail provider is configured")
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from_email
    message["To"] = recipient
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")
    if attachment_png:
        message.add_attachment(
            attachment_png,
            maintype="image",
            subtype="png",
            filename=attachment_name,
        )

    try:
        if settings.smtp_use_ssl:
            with smtplib.SMTP_SSL(
                settings.smtp_host,
                settings.smtp_port,
                timeout=15,
                context=ssl.create_default_context(),
            ) as server:
                if settings.smtp_username:
                    server.login(settings.smtp_username, settings.smtp_password)
                refused = server.send_message(message)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
                server.ehlo()
                if settings.smtp_starttls:
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                if settings.smtp_username:
                    server.login(settings.smtp_username, settings.smtp_password)
                refused = server.send_message(message)
        if refused:
            logger.error("SMTP refused a notification recipient")
            return False
        logger.info("Notification e-mail sent through SMTP")
        return True
    except Exception:
        logger.exception("Could not send a notification through SMTP")
        return False


def send_admin_announcement_email(recipient: str, name: str, subject: str, body: str) -> bool:
    """Send one admin announcement using the configured mail provider and external HTML template."""
    safe_name = " ".join((name or "").split())[:100] or ""
    safe_subject = " ".join((subject or "").split())[:180]
    paragraphs = [part.strip() for part in (body or "").splitlines() if part.strip()]
    safe_message_html = "<br>".join(escape(part) for part in paragraphs)
    html_body = _render_email_template("email-admin-announcement.html", {
        "NAME": escape(safe_name),
        "SUBJECT": escape(safe_subject),
        "MESSAGE": safe_message_html,
    })
    text_body = f"Bonjour {safe_name},\n\n{body.strip()}\n\nL’équipe Dedikka"
    return _send_email_message(recipient, safe_subject, text_body, html_body)


async def send_page_created_email(
    recipient: str,
    name: str,
    title: str,
    page_url: str,
    backend_url: str = "",
    allow_qr: bool = True,
    is_free: bool = True,
    duration_days: int = 0,
) -> bool:
    """Send the publication notice without blocking the API worker's event loop."""
    if not recipient:
        logger.warning("Publication e-mail skipped: account has no e-mail address")
        return False

    subject, text_body, html_body, _ = _publication_email_content(
        name, title, page_url, backend_url, allow_qr, is_free, duration_days
    )

    if settings.email_api_url and settings.email_api_key:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
                response = await client.post(
                    settings.email_api_url,
                    json={"to": recipient, "subject": subject, "html": html_body, "text": text_body},
                    headers={
                        "Authorization": f"Bearer {settings.email_api_key}",
                        "X-API-Key": settings.email_api_key,
                    },
                )
            if not 200 <= response.status_code < 300:
                logger.error("Email API rejected a publication e-mail: HTTP %s", response.status_code)
                return False
            try:
                result = response.json()
            except ValueError:
                result = None
            if isinstance(result, dict) and (
                result.get("success") is False
                or bool(result.get("error"))
                or str(result.get("status", "")).lower() in {"error", "failed", "failure"}
            ):
                logger.error("Email API returned a rejection for a publication e-mail")
                return False
            logger.info("Publication e-mail accepted by the configured e-mail API")
            return True
        except httpx.HTTPError:
            logger.exception("Could not send a publication e-mail through the configured e-mail API")
            return False

    # SMTP remains the fallback; image generation and SMTP I/O run off the event loop.
    attachment = None
    if allow_qr and settings.smtp_host and settings.smtp_from_email:
        try:
            attachment = await asyncio.to_thread(_qr_png, page_url)
        except Exception:
            logger.exception("Could not generate the QR attachment for a publication e-mail")
            return False
    return await asyncio.to_thread(
        _send_email_message, recipient, subject, text_body, html_body, attachment
    )


def _expiration_reminder_content(name: str, title: str, page_url: str) -> tuple[str, str, str]:
    """Construit le rappel d'expiration à partir de template/email-expiration-reminder.html."""
    safe_name = " ".join(name.split())
    safe_title = " ".join(title.split())[:120] or "Votre dédicace"
    duration_label = _duration_hours_label(settings.free_page_duration_hours)
    reminder_label = _duration_minutes_label(settings.page_expiration_reminder_minutes)
    parsed_page_url = urlsplit(page_url)
    plans_url = (
        f"{parsed_page_url.scheme}://{parsed_page_url.netloc}/plans.html"
        if parsed_page_url.scheme in {"http", "https"} and parsed_page_url.netloc
        else ""
    )
    subject = f"Votre dédicace expire dans {reminder_label}"
    text_body = (
        f"Bonjour {safe_name},\n\n"
        f"Votre dédicace « {safe_title} » expirera dans environ {reminder_label}, soit {duration_label} après sa publication.\n"
        f"Ouvrir la dédicace : {page_url}\n"
        + (f"Consulter les offres : {plans_url}\n" if plans_url else "")
        + "\nL’équipe Dedikka"
    )
    plans_button = (
        f'<p style="margin:12px 0 24px"><a href="{escape(plans_url, quote=True)}" '
        'style="display:inline-block;border:1px solid #7454ef;color:#6043d8;text-decoration:none;'
        'font-weight:700;padding:12px 20px;border-radius:12px">Consulter les offres</a></p>'
        if plans_url else ""
    )
    html_body = _render_email_template(
        "email-expiration-reminder.html",
        {
            "NAME": escape(safe_name),
            "TITLE": escape(safe_title),
            "PAGE_URL": escape(page_url, quote=True),
            "REMINDER": escape(reminder_label),
            "DURATION": escape(duration_label),
            "PLANS_BUTTON": plans_button,
        },
    )
    return subject, text_body, html_body


def send_expiration_reminder_email(recipient: str, name: str, title: str, page_url: str) -> bool:
    """Send the one-hour expiration reminder to the account owner."""
    subject, text_body, html_body = _expiration_reminder_content(name, title, page_url)
    return _send_email_message(recipient, subject, text_body, html_body)


def _public_page_url(slug: str) -> str:
    base_url = settings.public_frontend_url.rstrip("/") or settings.public_backend_url.rstrip("/")
    return f"{base_url}/p/{quote(slug)}" if base_url else ""


def send_due_reminders() -> int:
    """Process eligible pages; the row lock and sent timestamp prevent repeats."""
    from main import Dedication
    if not ((settings.email_api_url and settings.email_api_key) or (settings.smtp_host and settings.smtp_from_email)):
        logger.error("Expiration reminders skipped: no e-mail provider is configured")
        return 0

    Base.metadata.create_all(bind=engine)
    ensure_schema_compatibility()
    lead_seconds = max(1, settings.page_expiration_reminder_minutes) * 60
    now = int(time.time())

    with SessionLocal() as db:
        due_ids = db.scalars(
            select(Dedication.id)
            .where(
                Dedication.expires_at.is_not(None),
                Dedication.is_free.is_(True),
                Dedication.expires_at > now,
                Dedication.expires_at <= now + lead_seconds,
                Dedication.expiration_reminder_sent_at.is_(None),
            )
            .order_by(Dedication.expires_at)
        ).all()
        db.rollback()

    sent_count = 0
    for page_id in due_ids:
        with SessionLocal() as db:
            now = int(time.time())
            page = db.scalar(
                select(Dedication)
                .where(
                    Dedication.id == page_id,
                    Dedication.expires_at.is_not(None),
                    Dedication.is_free.is_(True),
                    Dedication.expires_at > now,
                    Dedication.expires_at <= now + lead_seconds,
                    Dedication.expiration_reminder_sent_at.is_(None),
                )
                .with_for_update(skip_locked=True)
            )
            if page is None:
                db.rollback()
                continue

            owner = db.get(User, page.user_id)
            page_url = _public_page_url(page.slug)
            if owner is None or not owner.email or not page_url:
                logger.warning("Expiration reminder skipped for page id %s: owner or public URL missing", page.id)
                db.rollback()
                continue

            delivered = send_expiration_reminder_email(
                owner.email,
                owner.name,
                page.title,
                page_url,
            )
            if not delivered:
                db.rollback()
                continue

            page.expiration_reminder_sent_at = int(time.time())
            db.commit()
            sent_count += 1

    logger.info("Expiration reminder run complete: %s message(s) sent", sent_count)
    return sent_count


# ==========================================
# GESTION DES PAIEMENTS CHARIOW (MOBILE MONEY)
# ==========================================

async def create_chariow_checkout_session(plan_key: str, phone_number: str, user_email: str, user_name: str) -> dict:
    """Initie un paiement Mobile Money avec l'API Chariow."""
    from core import OFFICIAL_PLANS, settings
    plan_info = OFFICIAL_PLANS.get(plan_key.lower().strip())
    if not plan_info:
        raise HTTPException(status_code=422, detail="Formule de dédicace invalide.")

    # Nettoyer le numéro de téléphone (enlever indicatif +243 ou 00243 s'il est présent)
    clean_digits = re.sub(r"\D", "", phone_number)
    if clean_digits.startswith("243"):
        clean_digits = clean_digits[3:]
    elif clean_digits.startswith("0"):
        clean_digits = clean_digits[1:]

    first_name = user_name.split()[0] if user_name else "Client"
    last_name = " ".join(user_name.split()[1:]) if len(user_name.split()) > 1 else "Dedikka"

    headers = {
        "Authorization": f"Bearer {settings.chariow_api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "product_id": plan_info["product_id"],
        "email": user_email,
        "first_name": first_name,
        "last_name": last_name,
        "phone": {
            "country_code": "CD",
            "number": clean_digits,
        },
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            response = await client.post(
                f"{settings.chariow_base_url.rstrip('/')}/checkout",
                headers=headers,
                json=payload,
            )
        except Exception as exc:
            logger.exception("Chariow checkout request failed")
            raise HTTPException(status_code=503, detail="Passerelle de paiement temporairement indisponible.") from exc

    if response.status_code in {200, 201}:
        res_data = response.json()
        return {
            "status": "success",
            "message": "Paiement Mobile Money initié. Validez sur votre téléphone.",
            "data": res_data.get("data", {}),
        }

    # Si erreur de validation Chariow
    err_body = response.text
    logger.error("Chariow error %s: %s", response.status_code, err_body)
    raise HTTPException(status_code=400, detail="Échec de l'initiation du paiement Mobile Money. Vérifiez votre numéro.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    send_due_reminders()
