#!/usr/bin/env bash
set -euo pipefail

# Create project structure
mkdir -p app

# app/main.py
cat > app/main.py <<'PYEOF'
import os
import json
import uuid
from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client, Client
from app.tasks import process_lesson  # Celery task

app = Flask(__name__)
CORS(app)

# ---- Env & Supabase client (service role) ----
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
supabase: Client | None = None
if SUPABASE_URL and SUPABASE_SERVICE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

@app.get("/health")
def health():
    return jsonify(ok=True, status="healthy")

def _get_user_id_from_auth():
    auth = request.headers.get("Authorization","")
    if not auth.startswith("Bearer "):
        return None
    token = auth.split(" ",1)[1]
    try:
        import jwt
        payload = jwt.decode(token, options={"verify_signature": False})
        return payload.get("sub") or payload.get("user_id")
    except Exception:
        return None

@app.post("/api/lessons")
def api_lessons():
    """Create a lesson job: record row in Supabase, enqueue Celery task."""
    body = request.get_json(silent=True) or {}
    child_id = body.get("child_id")
    file_path = body.get("file_path")  # path in Supabase Storage (e.g., uploads/uid/file.pdf)
    if not child_id or not file_path:
        return jsonify(ok=False, error="child_id and file_path are required"), 400

    # Ensure child_id is a valid UUID
    try:
        child_id = str(uuid.UUID(str(child_id)))
    except (ValueError, TypeError):
        return jsonify(ok=False, error="child_id must be a valid UUID"), 400

    # Optional auth check against children table
    user_id = _get_user_id_from_auth()
    if user_id and supabase:
        child = supabase.table("children").select("id,parent_id").eq("id", child_id).execute()
        if not child.data:
            return jsonify(ok=False, error="Child not found"), 404
        if child.data[0]["parent_id"] != user_id:
            return jsonify(ok=False, error="Not authorized for this child"), 403

    # Create lesson row
    lesson_rec = {
        "child_id": child_id,
        "uploaded_file_path": file_path,
        "status": "processing"
    }
    lesson = (
        supabase.table("lessons").insert(lesson_rec).execute()
        if supabase
        else type("X", (object,), {"data": [{"id": str(uuid.uuid4())}]})()
    )
    lesson_id = lesson.data[0]["id"]

    # Enqueue Celery job
    try:
        process_lesson.delay(str(lesson_id), file_path, str(child_id))
    except Exception as e:
        if supabase:
            supabase.table("lessons").update({"status":"error"}).eq("id", lesson_id).execute()
        return jsonify(ok=False, error=f"Enqueue failed: {e}"), 500

    return jsonify(ok=True, lesson_id=lesson_id, status="processing"), 202

@app.get("/api/lessons/<lesson_id>")
def get_lesson(lesson_id):
    if not supabase:
        return jsonify(status="completed", lesson={"ui_steps":[{"type":"note","text":"Dev mode lesson (no DB)."}]})
    lesson = supabase.table("lessons").select("*").eq("id", lesson_id).execute()
    if not lesson.data:
        return jsonify(error="Not found"), 404
    rec = lesson.data[0]
    return jsonify(status=rec["status"], lesson=rec.get("lesson_data"))
PYEOF

# app/tasks.py
cat > app/tasks.py <<'PYEOF'
import os, requests, re
from datetime import datetime
from celery import Celery
from celery.utils.log import get_task_logger
from supabase import create_client, Client

logger = get_task_logger(__name__)

# ---- Celery ----
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "")
celery_app = Celery("tasks", broker=CELERY_BROKER_URL)

# ---- Supabase ----
SUPABASE_URL = os.getenv("SUPABASE_URL","")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY","")
supabase: Client | None = None
if SUPABASE_URL and SUPABASE_SERVICE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

def _public_storage_url(path:str)->str:
    base = SUPABASE_URL.replace("supabase.co","supabase.co/storage/v1/object/public")
    return f"{base}/{path}"

