# app/tutor_sync.py
import os
import json
from flask import Blueprint, request, jsonify
from openai import OpenAI
from app import mimi

bp = Blueprint("tutor_sync", __name__)

OPENAI_MODEL_IMAGE = os.getenv("OPENAI_MODEL_IMAGE", "gpt-image-1")
OPENAI_MODEL_TEXT = os.getenv("OPENAI_MODEL_TEXT", "gpt-4o-mini")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

client = OpenAI(api_key=OPENAI_API_KEY)

def safe_trim(text: str, limit: int = 12000) -> str:
    return (text or "")[:limit]

@bp.route("/api/v2/lesson", methods=["POST"])
def build_lesson():
    body = request.get_json(force=True, silent=True) or {}
    topic = body.get("topic", "")
    pdf_text = safe_trim(body.get("pdf_text", ""))
    image_desc = body.get("image_descriptions", []) or []
    age = body.get("age", 11)

    lesson = mimi.build_mimi_lesson(
        topic=topic,
        ocr_text=pdf_text,
        image_descriptions=image_desc,
        age=age
    )
    return jsonify({"lesson": lesson})

@bp.route("/api/v2/generate_images", methods=["POST"])
def generate_images():
    body = request.get_json(force=True, silent=True) or {}
    prompts = body.get("image_prompts", []) or []
    out = []
    for p in prompts:
        prompt = p.get("prompt", "")
        if not prompt:
            continue
        img = client.images.generate(
            model=OPENAI_MODEL_IMAGE,
            prompt=prompt,
            size="1024x1024"
        )
        b64 = img.data[0].b64_json
        out.append({"id": p.get("id", ""), "b64": b64})
    return jsonify({"images": out})

@bp.route("/api/v2/chat", methods=["POST"])
def tutor_chat():
    body = request.get_json(force=True, silent=True) or {}
    lesson = body.get("lesson", {})
    history = body.get("history", [])[-10:]
    message = body.get("message", "")

    system = (
        "You are Mimi, the friendly tutor. Teach gently, one step at a time. "
        "Encourage speaking. Use simple FR + short EN glosses when needed. "
        "If asked, give tiny hints rather than full answers. "
        "Use the provided lesson JSON as context. Keep replies under 120 words.\n\n"
        f"Lesson JSON (context):\n{json.dumps(lesson)[:8000]}"
    )

    messages = [{"role": "system", "content": system}] + history + [{"role": "user", "content": message}]
    resp = client.chat.completions.create(model=OPENAI_MODEL_TEXT, temperature=0.5, messages=messages)
    answer = (resp.choices[0].message.content or "").strip()
    return jsonify({"reply": answer})
