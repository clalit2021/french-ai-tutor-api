# tasks.py
import os
import re
import json
import requests
from datetime import datetime
from celery import Celery
from celery.utils.log import get_task_logger
from supabase import create_client, Client
from requests.utils import unquote  # <-- NEW: to decode %20 etc.

logger = get_task_logger(__name__)

# ---- Celery ----
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "")
celery_app = Celery("tasks", broker=CELERY_BROKER_URL)
# prevent AMQP attempts & silence startup warning
celery_app.conf.broker_connection_retry_on_startup = True
celery_app.conf.result_backend = CELERY_BROKER_URL  # use Redis for results
celery_app.conf.task_ignore_result = True           # or ignore results entirely

# ---- Supabase ----
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
supabase: Client | None = None
if SUPABASE_URL and SUPABASE_SERVICE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# ---- OpenAI ----
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

def _public_storage_url(path: str) -> str:
    """
    Convert bucket path like 'uploads/folder/file.png' to public URL:
    https://<proj>.supabase.co/storage/v1/object/public/uploads/folder/file.png
    """
    base = SUPABASE_URL.rstrip("/").replace(
        "supabase.co", "supabase.co/storage/v1/object/public"
    )
    return f"{base}/{path.lstrip('/')}"

@celery_app.task(name="tasks.process_lesson", bind=True)
def process_lesson(self, lesson_id: str, file_path: str, child_id: str):
    """
    1) Download file from Supabase public URL
    2) OCR/Describe:
         - PDF -> PyMuPDF text extraction
         - Image -> OpenAI Vision (no Tesseract needed)
    3) Generate interactive lesson JSON (STRICT + image step)
    4) Save to Supabase (status=completed) or mark error
    """
    logger.info(f"[JOB] lesson={lesson_id} child={child_id} file={file_path}")

    def update(fields: dict):
        if supabase:
            supabase.table("lessons").update(fields).eq("id", lesson_id).execute()

    try:
        # --- 1) Build public URL (decode %20, %28, etc. first)
        clean_path = unquote(file_path) if "%" in file_path else file_path  # <-- NEW
        url = _public_storage_url(clean_path)

        # Download file bytes (also verifies object exists)
        resp = requests.get(url, timeout=90)
        resp.raise_for_status()
        content = resp.content
        logger.info(f"[JOB] downloaded {len(content)} bytes")

        # --- 2) OCR / description
        text = ""
        ext = os.path.splitext(clean_path)[1].lower()

        if ext == ".pdf":
            # PDF -> extract text with PyMuPDF
            import fitz, tempfile
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                f.write(content)
                tmp_pdf = f.name
            try:
                doc = fitz.open(tmp_pdf)
                for page in doc:
                    try:
                        text += page.get_text() or ""
                    except Exception:
                        pass
                doc.close()
            finally:
                try: os.remove(tmp_pdf)
                except Exception: pass
            if not text.strip():
                text = "Leçon: PDF sans texte détectable."
        else:
            # Image -> OpenAI Vision (no Tesseract needed)
            if OPENAI_API_KEY:
                try:
                    vision_api = "https://api.openai.com/v1/chat/completions"
                    sys_v = (
                        "You transcribe and summarize text from images in French when present. "
                        "If little text is present, briefly describe the scene in French for an 11-year-old learner."
                    )
                    user_v = [
                        {
                            "type": "text",
                            "text": "Lis le texte de l'image (en français) et résume les éléments clés pour une mini-leçon FLE (enfant 11 ans)."
                        },
                        {"type": "image_url", "image_url": {"url": url}},
                    ]
                    payload_v = {
                        "model": "gpt-4o-mini",
                        "messages": [
                            {"role": "system", "content": sys_v},
                            {"role": "user", "content": user_v},
                        ],
                        "temperature": 0.2,
                    }
                    vresp = requests.post(
                        vision_api,
                        headers={
                            "Authorization": f"Bearer {OPENAI_API_KEY}",
                            "Content-Type": "application/json",
                        },
                        json=payload_v,
                        timeout=120,
                    )
                    vresp.raise_for_status()
                    text = vresp.json()["choices"][0]["message"]["content"].strip()
                except Exception as e:
                    logger.warning(f"[JOB] vision OCR failed: {e}")
            if not text.strip():
                text = "Leçon: description d'image/texte non extrait."

        update({"ocr_text": text[:10000]})

        # --- 3) Build interactive lesson JSON (STRICT + forced image step)
        if OPENAI_API_KEY:
            api = "https://api.openai.com/v1/chat/completions"

            # Tight schema + requirement to include an image step using the same URL
            sys = (
                "You are a playful French tutor for an 11-year-old.\n"
                "Always reply with STRICTLY valid JSON and NOTHING else.\n"
                "Output must be parseable by json.loads.\n"
                "Use ONLY this schema: {\"ui_steps\": [ ... ]}\n"
                "Allowed step shapes exactly:\n"
                "  {\"type\":\"note\",\"title\":\"...\",\"text\":\"...\"}\n"
                "  {\"type\":\"speak\",\"title\":\"...\",\"text\":\"...\"}\n"
                "  {\"type\":\"question\",\"prompt\":\"...\",\"options\":[\"...\"],\"answer_index\":0}\n"
                "  {\"type\":\"image\",\"image_url\":\"<URL>\",\"caption\":\"...\"}\n"
                "You MUST include at least ONE image step (type=image) and set its image_url to the URL provided."
            )

            user_content = [
                {
                    "type": "text",
                    "text": (
                        "Create a 2–4 step interactive FLE mini-lesson (A1/A2) based on this OCR/description:\n"
                        f"{text[:1200]}\n"
                        'Return STRICT JSON: {"ui_steps":[ ... ]}.\n'
                        "Include at least one speak-aloud prompt AND at least one image step "
                        "with \"type\":\"image\" and \"image_url\" equal to the provided URL. "
                        "Kid-friendly French only."
                    ),
                },
                {"type": "image_url", "image_url": {"url": url}},  # visual context
            ]

            payload = {
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": sys},
                    {"role": "user", "content": user_content},
                ],
                "temperature": 0.4,
            }

            lresp = requests.post(
                api,
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=120,
            )
            lresp.raise_for_status()
            content_str = lresp.json()["choices"][0]["message"]["content"]

            # Parse JSON strictly; if it fails, try regex to extract {...}
            try:
                lesson_json = json.loads(content_str)
            except Exception:
                match = re.search(r"\{.*\}", content_str, re.S)
                if match:
                    try:
                        lesson_json = json.loads(match.group(0))
                    except Exception:
                        lesson_json = {"ui_steps": [{"type": "note", "text": "JSON parse failed; fallback."}]}
                else:
                    lesson_json = {"ui_steps": [{"type": "note", "text": "JSON parse failed; fallback."}]}

            # Safety net: if model forgot an image step, inject one
            has_image = any(
                isinstance(s, dict) and s.get("type") == "image" and s.get("image_url")
                for s in lesson_json.get("ui_steps", [])
            )
            if not has_image:
                lesson_json.setdefault("ui_steps", []).insert(0, {
                    "type": "image",
                    "image_url": url,
                    "caption": "Regarde l'image et dis ce que tu vois."
                })
        else:
            lesson_json = {"ui_steps": [{"type": "note", "text": "OPENAI_API_KEY missing. Demo step only."}]}

        # --- 4) Save and finish
        update({
            "lesson_data": lesson_json,
            "status": "completed",
            "completed_at": datetime.utcnow().isoformat()
        })
        logger.info(f"[JOB] lesson {lesson_id} completed")

    except Exception as e:
        logger.error(f"[JOB] failed: {e}", exc_info=True)
        update({"status": "error"})
