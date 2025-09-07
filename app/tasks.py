# app/tasks.py
import os, json, requests, logging
from datetime import datetime
from typing import Optional
from urllib.parse import quote

from flask import Blueprint, request, jsonify
from celery.utils.log import get_task_logger

# ---- Celery (use the shared app) ----
try:
    from app.celery_app import celery_app  # single source of truth
except Exception:
    # Fallback (won't have your nice config)
    from celery import Celery
    celery_app = Celery("tasks", broker=os.getenv("CELERY_BROKER_URL", ""))

logger = get_task_logger(__name__)
py_logger = logging.getLogger(__name__)

# ---- Flask Blueprint ----
bp = Blueprint("tasks", __name__)

# ---- Supabase ----
SUPABASE_URL = os.getenv("SUPABASE_URL","")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY","")
supabase = None
if SUPABASE_URL and SUPABASE_SERVICE_KEY:
    try:
        from supabase import create_client
        supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    except Exception as e:
        py_logger.warning("[SUPABASE] client init failed: %r", e)

# ---- OpenAI ----
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY","")
OPENAI_MODEL = os.getenv("OPENAI_MODEL_TEXT","gpt-4o-mini")

# ---- Helpers ----
def _get_user_id_from_auth() -> Optional[str]:
    auth = request.headers.get("Authorization","")
    if not auth.startswith("Bearer "):
        return None
    token = auth.split(" ",1)[1]
    try:
        import jwt  # requires PyJWT
        payload = jwt.decode(token, options={"verify_signature": False})
        return payload.get("sub") or payload.get("user_id")
    except Exception:
        return None

def _public_storage_url(path: str) -> str:
    """
    Build a public URL for Supabase Storage.
    Accepts either "bucket/path/to/file" or just "path" if your path already starts with the bucket.
    Preserves existing % encodings and encodes spaces safely.
    """
    base = SUPABASE_URL.rstrip("/").replace(
        "supabase.co", "supabase.co/storage/v1/object/public"
    )
    # Avoid double-encoding existing %xx
    safe_path = quote(path.lstrip("/"), safe="/%")
    return f"{base}/{safe_path}"

