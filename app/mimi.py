# app/mimi.py
import os, json, time
from typing import List, Dict, Any, Optional

try:
    # OpenAI SDK path (if you choose to keep the SDK)
    from openai import OpenAI  # requires 'openai' in requirements.txt
except Exception:
    OpenAI = None  # type: ignore

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL_TEXT = os.getenv("OPENAI_MODEL_TEXT", "gpt-4o-mini")
OPENAI_TIMEOUT = float(os.getenv("OPENAI_TIMEOUT", "30"))  # seconds
OPENAI_RETRIES = int(os.getenv("OPENAI_RETRIES", "2"))

# Single shared OpenAI client (SDK) — only if both key and SDK exist
openai_client: Optional["OpenAI"] = None
if OPENAI_API_KEY and OpenAI is not None:
    try:
        openai_client = OpenAI(api_key=OPENAI_API_KEY)
    except Exception as e:
        print("[BOOT] OpenAI client init failed:", repr(e))
        openai_client = None
else:
    if not OPENAI_API_KEY:
        print("[BOOT] OPENAI_API_KEY not set — running in demo mode")
    elif OpenAI is None:
        print("[BOOT] 'openai' package not installed — install or switch to requests fallback")

SYSTEM_PROMPT = """
You are Mimi, a warm, patient French tutor for an 11-year-old (A1–A2 level).
Turn the input (topic, text excerpt, image descriptions) into a complete 30-minute lesson.

Return STRICT JSON ONLY with EXACTLY these keys:

{
  "title": "string",
  "duration": "string (e.g., '30 min')",
  "objectives": ["string", "..."],
  "materials": ["string", "..."],
  "warm_up": { "name": "string", "minutes": "string or number", "teacher_script": "string" },
  "vocab_cards": { "name": "string", "minutes": "string or number", "teacher_script": "string" },
  "mini_story": { "name": "string", "minutes": "string or number", "teacher_script": "string" },
  "phonics_focus": { "name": "string", "minutes": "string or number", "teacher_script": "string" },
  "practice": { "name": "string", "minutes": "string or number", "teacher_script": "string" },
  "wrap_up": { "name": "string", "minutes": "string or number", "teacher_script": "string" },
  "homework": { "name": "string", "minutes": "string or number", "teacher_script": "string" },
  "image_prompts": [
    { "id": "string", "prompt": "string" }
  ],
  "first_tutor_messages": ["string", "..."]
}

Rules:
- No extra keys.
- No code fences.
- No prose outside JSON.
- Objectives: 2–3 short bullet points.
- Provide a materials list for the teacher.
- Make language simple and encouraging; short sentences; playful tone.
- Include speaking aloud, call-and-response, mini-games, and a creative wrap-up.
- Provide 5–8 kid-safe image prompts (no brands, no text in-image, no real faces).
- Use image prompt IDs like "cover_scene", "vocab_card_<word>", "story_frame", "phonics_poster", "reward_sticker".
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
    if len(objectives) < 2 or len(objectives) > 3:
        raise ValueError("Expected 2-3 objectives")

    materials = obj.get("materials") or obj.get("material_list") or []
    if isinstance(materials, str):
        materials = [materials]
    if not isinstance(materials, list):
        materials = [str(materials)]
    if not materials:
        raise ValueError("Materials list required")

    def _norm_activity(key: str) -> Dict[str, str]:
        raw = obj.get(key) or {}
        if not isinstance(raw, dict):
            raw = {}
        name = raw.get("name") or raw.get("title") or key.replace("_", " ").title()
        minutes = raw.get("minutes") or raw.get("duration") or raw.get("duration_minutes") or ""
        if isinstance(minutes, (int, float)):
            minutes = str(int(minutes))
        teacher_script = (
            raw.get("teacher_script")
            or raw.get("script")
            or (" • ".join(raw.get("steps", [])) if isinstance(raw.get("steps"), list) else raw.get("description"))
            or ""
        )
        return {"name": name, "minutes": minutes, "teacher_script": teacher_script}

    activity_keys = [
        "warm_up",
        "vocab_cards",
        "mini_story",
        "phonics_focus",
        "practice",
        "wrap_up",
        "homework",
    ]
    activities = {k: _norm_activity(k) for k in activity_keys}

    # plan normalization
    plan_in = obj.get("plan") or obj.get("activities") or obj.get("sections") or []
    plan_out: List[Dict[str, Any]] = []
    if isinstance(plan_in, list) and plan_in:
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
    else:
        for key in activity_keys:
            act = activities.get(key)
            if act.get("teacher_script") or act.get("minutes"):
                plan_out.append(act)

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
    result: Dict[str, Any] = {
        "title": title,
        "duration": duration,
        "objectives": objectives,
        "materials": materials,
        "plan": plan_out,
        "image_prompts": image_prompts,
        "first_tutor_messages": first_tutor_messages,
    }
    result.update(activities)
    return result

def _extract_json_loose(text: str) -> Dict[str, Any]:
    """As a last resort, try to find the outermost JSON object in text."""
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end+1])
        except Exception:
            pass
    raise ValueError("Model did not return valid JSON")

def _chat_json_strict(payload: Dict[str, Any]) -> Dict[str, Any]:
    # Demo path if no key or no client
    if not OPENAI_API_KEY or openai_client is None:
        demo = {
            "title": "Démo — Les symboles de la France",
            "duration": "30 min",
            "objectives": ["Reconnaître quelques symboles", "Dire 'C’est ...'"] ,
            "materials": ["Images imprimées", "Crayons"],
            "warm_up": {
                "name": "Échauffement — Devine l’image",
                "minutes": "5",
                "teacher_script": "Regarde l’image. Qu’est-ce que c’est ? Répète : C’est un croissant !"
            },
            "vocab_cards": {
                "name": "Cartes de vocabulaire",
                "minutes": "8",
                "teacher_script": "Associe la photo au mot. Répète ensemble."
            },
            "mini_story": {
                "name": "Découverte — Carte du monde",
                "minutes": "7",
                "teacher_script": "On parle français dans plusieurs pays."
            },
            "phonics_focus": {
                "name": "Sons — 'ou'",
                "minutes": "4",
                "teacher_script": "Écoute et répète le son 'ou' comme dans 'bonjour'."
            },
            "practice": {
                "name": "Jeu de rôle — Guide & Touriste",
                "minutes": "6",
                "teacher_script": "Tu es le guide, je suis le touriste."
            },
            "wrap_up": {
                "name": "Créatif — Dessin",
                "minutes": "4",
                "teacher_script": "Dessine ton symbole préféré et dis : C’est ..."
            },
            "homework": {
                "name": "À la maison",
                "minutes": "0",
                "teacher_script": "Montre un symbole français à ta famille et dis : C’est ..."
            },
            "image_prompts": [
                {"id": "cover_scene", "prompt": "Kid-friendly illustration of the Eiffel Tower, bright colors, no text, no real faces"},
                {"id": "vocab_card_croissant", "prompt": "Simple drawing of a croissant on a plate, no text"},
                {"id": "story_frame", "prompt": "Children looking at a world map with France highlighted, cartoon style"},
                {"id": "phonics_poster", "prompt": "Poster showing the French letters 'ou' with a smiling mouth diagram"},
                {"id": "reward_sticker", "prompt": "Cute gold star sticker with a smiley face, flat design"}
            ],
            "first_tutor_messages": ["Bonjour ! Prêt(e) ? On commence avec un jeu de devinettes !"]
        }
        return _normalize_to_strict_schema(demo)

    # Trim long text defensively
    payload = dict(payload)
    if isinstance(payload.get("pdf_text_excerpt"), str):
        payload["pdf_text_excerpt"] = payload["pdf_text_excerpt"][:12000]

    # Tiny retry with exponential backoff
    last_err = None
    for attempt in range(OPENAI_RETRIES + 1):
        try:
            resp = openai_client.chat.completions.create(
                model=OPENAI_MODEL_TEXT,
                temperature=0.4,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                timeout=OPENAI_TIMEOUT,
            )
            if not resp.choices:
                raise ValueError("No choices returned from model")
            text = (resp.choices[0].message.content or "").strip()
            try:
                raw = json.loads(text)  # ideal: enforced JSON
            except json.JSONDecodeError:
                # Fallback: loose extraction if model accidentally added stray chars
                raw = _extract_json_loose(text)
            return _normalize_to_strict_schema(raw)
        except Exception as e:
            last_err = e
            if attempt < OPENAI_RETRIES:
                time.sleep(0.7 * (2 ** attempt))
            else:
                raise

    # Should never reach (loop returns or raises)
    raise RuntimeError(f"OpenAI call failed: {last_err}")

def build_mimi_lesson(topic: str = "", ocr_text: str = "", image_descriptions: Optional[List[str]] = None, age: int = 11) -> Dict[str, Any]:
    if image_descriptions is None:
        image_descriptions = []
    payload = {
        "topic_hint": topic or "",
        "pdf_text_excerpt": (ocr_text or "")[:12000],
        "image_descriptions": image_descriptions,
        "age": age
    }
    lesson = _chat_json_strict(payload)

    # Ensure materials is always a list for the client
    materials = lesson.get("materials") or []
    if isinstance(materials, str):
        materials = [materials]
    lesson["materials"] = materials

    # Preview snippets for specific activities
    ui_steps: List[Dict[str, str]] = []
    preview_keys = [
        ("warm_up", "Warm-up"),
        ("vocab_cards", "Vocabulary"),
        ("mini_story", "Mini-story"),
        ("phonics_focus", "Phonics"),
        ("practice", "Practice"),
        ("wrap_up", "Wrap-up"),
    ]
    for key, fallback in preview_keys:
        block = lesson.get(key) or {}
        name = block.get("name") or fallback
        script = block.get("teacher_script") or ""
        if name or script:
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
