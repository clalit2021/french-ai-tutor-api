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

# --- Image generation route (direct) ---
OPENAI_MODEL_IMAGE = os.getenv("OPENAI_MODEL_IMAGE", "gpt-image-1")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
openai_client = OpenAI(api_key=OPENAI_API_KEY)

@app.route("/api/v2/generate_images", methods=["POST"])
def generate_images_v2():
    """
    Body: { "image_prompts": [{"id":"img1","prompt":"..."}] }
    Returns: { "images": [{"id":"img1","b64":"..."}] }
    """
    if not OPENAI_API_KEY:
        return jsonify({"error": "OPENAI_API_KEY not configured"}), 500

    body = request.get_json(force=True, silent=True) or {}
    prompts = body.get("image_prompts", []) or []
    out = []
    try:
        for p in prompts:
            prompt = (p or {}).get("prompt", "")
            if not prompt:
                continue
            img = openai_client.images.generate(
                model=OPENAI_MODEL_IMAGE,
                prompt=prompt,
                size="1024x1024"
            )
            b64 = img.data[0].b64_json
            out.append({"id": p.get("id", ""), "b64": b64})
        return jsonify({"images": out})
    except Exception as e:
        # Return the error text so the frontend can display it
        return jsonify({"error": str(e)}), 500

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

