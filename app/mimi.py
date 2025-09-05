# app/mimi.py
import os
import json
from typing import List, Dict, Any

from openai import OpenAI

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL_TEXT = os.getenv("OPENAI_MODEL_TEXT", "gpt-4o-mini")

# Single shared OpenAI client
openai_client: OpenAI | None = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

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

def _normalize_to_strict_schema(obj: Dict[str, Any]) -> Dict[str, Any]:
    title = obj.get("title") or obj.get("lesson_title") or "Leçon"

    # duration normalization
    if "duration" in obj and isinstance(obj["duration"], (int, float)):
        duration = f"{int(obj['duration'])} min"
    elif "duration" in obj and isinstance(obj["duration"], str):
        duration = obj["duration"]
    elif "duration_minutes" in obj:
        try:
            duration_val = int(obj.get("duration_minutes") or 30)
        except Exception:
            duration_val = 30
        duration = f"{duration_val} min"
    else:
        duration = "30 min"

    objectives = obj.get("objectives") or []
    if not isinstance(objectives, list):
        objectives = [str(objectives)]

    # plan normalization
    plan_in = obj.get("plan") or obj.get("activities") or obj.get("sections") or []
    plan_out: List[Dict[str, Any]] = []
    if isinstance(plan_in, list):
        for step in plan_in:
            if not isinstance(step, dict):
                continue
            name = step.get("name") or step.get("title") or "Étape"
            minutes = step.get("minutes") or step.get("duration") or step.get("duration_minutes") or ""
            if isinstance(minutes, (int, float)):
                minutes = str(int(minutes))
            teacher_script = (
                step.get("teacher_script")
                or step.get("script")
                or (" • ".join(step.get("steps", [])) if isinstance(step.get("steps"), list) else step.get("description"))
                or ""
            )
            plan_out.append({"name": name, "minutes": minutes, "teacher_script": teacher_script})

    # image prompts normalization
    image_prompts_in = obj.get("image_prompts") or obj.get("imagePrompts") or obj.get("slides") or []
    image_prompts: List[Dict[str, str]] = []
    if isinstance(image_prompts_in, list):
        for i, it in enumerate(image_prompts_in):
            if isinstance(it, dict):
                prompt = it.get("prompt") or it.get("image_prompt")
                if not prompt and isinstance(it.get("bullets"), list):
                    prompt = "Illustration pour: " + ", ".join(it["bullets"][:3])
                if prompt:
                    image_prompts.append({"id": it.get("id") or f"img{i+1}", "prompt": prompt})

    first_tutor_messages = obj.get("first_tutor_messages") or obj.get("firstTutorMessages") or []
    if not isinstance(first_tutor_messages, list) or not first_tutor_messages:
        first_tutor_messages = [f"Bonjour ! {title}"]

    return {
        "title": title,
        "duration": duration,
        "objectives": objectives,
        "plan": plan_out,
        "image_prompts": image_prompts,
        "first_tutor_messages": first_tutor_messages,
    }

def _chat_json_strict(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not openai_client:
        # Demo object if key missing
        return {
            "title": "Démo — Les symboles de la France",
            "duration": "30 min",
            "objectives": ["Reconnaître quelques symboles", "Dire 'C’est ...'"],
            "plan": [
                {"name": "Échauffement — Devine l’image", "minutes": "5", "teacher_script": "Regarde l’image. Qu’est-ce que c’est ? Répète : C’est un croissant !"},
                {"name": "Jeu — Associer", "minutes": "8", "teacher_script": "Associe la photo au mot. Répète ensemble."},
                {"name": "Découverte — Carte du monde", "minutes": "7", "teacher_script": "On parle français dans plusieurs pays."},
                {"name": "Jeu de rôle — Guide & Touriste", "minutes": "6", "teacher_script": "Tu es le guide, je suis le touriste."},
                {"name": "Créatif — Dessin", "minutes": "4", "teacher_script": "Dessine ton symbole préféré et dis : C’est ..."}
            ],
            "image_prompts": [
                {"id":"img1","prompt":"Kid-friendly illustration of the Eiffel Tower, bright colors, no text, no real faces, teaching style"},
                {"id":"img2","prompt":"Croissant on a small plate, friendly illustration, simple shapes, no text"}
            ],
            "first_tutor_messages": ["Bonjour ! Prêt(e) ? On commence avec un jeu de devinettes !"]
        }

    resp = openai_client.chat.completions.create(
        model=OPENAI_MODEL_TEXT,
        temperature=0.4,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    )
    text = (resp.choices[0].message.content or "").strip()
    raw = json.loads(text)  # enforced JSON
    return _normalize_to_strict_schema(raw)

def build_mimi_lesson(topic: str = "", ocr_text: str = "", image_descriptions: List[str] | None = None, age: int = 11) -> Dict[str, Any]:
    if image_descriptions is None:
        image_descriptions = []
    payload = {
        "topic_hint": topic or "",
        "pdf_text_excerpt": (ocr_text or "")[:12000],
        "image_descriptions": image_descriptions,
        "age": age
    }
    lesson = _chat_json_strict(payload)

    # Back-compat preview steps for legacy UI
    ui_steps: List[Dict[str, str]] = []
    for block in (lesson.get("plan") or [])[:3]:
        name = block.get("name") or "Activité"
        script = block.get("teacher_script") or ""
        ui_steps.append({"step": name})
        if script:
            ui_steps.append({"prompt": script.split("\n")[0][:140]})

    if not ui_steps:
        preview = (ocr_text or topic or "Nouvelle leçon").strip()[:160]
        ui_steps = [
            {"step": f"Explorons : {preview}"},
            {"prompt": "Répète : Bonjour Mimi ! Je suis prêt(e) à apprendre !"}
        ]

    lesson["ui_steps"] = ui_steps
    return lesson
