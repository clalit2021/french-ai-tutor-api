# app/main.py
import os
from flask import Flask, jsonify
from flask_cors import CORS
from werkzeug.exceptions import HTTPException

# ---- Flask app ----
# Keep static at root so /styles.css works (static_url_path="")
app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

# ---- Blueprints ----
# Register BOTH blueprints so /api/... endpoints exist
try:
    from app.tutor_sync import bp as tutor_sync_bp
    app.register_blueprint(tutor_sync_bp)
except Exception as e:
    print("[BOOT] tutor_sync blueprint not loaded:", repr(e))

try:
    from app.tasks import bp as tasks_bp
    app.register_blueprint(tasks_bp)
except Exception as e:
    print("[BOOT] tasks blueprint not loaded:", repr(e))

# ---- Health ----
@app.get("/health")
def health():
    return jsonify(ok=True, status="healthy")

# ---- Root: serve the SPA entry ----
@app.get("/")
def index():
    return app.send_static_file("index.html")

# ⚠️ IMPORTANT:
# Do NOT add a catch-all `/<path:filename>` static route – it will swallow /api/* and cause 405 on POSTs.

# ---- Error handlers ----
@app.errorhandler(404)
def not_found(error):
    return jsonify(error="Not Found", message="The requested resource was not found", status=404), 404

@app.errorhandler(Exception)
def on_error(e):
    # Preserve framework HTTP codes (405, 400, etc.)
    if isinstance(e, HTTPException):
        return jsonify(error=e.name, message=e.description, status=e.code), e.code
    app.logger.exception("Unhandled error")
    return jsonify(error=str(e)), 500

# ---- Gunicorn entrypoint ----
# $ gunicorn -w 1 -k gthread --threads 8 --timeout 300 --bind 0.0.0.0:5000 app.main:app
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
