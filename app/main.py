# app/main.py
import os
from flask import Flask, jsonify
from flask_cors import CORS

# ---- Flask app ----
app = Flask(__name__, static_folder="static", static_url_path="/")
CORS(app)

# ---- After request hook for headers ----
@app.after_request
def add_headers(response):
    # Set Content-Type with utf-8 for HTML responses
    if response.content_type and 'text/html' in response.content_type:
        response.content_type = 'text/html; charset=utf-8'
    
    # Set Cache-Control for static assets
    response.headers['Cache-Control'] = 'public, max-age=3600'
    
    # Set X-Content-Type-Options for security
    response.headers['X-Content-Type-Options'] = 'nosniff'
    
    return response

# ---- Health ----
@app.get("/health")
def health():
    return jsonify(ok=True, status="healthy")

# ---- Static index ----
@app.get("/")
def index():
    # Serves app/static/index.html
    return app.send_static_file("index.html")

# ---- Blueprints ----
# Sync (build lesson now)
from app.tutor_sync import bp as tutor_sync_bp
app.register_blueprint(tutor_sync_bp)

# Async (enqueue, poll later)
# Removed import of bp from app.tasks, as it does not exist there.

# ---- Error handler (nice JSON) ----
@app.errorhandler(Exception)
def on_error(e):
    # Keep logs visible in server
    app.logger.exception("Unhandled error")
    return jsonify(error=str(e)), 500

# ---- Gunicorn entrypoint ----
# $ gunicorn -w 1 -k gthread --threads 8 --timeout 300 --bind 0.0.0.0:5000 app.main:app
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)

