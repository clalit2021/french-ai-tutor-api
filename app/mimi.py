# app/mimi.py
import os
import re
import json
import requests

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL_TEXT = os.getenv("OPENAI_MODEL_TEXT", "gpt-4o-mini")

SYSTEM_PROMPT = """You are "Mimi", a warm, patient French tutor for an 11-year-old child (A1–A2 level).
Input may contain: (a) extracted text from a PDF, (b) one or more images (described),
or (c) a topic string. Your job is to turn that input into a complete, kid-friendly,
30-minute interactive lesson.

Constraints:
- Keep language SIMPLE and encouraging. Use short sentences. Avoid jargon.
- Build a clear 30-minute sequence of activities (5–7 blocks).
- Always include speaking aloud, call-and-response, mini-games, and a creative wrap-up.
- Prepare exercises with correct answers and brief explanations.
- Propose 6–10 kid-safe image prompts (NO brand names, NO text in-image, no faces of real people).
- Output MUST be valid JSON matching the schema below—no commentary.

JSON schema to produce:
{
  "title": "string",
  "age": 11,
  "level": "A1-A2",
  "topic_detected": "string",
  "objectives": ["string", "..."],
  "duration_minutes": 30,
  "plan": [
    {
      "minutes": 5,
      "name": "Warm-up - Guess the photo",
      "teacher_script": "What you say step by step in simple English+French lines",
      "student_actions": ["guess pictures", "repeat sentences"],
      "target_phrases_fr": ["C'est ...", "Voila ..."]
    }
  ],
  "slides": [
    { "title": "France en photos", "bullets": ["..."], "speak_aloud_fr": "..." }
  ],
  "image_prompts": [
    { "id": "img1", "prompt": "Kid-friendly illustration of [X]; bright, simple; no text; 1024x1024; for teaching." }
  ],
  "exercises": [
    {
      "type": "mcq",
      "question_fr": "C'est ... ?",
      "options": ["la baguette", "le sushi", "le taco"],
      "answer_index": 0,
      "explain_en": "In France we say 'la baguette'."
    },
    {
      "type": "fill",
      "question_fr": "C'___ la tour Eiffel.",
      "answer_text": "est",
      "explain_en": "C'est = it is."
    }
  ],
  "first_tutor_messages": [
    "Bonjour ! Ready to explore France together? First, look at this picture..."
  ]
}

If the input looks like the "France & Francophonie" spread (Eiffel Tower, croissant, Montreal, etc.),
adapt the plan to: warm-up guess-the-photo -> matching game -> world map discovery ->
role-play ("guide & tourist") -> creative wrap-up. Keep it playful.
"""

def _chat_json_strict(payload: dict) -> dict:
    if not OPENAI_API_KEY:
        return {
            "title": "Demo",
            "duration_minutes": 30,
            "plan": [],
            "slides": [],
            "image_prompts": [],
            "exercises": [],
            "first_tutor_messages": ["OPENAI_API_KEY missing."]
        }

    api = "https://api.openai.com/v1/chat/completions"
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "Create the JSON lesson strictly by the schema for this input:"},
        {"role": "user", "content": json.dumps(payload)}
    ]
    resp = requests.post(
        api,
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        },
        json={"model": OPENAI_MODEL_TEXT, "temperature": 0.4, "messages": messages},
        timeout=120
    )
    resp.raise_for_status()
    text = (resp.json()["choices"][0]["message"]["content"] or "").strip()

    if text.startswith("```"):
        text = text.strip("`")
        s, e = text.find("{"), text.rfind("}")
        if s != -1 and e != -1 and e > s:
            text = text[s:e+1]

    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ValueError("Model did not return JSON")
        return json.loads(m.group(0))

def build_mimi_lesson(topic: str = "", ocr_text: str = "", image_descriptions=None, age: int = 11) -> dict:
    if image_descriptions is None:
        image_descriptions = []
    payload = {
        "topic_hint": topic or "",
        "pdf_text_excerpt": (ocr_text or "")[:12000],
        "image_descriptions": image_descriptions,
        "age": age
    }
    lesson = _chat_json_strict(payload)

    # derive simple ui_steps for your existing frontend
    ui_steps = []
    plan = lesson.get("plan") or []
    for block in plan[:3]:
        name = block.get("name") or "Activite"
        script = block.get("teacher_script") or ""
        ui_steps.append({"step": name})
        if script:
            ui_steps.append({"prompt": script.split("\n")[0][:140]})

    if not ui_steps:
        preview = (ocr_text or topic or "Nouvelle lecon").strip()[:160]
        ui_steps = [
            {"step": f"Explorons: {preview}"},
            {"prompt": "Repete: Bonjour Mimi ! Je suis pret(e) a apprendre !"}
        ]

    lesson["ui_steps"] = ui_steps
    return lesson
