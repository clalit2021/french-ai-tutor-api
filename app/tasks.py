# app/tasks.py
import os, requests, logging, re, uuid
from collections import Counter
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
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
supabase = None
if SUPABASE_URL and SUPABASE_SERVICE_KEY:
    try:
        from supabase import create_client
        supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    except Exception as e:
        py_logger.warning("[SUPABASE] client init failed: %r", e)

# ---- OpenAI (env) ----
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")              # used indirectly by mimi
OPENAI_MODEL = os.getenv("OPENAI_MODEL_TEXT", "gpt-4o-mini")  # kept for compatibility

# ---- Helpers ----
def _get_user_id_from_auth() -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth.split(" ", 1)[1]
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

def extract_image_descriptions(text: str, max_items: int = 5) -> list[str]:
    """Return key nouns/scene hints from OCR text using simple frequency analysis."""
    if not text:
        return []
    # Grab alphabetic words (including accents)
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", text.lower())
    stopwords = {
        "le", "la", "les", "un", "une", "de", "des", "et", "en", "du",
        "que", "qui", "pour", "dans", "est", "sur", "au", "aux", "ce",
        "ces", "se", "sa", "son", "ses", "avec", "par", "plus", "pas",
    }
    words = [w for w in words if w not in stopwords and len(w) > 2]
    if not words:
        return []
    counts = Counter(words)
    return [w for w, _ in counts.most_common(max_items)]

def derive_topic_from_text(text: str, max_words: int = 3) -> tuple[str, list[str]]:
    """Derive a concise topic from OCR text using top nouns."""
    image_desc = extract_image_descriptions(text)
    topic = " ".join(image_desc[:max_words])
    return topic, image_desc


def redact_sensitive(text: str) -> str:
    """Redact simple sensitive patterns such as emails and phone numbers."""
    text = re.sub(r"[\w\.-]+@[\w\.-]+", "[REDACTED_EMAIL]", text)
    text = re.sub(r"\+?\d[\d\s-]{7,}\d", "[REDACTED_PHONE]", text)
    return text

def _vision_ocr_fallback(file_bytes: bytes, ext: str) -> str:
    """Try to OCR or describe the image/PDF using pytesseract or OpenAI vision."""
    try:
        import io
        import pytesseract
        from PIL import Image
        import fitz  # type: ignore

        text = ""
        if ext == ".pdf":
            try:
                doc = fitz.open(stream=file_bytes, filetype="pdf")
                for page in doc:
                    pix = page.get_pixmap()
                    img = Image.open(io.BytesIO(pix.tobytes("png")))
                    text += pytesseract.image_to_string(img, lang="fra") + "\n"
                doc.close()
            except Exception as e:
                logger.warning("[JOB] pytesseract PDF fallback failed: %r", e)
        else:
            img = Image.open(io.BytesIO(file_bytes))
            text = pytesseract.image_to_string(img, lang="fra")
        if text.strip():
            return text
    except Exception as e:
        logger.warning("[JOB] pytesseract fallback failed: %r", e)

    if OPENAI_API_KEY:
        try:
            import base64
            from openai import OpenAI

            client = OpenAI(api_key=OPENAI_API_KEY)
            b64 = base64.b64encode(file_bytes).decode("utf-8")
            resp = client.responses.create(
                model=os.getenv("OPENAI_MODEL_VISION", "gpt-4o-mini"),
                input=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": "Extract any visible text or briefly describe the scene in French.",
                            },
                            {"type": "input_image", "image_base64": b64},
                        ],
                    }
                ],
            )
            return getattr(resp, "output_text", "")
        except Exception as e:
            logger.warning("[JOB] OpenAI vision fallback failed: %r", e)

    return ""

