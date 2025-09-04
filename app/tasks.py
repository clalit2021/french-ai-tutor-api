import os
import re
import json
import requests
from datetime import datetime
from celery import Celery
from celery.utils.log import get_task_logger
from supabase import create_client, Client
from app import mimi

logger = get_task_logger(__name__)

# Celery
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "")
celery_app = Celery("tasks", broker=CELERY_BROKER_URL)
celery_app.conf.broker_connection_retry_on_startup = True
celery_app.conf.result_backend = CELERY_BROKER_URL
celery_app.conf.task_ignore_result = True

# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
supabase: Client | None = None
if SUPABASE_URL and SUPABASE_SERVICE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# OpenAI (for Vision)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

def _public_storage_url(path: str) -> str:
    base = SUPABASE_URL.rstrip("/").replace("supabase.co", "supabase.co/storage/v1/object/public")
    return f"{base}/{path.lstrip('/')}"

@celery_app.task(name="tasks.process_lesson", bind=True)
def process_lesson(self, lesson_id: str, file_path: str, child_id: str):
    logger.info(f"[JOB] lesson={lesson_id} child={child_id} file={file_path}")

    def update(fields: dict):
        if supabase:
            supabase.table("lessons").update(fields).eq("id", lesson_id).execute()

    try:
        # 1) Download file
        url = _public_storage_url(file_path)
        r = requests.get(url, timeout=90)
        r.raise_for_status()
        content = r.content
        logger.info(f"[JOB] downloaded {len(content)} bytes")

        # 2) OCR / description
        text = ""
        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".pdf":
            import fitz
            import tempfile
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
                try:
                    os.remove(tmp_pdf)
                except Exception:
                    pass
            if not text.strip():
                text = "Lecon: PDF sans texte detectable."
        else:
            # Image -> OpenAI Vision (describe or transcribe)
            if OPENAI_API_KEY:
                try:
                    vision_api = "https://api.openai.com/v1/chat/completions"
                    sys_v = (
                        "You transcribe and summarize text from images in French when present. "
                        "If little text is present, briefly describe the scene in French for an 11-year-old learner."
                    )
                    user_v = [
                        {"type": "text", "text": "Lis l'image et donne les elements cles pour une mini-lecon (enfant 11 ans)."},
                        {"type": "image_url", "image_url": {"url": url}}
                    ]
                    payload_v = {
                        "model": "gpt-4o-mini",
                        "messages": [
                            {"role": "system", "content": sys_v},
                            {"role": "user", "content": user_v}
                        ],
                        "temperature": 0.2
                    }
                    vresp = requests.post(
                        vision_api,
                        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                        json=payload_v,
                        timeout=120
                    )
                    vresp.raise_for_status()
                    text = (vresp.json()["choices"][0]["message"]["content"] or "").strip()
                except Exception as e:
                    logger.warning(f"[JOB] vision OCR failed: {e}")
            if not text.strip():
                text = "Lecon: description d'image/texte non extrait."

        update({"ocr_text": text[:10000]})

        # 3) Build the lesson via shared Mimi generator
        image_desc = []
        if ext != ".pdf":
            image_desc = [f"Lesson page image at {url}"]

        lesson_json = mimi.build_mimi_lesson(
            topic="",
            ocr_text=text,
            image_descriptions=image_desc,
            age=11
        )

        # Safety: guarantee ui_steps exists
        if not isinstance(lesson_json, dict):
            lesson_json = {}
        ui = lesson_json.get("ui_steps")
        if not isinstance(ui, list) or len(ui) == 0:
            preview = (text or "Lecon").strip()[:160]
            lesson_json["ui_steps"] = [
                {"type": "text", "title": "Decouverte", "text": f"Indice: {preview}"},
                {"type": "speak", "title": "Repete", "text": "Je vois des images et j'apprends de nouveaux mots en francais !"}
            ]

        # 4) Save
        update({
            "lesson_data": lesson_json,
            "status": "completed",
            "completed_at": datetime.utcnow().isoformat()
        })
        logger.info(f"[JOB] lesson {lesson_id} completed")

    except Exception as e:
        logger.error(f"[JOB] failed: {e}", exc_info=True)
        update({"status": "error"})