def _json_loose(text: str):
    """Extract outermost JSON object if model returns stray characters."""
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{"); end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start:end+1])
        raise

# ---- API: create lesson job ----
@bp.route("/api/lessons", methods=["POST"])
def api_lessons():
    body = request.get_json(silent=True) or {}
    child_id = body.get("child_id")
    file_path = body.get("file_path")  # "bucket/path/filename.pdf" recommended

    if not child_id or not file_path:
        return jsonify(ok=False, error="child_id and file_path are required"), 400

    # Optional auth check
    user_id = _get_user_id_from_auth()
    if user_id and supabase:
        child = supabase.table("children").select("id,parent_id").eq("id", child_id).execute()
        if not child.data:
            return jsonify(ok=False, error="Child not found"), 404
        if child.data[0].get("parent_id") != user_id:
            return jsonify(ok=False, error="Not authorized for this child"), 403

    # Create lesson row
    lesson_rec = {
        "child_id": child_id,
        "uploaded_file_path": file_path,  # ensure this column exists (see migration below)
        "status": "processing"
    }
    if supabase:
        lesson = supabase.table("lessons").insert(lesson_rec).execute()
        lesson_id = lesson.data[0]["id"]
    else:
        lesson_id = "dev-lesson-id"

    # Enqueue Celery job
    try:
        process_lesson.delay(str(lesson_id), file_path, str(child_id))
    except Exception as e:
        if supabase:
            supabase.table("lessons").update({"status":"error"}).eq("id", lesson_id).execute()
        return jsonify(ok=False, error=f"Enqueue failed: {e}"), 500

    return jsonify(ok=True, lesson_id=lesson_id, status="processing"), 202

# ---- API: poll lesson ----
@bp.route("/api/lessons/<lesson_id>", methods=["GET"])
def get_lesson(lesson_id):
    if not supabase:
        return jsonify(status="completed", lesson={"ui_steps":[{"type":"note","text":"Dev mode lesson (no DB)."}]})
    lesson = supabase.table("lessons").select("*").eq("id", lesson_id).maybe_single().execute()
    if not lesson.data:
        return jsonify(error="Not found"), 404
    rec = lesson.data
    return jsonify(status=rec.get("status"), lesson=rec.get("lesson_data"))

# ---- Celery task ----
@celery_app.task(name="tasks.process_lesson", bind=True)
def process_lesson(self, lesson_id: str, file_path: str, child_id: str):
    logger.info(f"[JOB] lesson={lesson_id} child={child_id} file={file_path}")

    def update(fields:dict):
        if supabase:
            try:
                supabase.table("lessons").update(fields).eq("id", lesson_id).execute()
            except Exception as e:
                logger.warning("[JOB] update failed: %r", e)

    try:
        # 1) Download file (public URL)
        url = _public_storage_url(file_path)
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        content = r.content
        logger.info(f"[JOB] downloaded {len(content)} bytes from {url}")

        # 2) Extract text (simple path; your ingest pipeline can replace this)
        text = ""
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            try:
                import fitz, tempfile
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                    f.write(content); tmp = f.name
                doc = fitz.open(tmp)
                for p in doc:
                    try:
                        t = p.get_text() or ""
                    except Exception:
                        t = ""
                    text += t + "\n"
                doc.close()
            except Exception as e:
                logger.warning("[JOB] PDF text extraction failed: %r", e)
        else:
            try:
                from PIL import Image
                import pytesseract, tempfile
                with tempfile.NamedTemporaryFile(suffix=ext or ".png", delete=False) as f:
                    f.write(content); ipath = f.name
                img = Image.open(ipath)
                text = pytesseract.image_to_string(img, lang="fra")
            except Exception as e:
                logger.warning("[JOB] image OCR not available: %r", e)

        if not (text or "").strip():
            text = "Leçon: images et lieux français. (OCR vide)"
        update({"ocr_text": text[:10000]})

        # 3) Call OpenAI to build a tiny interactive lesson JSON
        if OPENAI_API_KEY:
            api = "https://api.openai.com/v1/chat/completions"
            sys = "You are a playful French tutor for an 11-year-old. Reply ONLY valid JSON."
            user = (
                "Create a 2-step interactive lesson from this text. Use simple French.\n"
                "Return {\n"
                '  "ui_steps":[\n'
                '    {"type":"image_card","text":"C\'est la tour Eiffel !","image_url":"https://upload.wikimedia.org/wikipedia/commons/a/a8/Tour_Eiffel_Wikimedia_Commons.jpg"},\n'
                '    {"type":"question","question":"Où parle-t-on français ?","options":["Montréal","Tokyo"],"correct_option":0}\n'
                "  ]\n"
                "}. Text source:\\n" + (text[:800] or "")
            )
            payload = {"model": OPENAI_MODEL, "messages":[{"role":"system","content":sys},{"role":"user","content":user}], "temperature":0.4}
            resp = requests.post(
                api,
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                json=payload,
                timeout=60
            )
            resp.raise_for_status()
            content_text = resp.json()["choices"][0]["message"]["content"]
            try:
                lesson_json = _json_loose(content_text)
            except Exception:
                lesson_json = {"ui_steps":[{"type":"note","text":"JSON parse failed; fallback card."}]}
        else:
            lesson_json = {"ui_steps":[{"type":"note","text":"OPENAI_API_KEY missing. Demo step only."}]}

        # 4) Save & finish
        update({
            "lesson_data": lesson_json,
            "status":"completed",
            "completed_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        })
        logger.info(f"[JOB] lesson {lesson_id} completed")
    except Exception as e:
        logger.error(f"[JOB] failed: {e}", exc_info=True)
        update({"status":"error"})