# ---- API: create lesson job ----
@bp.route("/api/lessons", methods=["POST"])
def api_lessons():
    body = request.get_json(silent=True) or {}
    child_id = body.get("child_id")
    file_path = body.get("file_path")  # "bucket/path/filename.pdf" recommended

    if not child_id or not file_path:
        return jsonify(ok=False, error="child_id and file_path are required"), 400

    # Ensure child_id is a valid UUID
    try:
        child_id = str(uuid.UUID(str(child_id)))
    except (ValueError, TypeError):
        return jsonify(ok=False, error="child_id must be a valid UUID"), 400

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
        "uploaded_file_path": file_path,  # ensure this column exists
        "status": "processing"
    }
    if supabase:
        lesson = supabase.table("lessons").insert(lesson_rec).execute()
        lesson_id = lesson.data[0]["id"]
    else:
        # Use a UUID string locally to mirror production IDs
        lesson_id = str(uuid.uuid4())

    # Enqueue Celery job
    try:
        process_lesson.delay(str(lesson_id), file_path, str(child_id))
    except Exception as e:
        if supabase:
            supabase.table("lessons").update({"status": "error"}).eq("id", lesson_id).execute()
        return jsonify(ok=False, error=f"Enqueue failed: {e}"), 500

    return jsonify(ok=True, lesson_id=lesson_id, status="processing"), 202

# ---- API: poll lesson ----
@bp.route("/api/lessons/<lesson_id>", methods=["GET"])
def get_lesson(lesson_id):
    if not supabase:
        return jsonify(status="completed", lesson={"ui_steps": [{"type": "note", "text": "Dev mode lesson (no DB)."}]})
    lesson = supabase.table("lessons").select("*").eq("id", lesson_id).maybe_single().execute()
    if not lesson.data:
        return jsonify(error="Not found"), 404
    rec = lesson.data
    return jsonify(status=rec.get("status"), lesson=rec.get("lesson_data"))

# ---- Celery task ----
@celery_app.task(name="tasks.process_lesson", bind=True)
def process_lesson(self, lesson_id: str, file_path: str, child_id: str):
    """Background job: OCR the uploaded file, derive a topic, and build a Mimi lesson."""
    logger.info(f"[JOB] lesson={lesson_id} child={child_id} file={file_path}")

    def update(fields: dict):
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

        # 2) Extract text
        text = ""
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            # Try PyMuPDF first
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

            # If PDF text seems empty (likely image-only), use ABBYY on the whole PDF
            if not (text or "").strip():
                try:
                    from app import ocr_abbyy
                    text = ocr_abbyy.ocr_file_to_text(file_bytes=content, is_pdf=True, language="French")
                    logger.info("[JOB] Used ABBYY OCR fallback for image-only PDF")
                except Exception as e:
                    logger.warning("[JOB] ABBYY OCR failed for PDF: %r", e)
        else:
            # Image file → use ABBYY (no Tesseract dependency)
            try:
                from app import ocr_abbyy
                text = ocr_abbyy.ocr_file_to_text(file_bytes=content, is_pdf=False, language="French")
            except Exception as e:
                logger.warning("[JOB] ABBYY OCR failed for image: %r", e)

        # Abort if OCR yields no meaningful text (try fallback vision OCR first)
        text = text or ""
        if not text.strip():
            logger.info("[JOB] ABBYY returned empty text; attempting vision fallback")
            text = _vision_ocr_fallback(content, ext) or ""
        if not text.strip():
            msg = "OCR extraction returned empty text"
            logger.warning("[JOB] %s", msg)
            update({"status": "error", "ocr_text": msg})
            return

        # Save and log a redacted preview of the OCR text
        preview = redact_sensitive(text[:200])
        logger.info(f"[JOB] OCR preview: {preview}")
        update({"ocr_text": text[:20000], "ocr_preview": preview})

        # 3) Build a full Mimi lesson from the OCR text (uses app/mimi.py)
        try:
            from app import mimi   # defer import to avoid startup issues
            topic, image_desc = derive_topic_from_text(text)
            lesson_json = mimi.build_mimi_lesson(
                topic=topic,                # derived from OCR text
                ocr_text=text,              # ← your OCR output (full file)
                image_descriptions=image_desc,
                age=11                      # TODO: fetch age from DB if you store it per child
            )
        except Exception as e:
            logger.error("[JOB] mimi lesson build failed: %r", e, exc_info=True)
            # Visible fallback so the job completes
            lesson_json = {"ui_steps": [{"type": "note", "text": "Lesson build failed; see logs."}]}

        # 4) Save & finish
        update({
            "lesson_data": lesson_json,
            "status": "completed",
            "completed_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        })
        logger.info(f"[JOB] lesson {lesson_id} completed")
    except Exception as e:
        logger.error(f"[JOB] failed: {e}", exc_info=True)
        update({"status": "error"})
