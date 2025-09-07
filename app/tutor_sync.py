# app/tutor_sync.py
import os
import json
from typing import List, Dict, Any, Optional
from flask import Blueprint, request, jsonify

from app import mimi  # lesson builder module

bp = Blueprint("tutor_sync", __name__)

OPENAI_MODEL_IMAGE = os.getenv("OPENAI_MODEL_IMAGE", "gpt-image-1")
OPENAI_MODEL_TEXT  = os.getenv("OPENAI_MODEL_TEXT", "gpt-4o-mini")
OPENAI_API_KEY     = os.getenv("OPENAI_API_KEY", "")

# ----- Lazy OpenAI client (so module imports even without key/SDK) -----
_openai_client = None
def _client():
    global _openai_client
    if _openai_client is not None:
        return _openai_client
    if not OPENAI_API_KEY:
        return None
    try:
        from openai import OpenAI  # require openai>=1.40 in requirements.txt
        _openai_client = OpenAI(api_key=OPENAI_API_KEY)
        return _openai_client
    except Exception as e:
        print("[BOOT] OpenAI client init failed:", repr(e))
        return None

# ----- Helpers -----
def _safe_trim(text: str, limit: int = 12000) -> str:
    return (text or "")[:limit]

def _normalize_history(raw: Any, limit: int = 10) -> List[Dict[str, str]]:
    """
    Accepts:
      - list of {role, content}
      - list of strings (treated as alternating user/assistant, starting with user)
    Returns last `limit` messages in OpenAI format.
    """
    if not isinstance(raw, list):
        return []
    msgs: List[Dict[str, str]] = []
    alt_roles = ["user", "assistant"]
    alt_i = 0
    for item in raw:
        if isinstance(item, dict) and "role" in item and "content" in item:
            role = item["role"]
            content = str(item["content"] or "")[:800]
            if role in ("system", "user", "assistant") and content:
                msgs.append({"role": role, "content": content})
        elif isinstance(item, str):
            msgs.append({"role": alt_roles[alt_i % 2], "content": item[:800]})
            alt_i += 1
    return msgs[-limit:]

# =========================
# Routes
# =========================

@bp.route("/api/v2/lesson", methods=["POST"])
def build_lesson():
    body = request.get_json(force=True, silent=True) or {}
    topic      = body.get("topic", "") or ""
    pdf_text   = _safe_trim(body.get("pdf_text", ""))
    image_desc = body.get("image_descriptions", []) or []
    age        = int(body.get("age", 11) or 11)

    try:
        lesson = mimi.build_mimi_lesson(
            topic=topic,
            ocr_text=pdf_text,
            image_descriptions=image_desc if isinstance(image_desc, list) else [],
            age=age
        )
        return jsonify({"ok": True, "lesson": lesson})
    except Exception as e:
        print("[V2/lesson][ERROR]", repr(e))
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/v2/generate_images", methods=["POST"])
def generate_images():
    body = request.get_json(force=True, silent=True) or {}
    prompts = body.get("image_prompts", []) or []

    cli = _client()
    if cli is None:
        return jsonify({"ok": False, "error": "OPENAI_API_KEY missing or OpenAI SDK not installed"}), 503

    out = []
    for p in prompts:
        try:
            prompt = (p.get("prompt") or "").strip()
            if not prompt:
                continue
            pid = p.get("id") or f"img{len(out)+1}"
            resp = cli.images.generate(
                model=OPENAI_MODEL_IMAGE,
                prompt=prompt[:1800],   # gentle cap for prompt size
                size="1024x1024"
            )
            b64 = resp.data[0].b64_json
            data_url = f"data:image/png;base64,{b64}"
            out.append({"id": pid, "b64": b64, "data_url": data_url})
        except Exception as e:
            # Don't fail the batch on a single error
            out.append({"id": p.get("id") or "", "error": str(e)})

    return jsonify({"ok": True, "images": out})


@bp.route("/api/v2/chat", methods=["POST"])
def tutor_chat():
    body: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
    lesson  = body.get("lesson", {}) or {}
    history = _normalize_history(body.get("history", []), limit=10)
    message = (body.get("message") or "").strip()

    if not message:
        return jsonify({"ok": False, "error": "message is required"}), 400

    cli = _client()
    if cli is None:
        # Simple offline reply for dev
        return jsonify({"ok": True, "reply": "Mode démo : dis-moi de quoi tu veux parler aujourd’hui 😊"})

    # Put a trimmed lesson JSON into the system prompt as context
    lesson_ctx = json.dumps(lesson, ensure_ascii=False)[:8000]
    system = (
        "You are Mimi, a friendly French tutor. Teach gently, one step at a time. "
        "Encourage speaking. Use simple FR with tiny EN glosses when needed. "
        'Give hints instead of full answers. Keep replies under 120 words.\n\n'
        f"Lesson JSON (context):\n{lesson_ctx}"
    )

    messages = [{"role": "system", "content": system}] + history + [{"role": "user", "content": message}]
    try:
        resp = cli.chat.completions.create(
            model=OPENAI_MODEL_TEXT,
            temperature=0.5,
            messages=messages,
        )
        answer = (resp.choices[0].message.content or "").strip()
        return jsonify({"ok": True, "reply": answer})
    except Exception as e:
        print("[V2/chat][ERROR]", repr(e))
        return jsonify({"ok": False, "error": str(e)}), 500
