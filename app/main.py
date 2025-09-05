import os
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client, Client
from app.tasks import process_lesson  # Celery task
from app.tutor_sync import bp as tutor_sync_bp
from openai import OpenAI

app = Flask(__name__)
CORS(app)

fetch("https://french-ai-tutor-api.onrender.com/api/v2/generate_images", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    image_prompts: [
      { id: "t1", prompt: "Kid-friendly illustration of the Eiffel Tower; bright, simple; no text; 1024x1024" }
    ]
  })
}).then(r => r.text()).then(t => console.log(t)).catch(e => console.error("fetch error", e));


# ---- Register blueprints ----
app.register_blueprint(tutor_sync_bp, url_prefix="/api/tutor_sync")

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
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth.split(" ", 1)[1]
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
    lesson = supabase.table("lessons").insert(lesson_rec).execute() if supabase else type(
        "X", (object,), {"data": [{"id": "dev-lesson-id"}]}
    )()
    lesson_id = lesson.data[0]["id"]

    # Enqueue Celery job
    try:
        process_lesson.delay(str(lesson_id), file_path, str(child_id))
    except Exception as e:
        if supabase:
            supabase.table("lessons").update({"status": "error"}).eq("id", lesson_id).execute()
        return jsonify(ok=False, error=f"Enqueue failed: {e}"), 500

    return jsonify(ok=True, lesson_id=lesson_id, status="processing"), 202


@app.get("/api/lessons/<lesson_id>")
def get_lesson(lesson_id):
    if not supabase:
        return jsonify(status="completed", lesson={"ui_steps": [{"type": "note", "text": "Dev mode lesson (no DB)."}]})
    lesson = supabase.table("lessons").select("*").eq("id", lesson_id).execute()
    if not lesson.data:
        return jsonify(error="Not found"), 404
    rec = lesson.data[0]
    return jsonify(status=rec["status"], lesson=rec.get("lesson_data"))

