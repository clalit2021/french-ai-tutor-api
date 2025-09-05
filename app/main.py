# app/main.py
import os
import json
import base64
import time
from datetime import datetime

from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client, Client
from openai import OpenAI

# --------------------------------------------------------------------------
# Flask app (serves / -> static/index.html)
# --------------------------------------------------------------------------
app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

# --------------------------------------------------------------------------
# Environment & Clients
# --------------------------------------------------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Models
OPENAI_MODEL_TEXT = os.getenv("OPENAI_MODEL_TEXT", "gpt-4o-mini")
OPENAI_MODEL_IMAGE = os.getenv("OPENAI_MODEL_IMAGE", "gpt-image-1")

# Supabase client
supabase: Client | None = None
if SUPABASE_URL and SUPABASE_SERVICE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# OpenAI client
openai_client: OpenAI | None = None
if OPENAI_API_KEY:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _safe_trim(text: str, limit: int = 12000) -> str:
    return (text or "")[:limit]

def _public_storage_url(path: str) -> str:
    path = (path or "").lstrip("/")
    return f"{SUPABASE_URL}/storage/v1/object/public/{path}"

# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@app.get("/health")
def health():
    return jsonify(ok=True, status="healthy")

@app.get("/")
def root():
    # Serves static/index.html
    return app.send_static_file("index.html")

# ---------------------- ASYNC: lessons table pipeline -----------------------
@app.post("/api/lessons")
def create_lesson():
    """
    Body: { "child_id": "<uuid>", "file_path": "uploads/file.png" }
    Creates a row in lessons(status='processing'). In production you'd enqueue Celery here.
    """
    if not supabase:
        return jsonify({"error": "Supabase not configured"}), 500

    body = request.get_json(silent=True) or {}
    child_id = (body.get("child_id") or "").strip()
    file_path = (body.get("file_path") or "").strip().lstrip("/")

    if not child_id or not file_path:
        return jsonify({"error": "child_id and file_path required"}), 400

    ins = {
        "child_id": child_id,
        "uploaded_file_path": file_path,
        "status": "processing",
        "created_at": datetime.utcnow().isoformat()
    }
    try:
        res = supabase.table("lessons").insert(ins).execute()
        if not res.data:
            return jsonify({"error": "insert failed"}), 500
        lesson_id = res.data[0]["id"]
        # TODO: enqueue Celery here if you use a worker:
        # process_lesson.delay(str(lesson_id), file_path, str(child_id))
        return jsonify({"lesson_id": lesson_id, "status": "processing"}), 202
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.get("/api/lessons/<lesson_id>")
def get_lesson(lesson_id):
    if not supabase:
        return jsonify({"error": "Supabase not configured"}), 500
    try:
        res = supabase.table("lessons").select("*").eq("id", lesson_id).execute()
        if not res.data:
            return jsonify({"error": "not found"}), 404
        return jsonify(res.data[0])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---------------------- SYNC: strict JSON lesson builder --------------------
SYSTEM_PROMPT = """
You are Mimi, a warm, patient French tutor for an 11-year-old (A1–A2 level).
Turn the input (topic, text excerpt, image descriptions) into a complete 30-minute lesson.

Return STRICT JSON ONLY with EXACTLY these keys:

{
  "title": "string",
  "duration": "string (e.g., '30 min')",
  "objectives": ["string", "..."],
  "plan": [
    { "name": "string", "minutes": "string or number", "teacher_script": "string" }
  ],
  "image_prompts": [
    { "id": "string", "prompt": "string" }
  ],
  "first_tutor_messages": ["string", "..."]
}

Rules:
- No extra keys.
- No code fences.
- No prose outside JSON.
- Make language simple and encouraging; short sentences; playful tone.
- Include speaking aloud, call-and-response, mini-games, and a creative wrap-up.
- Provide 5–8 kid-safe image prompts (no brands, no text in-image, no real faces).
"""

@app.post("/api/v2/lesson")
def build_lesson_v2():
    if not openai_client:
        return jsonify({"error": "OPENAI_API_KEY not configured"}), 500

    body = request.get_json(silent=True) or {}
    topic = body.get("topic", "").strip()
    pdf_text = _safe_trim(body.get("pdf_text", ""))
    image_desc = body.get("image_descriptions", [])
    age = int(body.get("age", 11))

    user_payload = {
        "topic_hint": topic,
        "pdf_text_excerpt": pdf_text,
        "image_descriptions": image_desc if isinstance(image_desc, list) else [],
        "age": age
    }

    try:
        resp = openai_client.chat.completions.create(
            model=OPENAI_MODEL_TEXT,
            temperature=0.4,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(user_payload)}
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
        lesson = json.loads(text)  # guaranteed JSON due to response_format
        return jsonify({"lesson": lesson})
    except Exception as e:
        return jsonify({"error": "LLM error", "details": str(e)}), 500

# ---------------------- IMAGE GENERATION (OpenAI Images) --------------------
@app.post("/api/v2/generate_images")
def generate_images_v2():
    if not openai_client:
        return jsonify({"error": "OPENAI_API_KEY not configured"}), 500

    body = request.get_json(silent=True) or {}
    prompts = body.get("image_prompts", []) or []
    if not isinstance(prompts, list) or not prompts:
        return jsonify({"images": []})

    out, errs = [], []
    for i, p in enumerate(prompts):
        prompt = (p or {}).get("prompt", "")
        _id = (p or {}).get("id", f"img{i+1}")
        if not prompt:
            continue
        try:
            img = openai_client.images.generate(
                model=OPENAI_MODEL_IMAGE,
                prompt=prompt,
                size="512x512",
            )
            b64 = img.data[0].b64_json
            out.append({"id": _id, "b64": b64})
            time.sleep(0.25)  # gentle rate
        except Exception as e:
            errs.append({"id": _id, "error": str(e)})

    resp = {"images": out}
    if errs:
        resp["errors"] = errs
    return jsonify(resp)

# ---------------------- Save generated image to Supabase --------------------
@app.post("/api/v2/save_image")
def save_image_to_supabase():
    if not supabase:
        return jsonify({"error": "Supabase not configured"}), 500

    body = request.get_json(silent=True) or {}
    b64 = body.get("b64", "")
    filename = (body.get("filename") or "image.png").strip()
    if not b64:
        return jsonify({"error": "Missing b64"}), 400

    try:
        img_bytes = base64.b64decode(b64)
        path = f"uploads/{filename}"
        supabase.storage.from_("uploads").upload(
            path, img_bytes, {"content-type": "image/png", "x-upsert": "true"}
        )
        url = _public_storage_url(path)
        return jsonify({"ok": True, "id": body.get("id"), "url": url})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --------------------------------------------------------------------------
if __name__ == "__main__":
    # Local dev server (in prod use gunicorn):
    # gunicorn -w 1 -k gthread --threads 8 --timeout 300 app.main:app
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
