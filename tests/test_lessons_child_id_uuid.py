import uuid
from flask import Flask

import app.tasks as tasks


def create_app():
    app = Flask(__name__)
    app.register_blueprint(tasks.bp)
    tasks.supabase = None
    return app


def test_api_lessons_rejects_non_uuid(monkeypatch):
    app = create_app()
    client = app.test_client()

    # Stub Celery delay
    monkeypatch.setattr(tasks.process_lesson, "delay", lambda *args, **kwargs: None)

    res = client.post("/api/lessons", json={"child_id": "not-uuid", "file_path": "f.pdf"})
    assert res.status_code == 400
    assert "UUID" in res.get_json()["error"]


def test_api_lessons_accepts_uuid(monkeypatch):
    app = create_app()
    client = app.test_client()

    # Stub Celery delay
    monkeypatch.setattr(tasks.process_lesson, "delay", lambda *args, **kwargs: None)

    child_id = str(uuid.uuid4())
    res = client.post("/api/lessons", json={"child_id": child_id, "file_path": "f.pdf"})
    assert res.status_code == 202
    data = res.get_json()
    assert data["ok"] is True
    assert data["lesson_id"]
