import pytest
from app.main import app
import app.tasks as tasks
import app.tutor_sync as tutor_sync


@pytest.fixture
def client():
    with app.test_client() as client:
        yield client


def test_api_lessons(client, monkeypatch):
    called = {}

    def fake_delay(*args, **kwargs):
        called['called'] = True

    monkeypatch.setattr(tasks.process_lesson, 'delay', fake_delay)
    monkeypatch.setattr(tasks, 'supabase', None)

    resp = client.post('/api/lessons', json={'child_id': '123', 'file_path': 'bucket/file.pdf'})
    assert resp.status_code == 202
    data = resp.get_json()
    assert data['ok'] is True
    assert called.get('called') is True


def test_api_v2_lesson(client, monkeypatch):
    def fake_build_mimi_lesson(*args, **kwargs):
        return {'title': 'Mock Lesson'}
    monkeypatch.setattr(tutor_sync.mimi, 'build_mimi_lesson', fake_build_mimi_lesson)

    resp = client.post('/api/v2/lesson', json={'topic': 'greetings', 'pdf_text': 'bonjour'})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True
    assert data['lesson']['title'] == 'Mock Lesson'
