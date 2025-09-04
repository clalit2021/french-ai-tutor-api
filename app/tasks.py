import os, json, requests
from datetime import datetime
from celery import Celery
from celery.utils.log import get_task_logger
from supabase import create_client, Client

logger = get_task_logger(__name__)

# ---- Celery ----
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "")
celery_app = Celery("tasks", broker=CELERY_BROKER_URL)

# Prevent AMQP attempts and silence the startup warning
celery_app.conf.broker_connection_retry_on_startup = True
celery_app.conf.result_backend = CELERY_BROKER_URL  # use Redis for results
celery_app.conf.task_ignore_result = True           # or ignore results entirely

# ---- Supabase ----
SUPABASE_URL = os.getenv("SUPABASE_URL","")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY","")
supabase: Client | None = None
if SUPABASE_URL and SUPABASE_SERVICE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# ---- OpenAI ----
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY","")

def _public_storage_url(path:str)->str:
    """
    Convert bucket path like 'uploads/folder/file.png' to a public URL
    e.g. https://<proj>.supabase.co/storage/v1/object/public/uploads/folder/file.png
    """
    base = SUPABASE_URL.replace("supabase.co", "supabase.co/storage/v1/object/public")
    return f"{base}/{path}"

@celery_app.task(name="tasks.process_lesson", bind=True)
def process_lesson(self, lesson_id: str, file_path: str, child_id: str):
    """
    Background job:
      1) Download file from Supabase public URL
      2) OCR/Describe content:
           - PDF -> PyMuPDF text extraction
           - Image -> OpenAI Vision (no Tesseract needed)
      3) Use OpenAI to generate interactive lesson JSON (image-aware)
      4) Save to Supabase: status=completed
    """
    logger.info(f"[JOB] lesson={lesson_id} child={child_id} file={file_path}")

    def update(fields:dict):
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
            # PDF -> extract text with PyMuPDF
            import fitz, tempfile
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                f.write(content); tmp_pdf = f.name
            doc = fitz.open(tmp_pdf)
            for p in doc:
                try:
                    text += p.get_text() or ""
                except Exception:
                    pass
            doc.close()
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
                        {"type": "text",
                         "text": "Lis le texte de l'image (en français) et résume les éléments clés pour une mini-leçon FLE (enfant 11 ans)."},
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
                        headers={
                            "Authorization": f"Bearer {OPENAI_API_KEY}",
                            "Content-Type": "application/json"
                        },
                        json=payload_v,
                        timeout=120
                    )
                    vresp.raise_for_status()
                    text = vresp.json()["choices"][0]["message"]["content"].strip()
                except Exception as e:
                    logger.warning(f"[JOB] vision OCR failed: {e}")
            if not text.strip():
                text = "Leçon: description d'image/texte non extrait."

        update({"ocr_text": text[:10000]})

        # 3) Build interactive lesson JSON (image-aware)
        if OPENAI_API_KEY:
            api = "https://api.openai.com/v1/chat/completions"
            sys = (
  "You are a playful French tutor for an 11-year-old. "
  "Always reply with STRICTLY valid JSON, nothing else. "
  "Do not add explanations, apologies, or text outside JSON. "
  "Output must be parseable by json.loads in Python. "
  "Use this schema only: {\"ui_steps\": [...]}"
)

            user_content = [
                {"type": "text",
                 "text": (
                     "Create a short interactive French lesson (2–4 steps) based on this OCR/description:\n"
                     f"{text[:1200]}\n"
                     'Return STRICTLY valid JSON in this schema: {"ui_steps":[ ... ]}. '
                     "Use kid-friendly French (FLE A1/A2). Include at least one speak-aloud prompt."
                 )}
            ]
            # If it's an image, attach it so the model can use visual context
            if ext != ".pdf":
                user_content.append({"type": "image_url", "image_url": {"url": url}})

            payload = {
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": sys},
                    {"role": "user", "content": user_content}
                ],
                "temperature": 0.4
            }
            resp = requests.post(
                api,
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}",
                         "Content-Type": "application/json"},
                json=payload,
                timeout=120
            )
            resp.raise_for_status()
            content_str = resp.json()["choices"][0]["message"]["content"]
            try:
                lesson_json = json.loads(content_str)
            except Exception:
                lesson_json = {
                    "ui_steps": [
                        {"type": "note", "text": "JSON parse failed; using fallback."},
                        {"type": "question", "question": "Où parle-t-on français ?", "options": ["Montréal", "Tokyo"], "correct_option": 0}
                    ]
                }
        else:
            lesson_json = {"ui_steps": [{"type": "note", "text": "OPENAI_API_KEY missing. Demo step only."}]}

        # 4) Save and finish
        update({
            "lesson_data": lesson_json,
            "status": "completed",
            "completed_at": datetime.utcnow().isoformat()
        })
        logger.info(f"[JOB] lesson {lesson_id} completed")

    except Exception as e:
        logger.error(f"[JOB] failed: {e}", exc_info=True)
        update({"status": "error"})
