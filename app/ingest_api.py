from flask import Blueprint, request, jsonify
from .ingest import ingest_pdf_to_vectors
from supabase import create_client, Client
import os

bp = Blueprint("api", __name__)

# ---- Supabase ----
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
supabase = None
if SUPABASE_URL and SUPABASE_SERVICE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    except Exception as e:
        print("[SUPABASE] ingest client init failed:", repr(e))

@bp.post("/api/ingest")
def api_ingest():
    file = request.files.get("file")
    lesson_id = request.form.get("lesson_id") or request.args.get("lesson_id")
    if not file or not lesson_id:
        return jsonify(error="file and lesson_id required"), 400

    pdf_bytes = file.read()
    print(f"[INGEST] start lesson_id={lesson_id} size={len(pdf_bytes)}")
    res = ingest_pdf_to_vectors(pdf_bytes, lesson_id)
    ok = bool(res.get("text"))

    # ✅ update Supabase *before* any lesson generation
    if supabase:
        supabase.table("lessons").update({
            "ocr_text": res.get("text") or None,
            "status": "ingested" if ok else "error"
        }).eq("id", lesson_id).execute()

    return jsonify(ok=ok, chunks=res.get("chunks", 0))