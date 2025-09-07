# app/main.py
import os
import uuid
import tempfile
from typing import List, Dict, Any

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

# ---- Flask app ----
# Static files live in app/static; serve index.html at "/"
app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

# ---- Optional deps from our modules (graceful if missing) ----
# Expect these files per the brief:
#   app/ingest.py  -> ingest_paths(paths: List[str]) -> Dict
#   app/tutor.py   -> chat_once(message: str, mode: str = "teach") -> Dict
try:
    from .ingest import ingest_paths  # type: ignore
except Exception as e:
    ingest_paths = None  # fallback below
    print("[BOOT] ingest.py not ready:", repr(e))

try:
    from .tutor import chat_once  # type: ignore
except Exception as e:
    chat_once = None  # fallback below
    print("[BOOT] tutor.py not ready:", repr(e))


# ---- Health ----
@app.get("/health")
def health():
    return jsonify(ok=True, status="healthy")


# ---- Root: serve SPA ----
@app.get("/")
def index():
    # Serve app/static/index.html
    return app.send_static_file("index.html")


@app.get("/<path:filename>")
def static_files(filename: str):
    # Allow direct access to other static assets
    return send_from_directory(app.static_folder, filename)


# ---- /api/ingest : PDF/images/zip -> extract text (+ ABBYY fallback in ingest.py) ----
@app.post("/api/ingest")
def api_ingest():
    """
    Accepts one or many files under the 'file' or 'files' field.
    Saves to temp, hands paths to ingest.ingest_paths which:
      - extracts/ocr text
      - chunks
      - embeds & writes to SQLite (content.db)
    Returns counts & simple summary.
    """
    if ingest_paths is None:
        return jsonify(
            ok=False,
            error="ingest.py missing or failed to import. Ensure app/ingest.py defines ingest_paths(paths)->dict."
        ), 500

    uploaded = []
    # Support both single and multiple file inputs
    if "files" in request.files:
        uploaded = request.files.getlist("files")
    elif "file" in request.files:
        uploaded = [request.files["file"]]

    if not uploaded:
        return jsonify(ok=False, error="No files uploaded. Use 'file' or 'files'."), 400

    tmp_paths: List[str] = []
    try:
        for f in uploaded:
            ext = os.path.splitext(f.filename or "")[1] or ""
            safe_name = f"{uuid.uuid4().hex}{ext}"
            tmp_dir = tempfile.mkdtemp(prefix="ingest_")
            tmp_path = os.path.join(tmp_dir, safe_name)
            f.save(tmp_path)
            tmp_paths.append(tmp_path)

        print(f"[INGEST] received={len(tmp_paths)} paths")
        result: Dict[str, Any] = ingest_paths(tmp_paths)  # user-implemented
        # Expected keys (recommendation): chunks, embeds, pages, sources
        print(f"[INGEST] chunks={result.get('chunks')} embeds={result.get('embeds')} pages={result.get('pages')}")
        return jsonify(ok=True, **result)

    except SystemExit as se:
        # Allow ABBYY confidence guard (exit code 3) to bubble as a clear API error
        code = int(getattr(se, "code", 1) or 1)
        print(f"[OCR] subprocess exit {code}")
        return jsonify(ok=False, error="OCR confidence failed. See server logs."), 502
    except Exception as e:
        app.logger.exception("[INGEST] Unhandled error")
        return jsonify(ok=False, error=str(e)), 500
    finally:
        # Do not delete temp files immediately if your ingest pipeline reads them later.
        # If ingest_paths already copies/reads and closes, you can clean up here.
        pass


# ---- /api/chat : Retrieval + LLM tutoring ----
@app.post("/api/chat")
def api_chat():
    """
    JSON body: { "message": str, "mode": "teach|practice|quiz" }
    Delegates to tutor.chat_once which should:
      - run retrieval (vector_store.py over content.db)
      - craft system/persona (prompts.py)
      - call OpenAI (gpt-4o-mini) and return:
        { "text": str, "suggested_images": [...], "voice": {"speed":"slow|fast"} }
    """
    if chat_once is None:
        return jsonify(
            ok=False,
            error="tutor.py missing or failed to import. Ensure app/tutor.py defines chat_once(message, mode)->dict."
        ), 500

    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    mode = (data.get("mode") or "teach").strip()

    if not message:
        return jsonify(ok=False, error="Missing 'message'."), 400

    try:
        print(f"[RAG] mode={mode!r}")
        out: Dict[str, Any] = chat_once(message=message, mode=mode)  # user-implemented
        text = out.get("text", "")
        print(f"[CHAT] chars={len(text)}")
        return jsonify(ok=True, **out)
    except Exception as e:
        app.logger.exception("[CHAT] Unhandled error")
        return jsonify(ok=False, error=str(e)), 500


# ---- Error handlers (nice JSON) ----
@app.errorhandler(404)
def not_found(error):
    return jsonify(error="Not Found", message="The requested resource was not found", status=404), 404


@app.errorhandler(Exception)
def on_error(e):
    app.logger.exception("Unhandled error")
    return jsonify(error=str(e)), 500


# ---- Gunicorn entrypoint ----
# $ gunicorn -w 1 -k gthread --threads 8 --timeout 300 --bind 0.0.0.0:5000 app.main:app
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
