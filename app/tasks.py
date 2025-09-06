# app/tasks.py
import os, io, uuid, time, threading, requests, fitz
from typing import Dict, Any, Tuple
from flask import Blueprint, request, jsonify
from app import mimi
from .ocr_abbyy import ocr_file_to_text

bp = Blueprint("tasks", __name__)

SUPABASE_URL   = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY   = os.getenv("SUPABASE_SERVICE_KEY", "") or os.getenv("SUPABASE_ANON_KEY", "")
# NOTE: paths look like "uploads/filename.pdf" where "uploads" is the bucket

# ---- In-memory jobs (simple) ----
_JOBS: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()

def _set_job(job_id: str, **fields):
    with _LOCK:
        _JOBS.setdefault(job_id, {})
        _JOBS[job_id].update(fields)

def _get_job(job_id: str) -> Dict[str, Any] | None:
    with _LOCK:
        return _JOBS.get(job_id)

# ---- Supabase download helpers ----
def _sb_public_url(file_path: str) -> str:
    # If bucket is public, this works directly
    return f"{SUPABASE_URL}/storage/v1/object/public/{file_path.lstrip('/')}"

def _download_supabase(file_path: str) -> bytes:
    """
    Tries public URL first; if forbidden, retries with bearer token.
    `file_path` should include bucket, e.g. 'uploads/Screenshot_...png'
    """
    if not SUPABASE_URL:
        raise RuntimeError("SUPABASE_URL not set")

    url = _sb_public_url(file_path)
    try:
        r = requests.get(url, timeout=(15, 120))
        if r.ok:
            return r.content
    except Exception:
        pass

    # Try with Authorization (works even if bucket is private and RLS allows service-role)
    headers = {}
    if SUPABASE_KEY:
        headers["Authorization"] = f"Bearer {SUPABASE_KEY}"
    r2 = requests.get(url, headers=headers, timeout=(15, 120))
    if not r2.ok:
        raise RuntimeError(f"Supabase download failed: HTTP {r2.status_code} - {r2.text[:200]}")
    return r2.content

# ---- Text extraction ----
def _pdf_extract_text_or_empty(pdf_bytes: bytes) -> Tuple[str, bool]:
    """
    Return (text, is_image_heavy)
    Heuristic: if most pages produce < 40 visible chars, we consider it image-heavy.
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

# ---- Worker ----
def _worker_build_lesson(job_id: str, child_id: str, file_path: str):
    try:
        _set_job(job_id, status="processing")
        print(f"[ASYNC] start job={job_id} child={child_id} file_path={file_path}")

        # 1) Download from Supabase
        blob = _download_supabase(file_path)
        mime = _guess_mime_from_name(file_path)

        # 2) Extract text (PDF first)
        ocr_excerpt = ""
        if mime == "application/pdf":
            text, image_heavy = _pdf_extract_text_or_empty(blob)
            if text and not image_heavy:
                ocr_excerpt = text[:1200]
            else:
                # Fallback to ABBYY on the whole PDF (if configured)
                abbyy_text = ocr_file_to_text(blob, is_pdf=True, language="French")
                ocr_excerpt = (abbyy_text or text or "")[:1200]
        elif mime in ("image/png", "image/jpeg"):
            # Image → ABBYY (if configured)
            abbyy_text = ocr_file_to_text(blob, is_pdf=False, language="French")
            ocr_excerpt = (abbyy_text or "")[:1200]
        else:
            # Unknown → punt
            ocr_excerpt = ""

        # 3) Build the lesson with your orchestrator
        lesson = mimi.build_mimi_lesson(
            topic="Leçon depuis fichier",
            ocr_text=ocr_excerpt,
            image_descriptions=[],  # (optional) you can add your own vision cues later
            age=11,
        )

        _set_job(job_id, status="ready", lesson=lesson)
        print(f"[ASYNC] done job={job_id} status=ready")
    except Exception as e:
        print(f"[ASYNC][ERROR] job={job_id} {repr(e)}")
        _set_job(job_id, status="failed", error=str(e))

# ---- Routes ----
@bp.route("/api/lessons", methods=["POST"])
def create_lesson_job():
    body = request.get_json(silent=True, force=True) or {}
    child_id = (body.get("child_id") or "").strip()
    file_path = (body.get("file_path") or "").strip()
    if not file_path:
        return jsonify(error="file_path is required (e.g., 'uploads/book.pdf')"), 400

    lesson_id = str(uuid.uuid4())
    _set_job(lesson_id, status="queued", child_id=child_id, file_path=file_path)

    th = threading.Thread(target=_worker_build_lesson, args=(lesson_id, child_id, file_path), daemon=True)
    th.start()

    print(f"[ASYNC] queued lesson_id={lesson_id} file_path={file_path}")
    return jsonify(lesson_id=lesson_id, status="queued")

@bp.route("/api/lessons/<lesson_id>", methods=["GET"])
def get_lesson_job(lesson_id: str):
    job = _get_job(lesson_id)
    if not job:
        return jsonify(error="lesson_id not found"), 404
    res = {"lesson_id": lesson_id, "status": job.get("status")}
    if job.get("status") == "ready":
        res["lesson"] = job.get("lesson", {})
    if job.get("status") == "failed":
        res["error"] = job.get("error")
    return jsonify(res)