def _redact(text:str)->str:
    text = re.sub(r"[\w\.-]+@[\w\.-]+", "[REDACTED_EMAIL]", text)
    text = re.sub(r"\+?\d[\d\s-]{7,}\d", "[REDACTED_PHONE]", text)
    return text

@celery_app.task(name="tasks.process_lesson", bind=True)
def process_lesson(self, lesson_id: str, file_path: str, child_id: str):
    """Background job: download file, OCR, generate lesson JSON, save to DB."""
    logger.info(f"[JOB] lesson={lesson_id} child={child_id} file={file_path}")
    def update(fields:dict):
        if supabase:
            supabase.table("lessons").update(fields).eq("id", lesson_id).execute()

    try:
        # 1) Download file
        url = _public_storage_url(file_path)
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        content = r.content
        logger.info(f"[JOB] downloaded {len(content)} bytes")

        # 2) Extract text: PDF -> PyMuPDF; image -> pytesseract
        text = ""
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            import fitz, tempfile
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                f.write(content); tmp = f.name
            doc = fitz.open(tmp)
            for p in doc:
                text += p.get_text()
            doc.close()
        else:
            try:
                from PIL import Image
                import pytesseract, tempfile
                with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
                    f.write(content); ipath = f.name
                img = Image.open(ipath)
                text = pytesseract.image_to_string(img, lang="fra")
            except Exception as e:
                logger.warning(f"[JOB] pytesseract not available: {e}")
        if not text.strip():
            text = "Leçon: images et lieux français. (OCR vide)"
        preview = _redact(text[:200])
        logger.info(f"[JOB] OCR preview: {preview}")
        update({"ocr_text": text[:10000], "ocr_preview": preview})

        # 3) Build Mimi lesson JSON
        try:
            from app import mimi
            lesson_json = mimi.build_mimi_lesson(ocr_text=text)
        except Exception as e:
            logger.error(f"[JOB] mimi lesson build failed: {e}", exc_info=True)
            lesson_json = {"ui_steps":[{"type":"note","text":"Lesson build failed; see logs."}]}

        # 4) Save
        update({"lesson_data": lesson_json, "status":"completed", "completed_at": datetime.utcnow().isoformat()})
        logger.info(f"[JOB] lesson {lesson_id} completed")
    except Exception as e:
        logger.error(f"[JOB] failed: {e}", exc_info=True)
        update({"status":"error"})
PYEOF

# requirements.txt
cat > requirements.txt <<'TXTEOF'
Flask==3.0.3
Flask-Cors==4.0.1
gunicorn==21.2.0
supabase==2.6.0
celery==5.3.6
redis==5.0.8
requests==2.32.3
PyMuPDF==1.24.9
pytesseract==0.3.10
Pillow==10.4.0
PyJWT==2.9.0
TXTEOF

# README.md
cat > README.md <<'MDEOF'
# French AI Tutor - Backend Advanced v1

Ready for Render (Web + Worker) + Supabase + Redis + OpenAI.

## Web Service
Build:  pip install -r requirements.txt  
Start:  gunicorn -w 1 -k gthread --threads 8 --timeout 600 app.main:app

## Worker
Build:  pip install -r requirements.txt  
Start:  celery -A app.tasks.celery_app worker --loglevel=info --concurrency=2

## Env (both services)
SUPABASE_URL  
SUPABASE_SERVICE_KEY  
CELERY_BROKER_URL  
OPENAI_API_KEY
MDEOF

# Zip everything in the current directory
zip -r french_ai_tutor_backend_advanced_v1.zip . >/dev/null

ZIP_PATH="$(realpath french_ai_tutor_backend_advanced_v1.zip)"
echo "ZIP created: ${ZIP_PATH}"
echo ""
echo "Run hints:"
echo "  Web:    pip install -r requirements.txt && gunicorn -w 1 -k gthread --threads 8 --timeout 600 app.main:app"
echo "  Worker: pip install -r requirements.txt && celery -A app.tasks.celery_app worker --loglevel=info --concurrency=2"
