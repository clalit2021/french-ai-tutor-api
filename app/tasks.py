# app/tasks.py
"""
Async lesson pipeline (Celery + Redis):
- POST /api/lessons            -> { lesson_id, status:"queued" }
- GET  /api/lessons/<lesson_id> -> { status, lesson? | error? }

Requires:
  CELERY_BROKER_URL=redis://host:6379/0
  CELERY_RESULT_BACKEND=redis://host:6379/1
Start worker:
  celery -A app.celery_app.celery_app worker -l info --concurrency=2
"""

import os
import uuid
from typing import Tuple

import fitz  # PyMuPDF
import requests
from flask import Blueprint, request, jsonify
from celery.result import AsyncResult

from app import mimi
from app.celery_app import celery_app
from .ocr_abbyy import ocr_file_to_text

bp = Blueprint("tasks", __name__)

# ---- Supabase config ----
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "") or os.getenv("SUPABASE_ANON_KEY", "")

# -----------------------------
# Helpers: Supabase + extraction
# -----------------------------
def _sb_public_url(file_path: str) -> str:
    """file_path should include the bucket, e.g. 'uploads/book.pdf'."""
    return f"{SUPABASE_URL}/storage/v1/object/public/{file_path.lstrip('/')}"

def _download_supabase(file_path: str) -> bytes:
    """Try public URL first; if forbidden, retry with Authorization (service role/anon)."""
    if not SUPABASE_URL:
        raise RuntimeError("SUPABASE_URL not set")

    url = _sb_public_url(file_path)

    # Try public
    try:
        r = requests.get(url, timeout=(15, 120))
        if r.ok:
            return r.content
    except Exception:
        pass

    # Try with Authorization (works for private buckets with proper policy/role)
    headers = {}
    if SUPABASE_KEY:
        headers["Authorization"] = f"Bearer {SUPABASE_KEY}"
    r2 = requests.get(url, headers=headers, timeout=(15, 120))
    if not r2.ok:
        raise RuntimeError(f"Supabase download failed: HTTP {r2.status_code} - {r2.text[:200]}")
    return r2.content

def _pdf_extract_text_or_empty(pdf_bytes: bytes) -> Tuple[str, bool]:
    """
    Return (text, is_image_heavy).
    Heuristic: if most pages yield < 40 visible chars, consider image-heavy.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total_text = []
    image_pages = 0
    for page in doc:
        t = (page.get_text("text") or "").strip()
        if len(t) < 40:
            image_pages += 1
        if t:
            total_text.append(t)
    is_image_heavy = (image_pages >= max(1, int(0.6 * doc.page_count)))
    return ("\n\n".join(total_text)).strip(), is_image_heavy

def _guess_mime_from_name(name: str) -> str:
    n = name.lower()
    if n.endswith(".pdf"): return "application/pdf"
    if n.endswith(".png"): return "image/png"
    if n.endswith(".jpg") or n.endswith(".jpeg"): return "image/jpeg"
    return "application/octet-stream"

# -----------------------------
# Celery task
# -----------------------------
@celery_app.task(name="build_lesson_from_file")
def build_lesson_from_file(file_path: str, child_id: str = "") -> dict:
    """
    Celery task body. Downloads the file, extracts text (PyMuPDF → ABBYY fallback),
    and returns a lesson payload: { "lesson": {...} }.
    """
    print(f"[CELERY] start file_path={file_path} child_id={child_id}")

    blob = _download_supabase(file_path)
    mime = _guess_mime_from_name(file_path)

    # Extract text
    ocr_excerpt = ""
    if mime == "application/pdf":
        text, image_heavy = _pdf_extract_text_or_empty(blob)
        if text and not image_heavy:
            ocr_excerpt = text[:1200]
        else:
            # Fallback to ABBYY (if configured); soft-fails to "" if not configured
            abbyy_text = ocr_file_to_text(blob, is_pdf=True, language="French")
            ocr_excerpt = (abbyy_text or text or "")[:1200]
    elif mime in ("image/png", "image/jpeg"):
        abbyy_text = ocr_file_to_text(blob, is_pdf=False, language="French")
        ocr_excerpt = (abbyy_text or "")[:1200]
    else:
        ocr_excerpt = ""

    # Build lesson via your orchestrator
    lesson = mimi.build_mimi_lesson(
        topic="Leçon depuis fichier",
        ocr_text=ocr_excerpt,
        image_descriptions=[],  # add image cues later if you like
        age=11,
    )

    print(f"[CELERY] done file_path={file_path} len(ocr)={len(ocr_excerpt)}")
    return {"lesson": lesson}

# -----------------------------
# HTTP API
# -----------------------------
@bp.route("/api/lessons", methods=["POST"])
def create_lesson_job():
    """
    Body:
      { "child_id": "uuid-or-any", "file_path": "uploads/xxx.pdf|.png" }
    """
    body = request.get_json(silent=True, force=True) or {}
    child_id = (body.get("child_id") or "").strip()
    file_path = (body.get("file_path") or "").strip()

    if not file_path:
        return jsonify(error="file_path is required (e.g., 'uploads/book.pdf')"), 400

    # Enqueue Celery task
    async_res = build_lesson_from_file.delay(file_path=file_path, child_id=child_id)
    lesson_id = async_res.id
    print(f"[ASYNC] queued lesson_id={lesson_id} file_path={file_path}")
    return jsonify(lesson_id=lesson_id, status="queued")

@bp.route("/api/lessons/<lesson_id>", methods=["GET"])
def get_lesson_job(lesson_id: str):
    """
    Poll Celery for status/result.
    """
    res = AsyncResult(lesson_id, app=celery_app)

    # Map Celery states to our simple statuses
    state = res.state  # PENDING, RECEIVED, STARTED, RETRY, SUCCESS, FAILURE
    if state == "PENDING":
        return jsonify(lesson_id=lesson_id, status="queued")
    if state in ("RECEIVED", "STARTED", "RETRY"):
        return jsonify(lesson_id=lesson_id, status="processing")
    if state == "SUCCESS":
        payload = res.result or {}
        # payload expected: { "lesson": {...} }
        return jsonify(lesson_id=lesson_id, status="ready", **payload)
    if state == "FAILURE":
        return jsonify(lesson_id=lesson_id, status="failed", error=str(res.result)), 500

    # Fallback for any uncommon state
    return jsonify(lesson_id=lesson_id, status=state.lower())
