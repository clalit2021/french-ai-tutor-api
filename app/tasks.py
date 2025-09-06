import os, uuid
from typing import Tuple
from urllib.parse import quote

import fitz
import requests
from flask import Blueprint, request, jsonify
from celery.result import AsyncResult

from app import mimi
from app.celery_app import celery_app
from .ocr_abbyy import ocr_file_to_text

bp = Blueprint("tasks", __name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "") or os.getenv("SUPABASE_ANON_KEY", "")

def _sb_public_url(file_path: str) -> str:
    # encode ONLY the object part; keep bucket literal
    bucket, _, rest = file_path.partition("/")
    encoded = f"{bucket}/{quote(rest)}" if rest else quote(bucket)
    return f"{SUPABASE_URL}/storage/v1/object/public/{encoded}"

def _download_supabase(file_path: str) -> bytes:
    if not SUPABASE_URL: raise RuntimeError("SUPABASE_URL not set")
    url = _sb_public_url(file_path)
    try:
        r = requests.get(url, timeout=(15, 120))
        if r.ok: return r.content
    except Exception: pass
    headers = {"Authorization": f"Bearer {SUPABASE_KEY}"} if SUPABASE_KEY else {}
    r2 = requests.get(url, headers=headers, timeout=(15, 120))
    if not r2.ok: raise RuntimeError(f"Supabase download failed: HTTP {r2.status_code} - {r2.text[:200]}")
    return r2.content

def _pdf_extract_text_or_empty(pdf_bytes: bytes) -> Tuple[str, bool]:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total, img_pages = [], 0
    for p in doc:
        t = (p.get_text("text") or "").strip()
        if len(t) < 40: img_pages += 1
        if t: total.append(t)
    is_image_heavy = (img_pages >= max(1, int(0.6 * doc.page_count)))
    return ("\n\n".join(total)).strip(), is_image_heavy

def _guess_mime(name: str) -> str:
    n = name.lower()
    if n.endswith(".pdf"): return "application/pdf"
    if n.endswith(".png"): return "image/png"
    if n.endswith(".jpg") or n.endswith(".jpeg"): return "image/jpeg"
    return "application/octet-stream"

@celery_app.task(name="build_lesson_from_file")
def build_lesson_from_file(file_path: str, child_id: str = "") -> dict:
    print(f"[CELERY] start file_path={{file_path}} child_id={{child_id}}")
    blob = _download_supabase(file_path)
    mime = _guess_mime(file_path)
    ocr_excerpt = ""

    if mime == "application/pdf":
        text, image_heavy = _pdf_extract_text_or_empty(blob)
        if text and not image_heavy:
            ocr_excerpt = text[:1200]
        else:
            abbyy = ocr_file_to_text(blob, is_pdf=True, language="French")
            ocr_excerpt = (abbyy or text or "")[:1200]
    elif mime in ("image/png", "image/jpeg"):
        abbyy = ocr_file_to_text(blob, is_pdf=False, language="French")
        ocr_excerpt = (abbyy or "")[:1200]

    lesson = mimi.build_mimi_lesson(
        topic="Leçon depuis fichier",
        ocr_text=ocr_excerpt,
        image_descriptions=[],
        age=11,
    )
    print(f"[CELERY] done file_path={{file_path}} len(ocr)={{len(ocr_excerpt)}}")
    return {"lesson": lesson}

@bp.route("/api/lessons", methods=["POST"])
def create_lesson_job():
    body = request.get_json(silent=True, force=True) or {}
    child_id = (body.get("child_id") or "").strip()
    file_path = (body.get("file_path") or "").strip()
    if not file_path:
        return jsonify(error="file_path is required (e.g., 'uploads/book.pdf')"), 400
    async_res = build_lesson_from_file.delay(file_path=file_path, child_id=child_id)
    return jsonify(lesson_id=async_res.id, status="queued")

@bp.route("/api/lessons/<lesson_id>", methods=["GET"])
def get_lesson_job(lesson_id: str):
    res = AsyncResult(lesson_id, app=celery_app)
    st = res.state
    if st == "PENDING":  return jsonify(lesson_id=lesson_id, status="queued")
    if st in ("RECEIVED","STARTED","RETRY"): return jsonify(lesson_id=lesson_id, status="processing")
    if st == "SUCCESS": return jsonify(lesson_id=lesson_id, status="ready", **(res.result or {}))
    if st == "FAILURE": return jsonify(lesson_id=lesson_id, status="failed", error=str(res.result)), 500
    return jsonify(lesson_id=lesson_id, status=st.lower()).
