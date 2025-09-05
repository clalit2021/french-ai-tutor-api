# app/main.py
import os
import json
import base64
import time
from datetime import datetime

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

from supabase import create_client, Client
from openai import OpenAI

# Optional: import Celery task to enqueue background jobs
# Make sure your worker service runs Celery with the same project
try:
    from app.tasks import process_lesson  # celery task
except Exception:
    process_lesson = None  # web can still run without the worker import

# ------------------------------------------------------------------------------
# App and CORS
# ------------------------------------------------------------------------------
app = Flask(__name__, static_folder=".", static_url_path="")
CORS(app)

# ------------------------------------------------------------------------------
# Environment & Clients
# ------------------------------------------------------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL_TEXT = os.getenv("OPENAI_MODEL_TEXT", "gpt-4o-mini")
OPENAI_MODEL_IMAGE = os.getenv("OPENAI_MODEL_IMAGE", "gpt-image-1")

supabase: Client = None
if SUPABASE_URL and SUPABASE_SERVICE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

openai_client: OpenAI = None
if OPENAI_API_KEY:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)


# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------
def _public_storage_url(path: str) -> str:
    """
    Convert 'uploads/dir/file.png' to public URL:
    https://<proj>.supabase.co/storage/v1/object/public/uploads/dir/file.png
    """
    path = path.lstrip("/")
    return f"{SUPABASE_URL}/storage/v1/object/public/{path}"


def _safe_trim(text: str, limit: int = 12000) -> str:
    return (text or "")[:limit]


# ------------------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------------------
@app.get("/health")
def health():
    return jsonify(ok=True, status="healthy")


@app.get("/")
def root():
    # Serve the SPA index.html placed at repo root
    return send_from_directory(".", "index.html")


# ----------------------------- ASYNC PIPELINE ---------------------------------
@app.post("/api/lessons")
def create_lesson():
    """
    Body: { "child_id": "<uuid>", "file_path": "uploads/file.png" }
    1) Insert lessons row (status=processing)
    2) Enqueue Celery job to process OCR + lesson generation
    3) Return 202 with lesson_id
    """
    if not supabase:
        return jsonify({"error": "Supabase not configured"}), 500
    data = request.get_json(force=True, silent=True) or {}
    child_id = (data.get("child_id") or "").strip()
    file_path = (data.get("file_path") or "").strip().lstrip("/")

    if not child_id or not file_path:
        return jsonify({"error": "child_id and file_path are required"}), 400

    # Insert lesson row
    ins = {
        "child_id": child_id,
        "uploaded_file_path": file_path,
        "status": "processing",
        "created_at": datetime.utcnow().isoformat()
    }
    res = supabase.table("lessons").insert(ins).execute()
    if not res.data or not isinstance(res.data, list):
        return jsonify({"error": "failed to insert lesson"}), 500
    lesson_id = res.data[0]["id"]

    # Enqueue Celery background job
    if process_lesson is None:
        # Web can still respond, but warn no worker import
        return jsonify({"lesson_id": lesson_id, "ok": True, "status": "processing", "warn": "worker not imported"}), 202
    try:
        process_lesson.delay(str(lesson_id), file_path, str(child_id))
    except Exception as e:
        # If enqueue fails, mark error
        supabase.table("lessons").update({"status": "error"}).eq("id", lesson_id).execute()
        return jsonify({"error": f"failed to enqueue: {e}"}), 500

    return jsonify({"lesson_id": lesson_id, "ok": True, "status": "processing"}), 202


@app.get("/api/lessons/<lesson_id>")
def get_lesson(lesson_id):
    """
    Return lesson status and data
    """
    if not supabase:
        return jsonify({"error": "Supabase not configured"}), 500
    res = supabase.table("lessons").select("*").eq("id", lesson_id).execute()
    rows = res.data or []
    if not rows:
        return jsonify({"error": "not found"}), 404
    row = rows[0]
    # normalize for frontend
    out = {
        "id": row.get("id"),
        "status": row.get("status"),
        "lesson": {
            "ocr_text": row.get("ocr_text"),
            "lesson_data": row.get("lesson_data"),
        }
    }
    return jsonify(out)


