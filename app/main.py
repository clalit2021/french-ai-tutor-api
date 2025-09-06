# app/main.py
import os
from flask import Flask, jsonify
from flask_cors import CORS

# ---- Flask app ----
app = Flask(__name__, static_folder="static", static_url_path="/")
CORS(app)

# ---- Health ----
@app.get("/health")
def health():
    return jsonify(ok=True, status="healthy")

# ---- Root welcome route ----
@app.get("/")
def index():
    return jsonify(
        message="Welcome to French AI Tutor API",
        description="An AI-powered French language learning platform",
        status="online"
    )

# ---- Blueprints ----
# Sync (build lesson now)
from app.tutor_sync import bp as tutor_sync_bp
app.register_blueprint(tutor_sync_bp)

# Async (enqueue, poll later)
from app.tasks import bp as tasks_bp
app.register_blueprint(tasks_bp)

# ---- Error handlers ----
@app.errorhandler(404)
def not_found(error):
    return jsonify(
        error="Not Found",
        message="The requested resource was not found on this server",
        status_code=404
    ), 404

@app.errorhandler(500)
def internal_error(error):
    # Keep logs visible in server
    app.logger.exception("Internal server error")
    return jsonify(
        error="Internal Server Error",
        message="An unexpected error occurred on the server",
        status_code=500
    ), 500

# ---- Generic error handler for other exceptions ----
@app.errorhandler(Exception)
def on_error(e):
    # Keep logs visible in server
    app.logger.exception("Unhandled error")
    return jsonify(
        error="Internal Server Error",
        message="An unexpected error occurred on the server",
        status_code=500
    ), 500

# ---- Gunicorn entrypoint ----
# $ gunicorn -w 1 -k gthread --threads 8 --timeout 300 --bind 0.0.0.0:5000 app.main:app
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)

