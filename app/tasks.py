# app/tasks.py
"""
Minimal async lesson pipeline:
- POST /api/lessons          -> returns {lesson_id, status:"queued"}
- GET  /api/lessons/<id>     -> returns {status, lesson?}

This version uses an in-memory queue/thread to simulate async work.
You can later swap the worker to Celery, RQ, or your OCR/ABBYY pipeline.
"""

import json
import time
import uuid
import threading
from typing import Dict, Any

from flask import Blueprint, request, jsonify
from app import mimi

bp = Blueprint("tasks", __name__)

# ---- In-memory job store (simple & volatile) ----
_JOBS: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()

def _set_job(job_id: str, **fields):
    with _LOCK:
        _JOBS.setdefault(job_id, {})
        _JOBS[job_id].update(fields)

def _get_job(job_id: str) -> Dict[str, Any] | None:
    with _LOCK:
        return _JOBS.get(job_id)

# ---- Worker ----
def _worker_build_lesson(job_id: str, child_id: str, file_path: str):
    try:
        # Mark as processing
        _set_job(job_id, status="processing")
        print(f"[ASYNC] start job={job_id} child={child_id} file_path={file_path}")

        # TODO: Here is where you'd:
        #  1) fetch the Supabase file (PDF/image),
        #  2) run OCR (PyMuPDF first, fallback ABBYY),
        #  3) extract image descriptions,
        #  4) pass signals into mimi.build_mimi_lesson(...)
        #
        # For now, we keep it simple but useful:
        fake_ocr_excerpt = f"Texte OCR simulé extrait de: {file_path}"
        image_desc = []  # You can stitch your own later.

        # Build the lesson using your existing orchestrator
        lesson = mimi.build_mimi_lesson(
            topic="Leçon depuis fichier",
            ocr_text=fake_ocr_excerpt,
            image_descriptions=image_desc,
            age=11,
        )

        # Simulate some processing latency
        time.sleep(1.0)

        _set_job(job_id, status="ready", lesson=lesson)
        print(f"[ASYNC] done job={job_id} status=ready")
    except Exception as e:
        print(f"[ASYNC][ERROR] job={job_id} {repr(e)}")
        _set_job(job_id, status="failed", error=str(e))

# ---- Routes ----
@bp.route("/api/lessons", methods=["POST"])
def create_lesson_job():
    """
    Body:
      { "child_id": "uuid-or-any", "file_path": "uploads/xxx.pdf|.png" }
    """
    body = request.get_json(silent=True, force=True) or {}
    child_id = (body.get("child_id") or "").strip()
    file_path = (body.get("file_path") or "").strip()

    if not file_path:
        return jsonify(error="file_path is required (e.g., 'uploads/book.pdf')"), 400

    lesson_id = str(uuid.uuid4())
    _set_job(lesson_id, status="queued", child_id=child_id, file_path=file_path)

    # Fire worker thread
    th = threading.Thread(
        target=_worker_build_lesson,
        args=(lesson_id, child_id, file_path),
        daemon=True,
    )
    th.start()

    print(f"[ASYNC] queued lesson_id={lesson_id} file_path={file_path}")
    return jsonify(lesson_id=lesson_id, status="queued")

@bp.route("/api/lessons/<lesson_id>", methods=["GET"])
def get_lesson_job(lesson_id: str):
    job = _get_job(lesson_id)
    if not job:
        return jsonify(error="lesson_id not found"), 404

    # Only include lesson if ready
    res = {
        "lesson_id": lesson_id,
        "status": job.get("status"),
    }
    if job.get("status") == "ready":
        res["lesson"] = job.get("lesson", {})

    if job.get("status") == "failed":
        res["error"] = job.get("error")

    return jsonify(res)