# ----------------------------- SYNC LESSON V2 ---------------------------------
SYSTEM_PROMPT = (
    "You are “Mimi”, a warm, patient French tutor for an 11-year-old child (A1–A2 level). "
    "Input may contain: (a) extracted text from a PDF, (b) one or more images (described), "
    "or (c) a topic string. Your job is to turn that input into a complete, kid-friendly, "
    "30-minute interactive lesson.\n\n"
    "Constraints:\n"
    "- Keep language SIMPLE and encouraging. Use short sentences. Avoid jargon.\n"
    "- Build a clear 30-minute sequence of activities (5–7 blocks).\n"
    "- Always include speaking aloud, call-and-response, mini-games, and a creative wrap-up.\n"
    "- Prepare exercises with correct answers and brief explanations.\n"
    "- Propose 6–10 kid-safe image prompts (NO brand names, NO text in-image, no faces of real people).\n"
    "- Output MUST be valid JSON matching the schema below—no commentary.\n\n"
    "JSON schema to produce:\n"
    "{\n"
    '  "title": "string",\n'
    '  "age": 11,\n'
    '  "level": "A1-A2",\n'
    '  "topic_detected": "string",\n'
    '  "objectives": ["string", "..."],\n'
    '  "duration_minutes": 30,\n'
    '  "plan": [ { "minutes": 5, "name": "Warm-up – Guess the photo", "teacher_script": "…", "student_actions": ["…"], "target_phrases_fr": ["…"] } ],\n'
    '  "slides": [ { "title": "France en photos", "bullets": ["..."], "speak_aloud_fr": "..." } ],\n'
    '  "image_prompts": [ { "id": "img1", "prompt": "Kid-friendly illustration of [X]; bright, simple; no text; 1024x1024; for teaching." } ],\n'
    '  "exercises": [\n'
    '    { "type": "mcq", "question_fr": "C’est ... ?", "options": ["la baguette","le sushi","le taco"], "answer_index": 0, "explain_en": "In France we say “la baguette”."},\n'
    '    { "type": "fill", "question_fr": "C’___ la tour Eiffel.", "answer_text": "est", "explain_en": "C’est = it is."}\n'
    '  ],\n'
    '  "first_tutor_messages": ["Bonjour ! Ready to explore France together? First, look at this picture..."]\n'
    "}\n"
)

@app.post("/api/v2/lesson")
def build_lesson_v2():
    """
    Body: {
      "topic": "optional",
      "pdf_text": "optional",
      "image_descriptions": ["optional"],
      "age": 11
    }
    """
    if not openai_client:
        return jsonify({"error": "OPENAI_API_KEY not configured"}), 500

    body = request.get_json(force=True, silent=True) or {}
    topic = body.get("topic", "")
    pdf_text = _safe_trim(body.get("pdf_text", ""))
    image_desc = body.get("image_descriptions", []) or []
    age = int(body.get("age", 11))

    user_payload = {
        "topic_hint": topic,
        "pdf_text_excerpt": pdf_text,
        "image_descriptions": image_desc,
        "age": age
    }

    resp = openai_client.chat.completions.create(
        model=OPENAI_MODEL_TEXT,
        temperature=0.4,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Create the JSON lesson strictly by the schema for this input:"},
            {"role": "user", "content": json.dumps(user_payload)}
        ]
    )
    text = (resp.choices[0].message.content or "").strip()
    # Strip code fences if present
    if text.startswith("```"):
        text = text.strip("`")
        if "{" in text and "}" in text:
            text = text[text.find("{"): text.rfind("}") + 1]

    try:
        lesson = json.loads(text)
    except Exception:
        return jsonify({"error": "Model did not return valid JSON", "raw": text}), 400

    return jsonify({"lesson": lesson})


# ----------------------------- IMAGE GENERATION --------------------------------
@app.post("/api/v2/generate_images")
def generate_images_v2():
    """
    Body: { "image_prompts": [{"id":"img1","prompt":"..."}] }
    Returns: { "images": [{"id":"img1","b64":"..."}], "errors":[...] }
    """
    if not openai_client:
        return jsonify({"error": "OPENAI_API_KEY not configured"}), 500

    body = request.get_json(force=True, silent=True) or {}
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
                size="512x512"  # smaller -> faster & fewer timeouts
            )
            b64 = img.data[0].b64_json
            out.append({"id": _id, "b64": b64})
            time.sleep(0.3)  # tiny pause helps with rate limits
        except Exception as e:
            errs.append({"id": _id, "error": str(e)})
            # continue to next prompt

    resp = {"images": out}
    if errs:
        resp["errors"] = errs
    return jsonify(resp)


# ----------------------------- SAVE IMAGE TO STORAGE ---------------------------
@app.post("/api/v2/save_image")
def save_image_to_supabase():
    """
    Body: { "id":"img1", "b64":"...", "filename":"lesson_123_img1.png" }
    Saves to bucket 'uploads/<filename>' and returns public URL.
    """
    if not supabase:
        return jsonify({"error": "Supabase not configured"}), 500

    body = request.get_json(force=True, silent=True) or {}
    b64 = body.get("b64", "")
    filename = (body.get("filename") or "image.png").strip()
    if not b64:
        return jsonify({"error": "Missing b64"}), 400

    try:
        img_bytes = base64.b64decode(b64)
        path = f"uploads/{filename}"
        # Overwrite if exists
        supabase.storage.from_("uploads").upload(
            path, img_bytes, {"content-type": "image/png", "x-upsert": "true"}
        )
        url = f"{SUPABASE_URL}/storage/v1/object/public/{path}"
        return jsonify({"ok": True, "id": body.get("id"), "url": url})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ------------------------------------------------------------------------------
# Main (dev only; Render uses gunicorn)
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
