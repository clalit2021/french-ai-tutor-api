import os, json, requests
from datetime import datetime
from flask import Blueprint, request, jsonify
from celery.utils.log import get_task_logger
from supabase import create_client, Client

# Import the singleton Celery app
from app.celery_app import celery_app

logger = get_task_logger(__name__)

bp = Blueprint("tasks", __name__)

# ---- Supabase ----
SUPABASE_URL = os.getenv("SUPABASE_URL","")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY","")
supabase: Client | None = None
if SUPABASE_URL and SUPABASE_SERVICE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# ---- OpenAI ----
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY","")

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

def _public_storage_url(path:str)->str:
    base = SUPABASE_URL.replace("supabase.co","supabase.co/storage/v1/object/public")
    return f"{base}/{path}"

@bp.route("/api/lessons", methods=["POST"])
def api_lessons():
    """Create a lesson job: record row in Supabase, enqueue Celery task."""
    body = request.get_json(silent=True) or {}
    child_id = body.get("child_id")
    file_path = body.get("file_path")
    if not child_id or not file_path:
        return jsonify(ok=False, error="child_id and file_path are required"), 400

    user_id = _get_user_id_from_auth()
    if user_id and supabase:
        child = supabase.table("children").select("id,parent_id").eq("id", child_id).execute()
        if not child.data:
            return jsonify(ok=False, error="Child not found"), 404
        if child.data[0]["parent_id"] != user_id:
            return jsonify(ok=False, error="Not authorized for this child"), 403

    lesson_rec = {
        "child_id": child_id,
        "uploaded_file_path": file_path,
        "status": "processing"
    }
    lesson = supabase.table("lessons").insert(lesson_rec).execute() if supabase else type("X",(object,),{"data":[{"id":"dev-lesson-id"}]})()
    lesson_id = lesson.data[0]["id"]

    # Enqueue Celery job
    try:
        process_lesson.delay(str(lesson_id), file_path, str(child_id))
    except Exception as e:
        if supabase:
            supabase.table("lessons").update({"status":"error"}).eq("id", lesson_id).execute()
        return jsonify(ok=False, error=f"Enqueue failed: {e}"), 500

    return jsonify(ok=True, lesson_id=lesson_id, status="processing"), 202

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
        # ... rest of your function ...
    except Exception as e:
        logger.error(f"Failed to process lesson {lesson_id}: {e}")
        update({"status": "error"})
