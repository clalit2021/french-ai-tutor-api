# app/tasks.py
import os
import json
import tempfile
from datetime import datetime

import requests
from celery import Celery
from celery.utils.log import get_task_logger
from supabase import create_client, Client

from openai import OpenAI
from app import mimi  # uses the strict JSON lesson builder

logger = get_task_logger(__name__)

# ------------------------------------------------------------------------------
# Celery
# ------------------------------------------------------------------------------
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "")  # e.g. redis://localhost:6379/0
if not CELERY_BROKER_URL:
    logger.warning("[CELERY] CELERY_BROKER_URL is not set. Background jobs will not run.")

celery_app = Celery("tasks", broker=CELERY_BROKER_URL or None)
celery_app.conf.broker_connection_retry_on_startup = True
celery_app.conf.result_backend = CELERY_BROKER_URL or None
celery_app.conf.task_ignore_result = True
celery_app.conf.worker_send_task_events = True
celery_app.conf.task_send_sent_event = True

# ------------------------------------------------------------------------------
# Supabase
# ------------------------------------------------------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
supabase: Client | None = None
if SUPABASE_URL and SUPABASE_SERVICE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
else:
    logger.warning("[SB] Supabase env vars missing; DB updates will fail.")

# ------------------------------------------------------------------------------
# OpenAI
# ------------------------------------------------------------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_VISION_MODEL = os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini")
openai_client: OpenAI | None = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------
def _public_storage_url(path: str) -> str:
    """
    Convert 'uploads/dir/file.pdf' (path includes bucket at start) to a public URL.
    Assumes the object is in a PUBLIC bucket (e.g., 'uploads').
    """
    path = (path or "").lstrip("/")
    return f"{SUPABASE_URL}/storage/v1/object/public/{path}"

def _sb_update_lesson(lesson_id: str, fields: dict):
    if not supabase:
        logger.warning("[SB] update skipped; Supabase not configured")
        return
    try:
        supabase.table("lessons").update(fields).eq("id", lesson_id).execute()
    except Exception as e:
        logger.error(f"[SB] update error: {e}", exc_info=True)

def _extract_pdf_text(bytes_data: bytes) -> str:
    """
    Fast text extraction via PyMuPDF if available; returns empty string if not.
    """
    try:
        import fitz  # PyMuPDF
    except Exception:
        logger.warning("[OCR] PyMuPDF not installed; skipping direct PDF extraction.")
        return ""

    text = ""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(bytes_data)
        tmp_pdf = f.name
    try:
        doc = fitz.open(tmp_pdf)
        for page in doc:
            try:
                page_text = page.get_text() or ""
                text += page_text
            except Exception:
                pass
        doc.close()
    finally:
        try:
            os.remove(tmp_pdf)
        except Exception:
            pass
    return text

def _vision_describe_image(image_url: str) -> str:
    """
    Uses OpenAI multimodal chat to read text/describe the image for a kid-friendly lesson seed.
    """
    if not openai_client:
        return "Image: description indisponible (clé API manquante)."

    try:
        messages = [
            {
                "role": "system",
                "content": (
                    "You transcribe and summarize visible text from images in French when present. "
                    "If little text is present, briefly describe the scene in French for an 11-year-old learner."
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Lis l'image et donne les éléments clés pour une mini-leçon (enfant 11 ans)."},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            },
        ]
        resp = openai_client.chat.completions.create(
            model=OPENAI_VISION_MODEL,
            temperature=0.2,
            messages=messages,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning(f"[VISION] failed: {e}")
        return "Description d'image non disponible."

# ------------------------------------------------------------------------------
# Task
# ------------------------------------------------------------------------------
@celery_app.task(name="tasks.process_lesson", bind=True)
def process_lesson(self, lesson_id: str, file_path: str, child_id: str):
    """
    Background job:
    - Downloads the uploaded asset from Supabase public bucket
    - Extracts OCR/text if PDF, or generates a brief description if image
    - Builds a strict-schema lesson via app.mimi.build_mimi_lesson(...)
    - Updates the lessons row with lesson_data and status
    """
    logger.info(f"[JOB] start lesson_id={lesson_id} child={child_id} file={file_path}")

    def set_status(status: str, extra: dict | None = None):
        payload = {"status": status}
        if extra:
            payload.update(extra)
        _sb_update_lesson(lesson_id, payload)

    try:
        set_status("processing", {"started_at": datetime.utcnow().isoformat()})

        # 1) Download file from public storage
        url = _public_storage_url(file_path)
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        content = r.content
        logger.info(f"[JOB] downloaded {len(content)} bytes from {url}")

        # 2) OCR / description
        text = ""
        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".pdf":
            text = _extract_pdf_text(content)
            if not text.strip():
                text = "Leçon: PDF sans texte détectable."
        else:
            # Assume image; let Vision describe/extract in French
            text = _vision_describe_image(url)
            if not text.strip():
                text = "Leçon: description d'image non extraite."

        # Persist OCR/description snippet for debugging
        _sb_update_lesson(lesson_id, {"ocr_text": (text or "")[:10000]})

        # 3) Build the lesson via strict Mimi generator
        image_desc = []
        if ext != ".pdf":
            image_desc = [f"Illustration inspirée de l'image de la leçon: {url}"]

        lesson_json = mimi.build_mimi_lesson(
            topic="",
            ocr_text=text,
            image_descriptions=image_desc,
            age=11,
        )

        # Safety: ensure dict
        if not isinstance(lesson_json, dict):
            logger.warning("[JOB] mimi.build_mimi_lesson returned non-dict; coercing to {}")
            lesson_json = {}

        # 4) Save lesson data and mark completed
        _sb_update_lesson(
            lesson_id,
            {
                "lesson_data": lesson_json,
                "status": "completed",
                "completed_at": datetime.utcnow().isoformat(),
            },
        )
        logger.info(f"[JOB] lesson {lesson_id} completed")

    except Exception as e:
        logger.error(f"[JOB] failed: {e}", exc_info=True)
        set_status("error", {"error_message": str(e), "failed_at": datetime.utcnow().isoformat()})
