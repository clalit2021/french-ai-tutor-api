import os, json, requests
from datetime import datetime
from flask import Blueprint, request, jsonify
from celery import Celery
from celery.utils.log import get_task_logger
from supabase import create_client, Client

logger = get_task_logger(__name__)

# ---- Flask Blueprint ----
bp = Blueprint("tasks", __name__)

# ---- Celery ----
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "")
celery_app = Celery("tasks", broker=CELERY_BROKER_URL)

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
    file_path = body.get("file_path")  # path in Supabase Storage (e.g., uploads/uid/file.pdf)
    if not child_id or not file_path:
        return jsonify(ok=False, error="child_id and file_path are required"), 400

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

@bp.route("/api/lessons/<lesson_id>", methods=["GET"])
def get_lesson(lesson_id):
    """Poll for lesson status and data."""
    if not supabase:
        return jsonify(status="completed", lesson={"ui_steps":[{"type":"note","text":"Dev mode lesson (no DB)."}]})
    lesson = supabase.table("lessons").select("*").eq("id", lesson_id).execute()
    if not lesson.data:
        return jsonify(error="Not found"), 404
    rec = lesson.data[0]
    return jsonify(status=rec["status"], lesson=rec.get("lesson_data"))

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
        update({"ocr_text": text[:10000]})

        # 3) OpenAI lesson JSON
        if OPENAI_API_KEY:
            api = "https://api.openai.com/v1/chat/completions"
            sys = "You are a playful French tutor for an 11-year-old. Reply ONLY valid JSON."
            user = f"""Create a 2-step interactive lesson from this text. Use simple French.
Return {{
  "ui_steps":[
    {{"type":"image_card","text":"C'est la tour Eiffel !","image_url":"https://upload.wikimedia.org/wikipedia/commons/a/a8/Tour_Eiffel_Wikimedia_Commons.jpg"}},
    {{"type":"question","question":"Où parle-t-on français ?","options":["Montréal","Tokyo"],"correct_option":0}}
  ]
}}. Text source:\\n{text[:800]}
."""
            payload = {"model":"gpt-4o-mini","messages":[{"role":"system","content":sys},{"role":"user","content":user}],"temperature":0.4}
            resp = requests.post(api, headers={"Authorization":f"Bearer {OPENAI_API_KEY}","Content-Type":"application/json"}, json=payload, timeout=60)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            try:
                lesson_json = json.loads(content)
            except Exception:
                lesson_json = {"ui_steps":[{"type":"note","text":"JSON parse failed; fallback card."}]}
        else:
            lesson_json = {"ui_steps":[{"type":"note","text":"OPENAI_API_KEY missing. Demo step only."}]}

        # 4) Save
        update({"lesson_data": lesson_json, "status":"completed", "completed_at": datetime.utcnow().isoformat()})
        logger.info(f"[JOB] lesson {lesson_id} completed")
    except Exception as e:
        logger.error(f"[JOB] failed: {e}", exc_info=True)
        update({"status":"error"})
