# tasks.py
import os
import re
import json
import requests
from datetime import datetime
from celery import Celery
from celery.utils.log import get_task_logger
from supabase import create_client, Client
from urllib.parse import quote, unquote  # safe encode/decode for storage paths

logger = get_task_logger(__name__)

# ---- Celery ----
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "")
celery_app = Celery("tasks", broker=CELERY_BROKER_URL)
celery_app.conf.broker_connection_retry_on_startup = True
celery_app.conf.result_backend = CELERY_BROKER_URL
celery_app.conf.task_ignore_result = True

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
    Build a public Supabase Storage URL and percent-encode safely.
    Accepts bucket-relative paths like 'uploads/Screenshot 2025-09-02 191857.png'
    """
    base = SUPABASE_URL.rstrip("/").replace(
        "supabase.co", "supabase.co/storage/v1/object/public"
    )
    clean = unquote(path).lstrip("/")      # normalize any %XX from user input
    encoded = quote(clean, safe="/")       # keep slashes; encode spaces -> %20
    return f"{base}/{encoded}"

def _force_image_urls(lesson_json: dict, url: str) -> dict:
    """
    Ensure every image shown uses our known-good Supabase URL.
    - Overwrite any step.type == 'image' -> image_url = url
    - If step.images list exists, reduce to first item and set its url = url
    - If no image step exists, inject one at the top
    """
    ui = lesson_json.get("ui_steps") or []
    has_image = False

    for step in ui:
        if not isinstance(step, dict):
            continue

        if step.get("type") == "image":
            step["image_url"] = url
            step.setdefault("caption", "Regarde l'image et dis ce que tu vois.")
            has_image = True

        imgs = step.get("images")
        if isinstance(imgs, list) and imgs:
            first = imgs[0] if isinstance(imgs[0], dict) else {}
            first["url"] = url
            first.pop("image_url", None)
            step["images"] = [first]
            has_image = True

    if not has_image:
        ui.insert(0, {
            "type": "image",
            "image_url": url,
            "caption": "Regarde l'image et dis ce que tu vois."
        })

    lesson_json["ui_steps"] = ui
    return lesson_json

@celery_app.task(name="tasks.process_lesson", bind=True)
def process_lesson(self, lesson_id: str, file_path: str, child_id: str):
    """
    1) Download file from Supabase public URL
    2) OCR/Describe:
         - PDF -> PyMuPDF text extraction
         - Image -> OpenAI Vision
    3) Generate lesson JSON with:
         - plan: goals + 30-minute activities with teacher/student scripts
         - ui_steps: interactive cards (note/speak/question/image)
    4) Save to Supabase
    """
    logger.info(f"[JOB] lesson={lesson_id} child={child_id} file={file_path}")

    def update(fields: dict):
        if supabase:
            supabase.table("lessons").update(fields).eq("id", lesson_id).execute()

    try:
        # --- 1) Build public URL from the plain path (with spaces)
        url = _public_storage_url(file_path)
        logger.info(f"[JOB] image_url={url}")

        # Verify object exists (also fetch bytes if needed for PDF OCR)
        resp = requests.get(url, timeout=90)
        resp.raise_for_status()
        content = resp.content
        logger.info(f"[JOB] downloaded {len(content)} bytes")

        # --- 2) OCR / description
        text = ""
        ext = os.path.splitext(file_path)[1].lower()

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
                try:
                    os.remove(tmp_pdf)
                except Exception:
                    pass
            if not text.strip():
                text = "Leçon: PDF sans texte détectable."
        else:
            # Image -> OpenAI Vision (no Tesseract)
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

        # --- 3) Build full lesson JSON (plan + ui_steps)
        if OPENAI_API_KEY:
            api = "https://api.openai.com/v1/chat/completions"

            # STRICT schema with a real lesson plan + interactive steps
            sys = (
                "You are a playful, structured French (FLE A1/A2) tutor for an 11-year-old.\n"
                "OUTPUT STRICTLY VALID JSON ONLY, parseable by json.loads, no extra text.\n"
                "SCHEMA:\n"
                "{\n"
                '  "plan": {\n'
                '    "title": "string",\n'
                '    "goals": ["string", ...],\n'
                '    "duration_min": 30,\n'
                '    "materials": ["string", ...],\n'
                '    "activities": [\n'
                '      {\n'
                '        "title": "string",\n'
                '        "minutes": 5,\n'
                '        "script": {\n'
                '          "teacher_says": ["string", ...],\n'
                '          "student_does": ["string", ...]\n'
                '        }\n'
                '      }, ...\n'
                '    ]\n'
                '  },\n'
                '  "ui_steps": [\n'
                '    {"type":"note","title":"...","text":"..."},\n'
                '    {"type":"speak","title":"...","text":"..."},\n'
                '    {"type":"question","prompt":"...","options":["..."],"answer_index":0},\n'
                '    {"type":"image","image_url":"<URL>","caption":"..."}\n'
                '  ]\n'
                "}\n"
                "CONSTRAINTS:\n"
                "- Use kid-friendly French (A1/A2), short sentences.\n"
                "- Include at least one 'image' step in ui_steps using the provided URL.\n"
                "- 5 activities totalling ~30 minutes. Include warm-up, matching, world map discovery, role-play, creative wrap-up.\n"
                "- Align with goals from symbols of France and La Francophonie, even if the image shows only part of it.\n"
            )

            # We nudge content toward your desired outline inside the user message.
            user_content = [
                {
                    "type": "text",
                    "text": (
                        "Base the lesson on this OCR/description:\n"
                        f"{text[:1200]}\n\n"
                        "Build a 30-minute plan with these themes:\n"
                        "- Reconnaître des symboles français (tour Eiffel, croissant, baguette, fromage)\n"
                        "- Découvrir la Francophonie (Montréal, Bruxelles, Martinique, Bamako, Tahiti)\n\n"
                        "Activities to include (adjust to fit the page content if needed):\n"
                        "1) Échauffement – deviner la photo\n"
                        "2) Jeu d’association – France en images\n"
                        "3) Découverte carte – Francophonie\n"
                        "4) Jeu de rôle – Guide touristique\n"
                        "5) Création – Mon pays\n\n"
                        "Return STRICT JSON with both keys: plan + ui_steps. "
                        "In ui_steps include: one image step (use the provided URL), one speak prompt, and one simple question."
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

            # Strict parse; if it fails, use regex to extract the first {...}
            try:
                lesson_json = json.loads(content_str)
            except Exception:
                match = re.search(r"\{.*\}", content_str, re.S)
                if match:
                    try:
                        lesson_json = json.loads(match.group(0))
                    except Exception:
                        lesson_json = {
                            "plan": {
                                "title": "Mini-leçon",
                                "goals": ["Découvrir des symboles français", "Explorer la Francophonie"],
                                "duration_min": 30,
                                "materials": [],
                                "activities": []
                            },
                            "ui_steps": [{"type": "note", "title": "Erreur de format", "text": "JSON parse failed"}]
                        }
                else:
                    lesson_json = {
                        "plan": {
                            "title": "Mini-leçon",
                            "goals": ["Découvrir des symboles français", "Explorer la Francophonie"],
                            "duration_min": 30,
                            "materials": [],
                            "activities": []
                        },
                        "ui_steps": [{"type": "note", "title": "Erreur de format", "text": "JSON parse failed"}]
                    }

            # Ensure ui_steps images use the real Supabase URL
            lesson_json = _force_image_urls(lesson_json, url)

        else:
            lesson_json = {
                "plan": {
                    "title": "Mini-leçon (démo)",
                    "goals": ["Clé API OpenAI manquante"],
                    "duration_min": 5,
                    "materials": [],
                    "activities": []
                },
                "ui_steps": [{"type": "note", "title": "Démo", "text": "OPENAI_API_KEY missing"}]
            }

        # --- 4) Save & finish
        update({
            "lesson_data": lesson_json,
            "status": "completed",
            "completed_at": datetime.utcnow().isoformat()
        })
        logger.info(f"[JOB] lesson {lesson_id} completed")

    except Exception as e:
        logger.error(f"[JOB] failed: {e}", exc_info=True)
        update({"status": "error"})
