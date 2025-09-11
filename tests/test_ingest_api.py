import os
import sys
import io
import pytest
from unittest.mock import patch, MagicMock

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from app.main import app


@pytest.fixture
def client():
    """Create test client."""
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


@patch('app.ingest_api.supabase')
@patch('app.ingest.extract_text_from_pdf_bytes')
@patch('app.ingest.upsert_lesson_chunks')
def test_api_ingest_success(mock_upsert, mock_extract, mock_supabase, client):
    """Test successful /api/ingest endpoint."""
    # Mock text extraction
    mock_extract.return_value = "This is extracted text from the PDF."
    
    # Mock Supabase update
    mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value = None
    
    # Create fake PDF file
    pdf_content = b"fake pdf bytes"
    
    response = client.post('/api/ingest', 
                          data={
                              'lesson_id': 'test-lesson-123',
                              'file': (io.BytesIO(pdf_content), 'test.pdf')
                          },
                          content_type='multipart/form-data')
    
    assert response.status_code == 200
    data = response.get_json()
    assert data['ok'] is True
    assert data['chunks'] > 0
    
    # Verify Supabase was called to update lesson
    mock_supabase.table.assert_called_with("lessons")
    update_call = mock_supabase.table.return_value.update
    update_call.assert_called_once()
    update_args = update_call.call_args[0][0]
    assert update_args['ocr_text'] == "This is extracted text from the PDF."
    assert update_args['status'] == 'ingested'


@patch('app.ingest_api.supabase')
@patch('app.ingest.extract_text_from_pdf_bytes')
@patch('app.ingest.upsert_lesson_chunks')
def test_api_ingest_no_text(mock_upsert, mock_extract, mock_supabase, client):
    """Test /api/ingest endpoint when no text is extracted."""
    # Mock no text extraction
    mock_extract.return_value = ""
    
    # Mock Supabase update
    mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value = None
    
    # Create fake PDF file
    pdf_content = b"fake pdf bytes"
    
    response = client.post('/api/ingest', 
                          data={
                              'lesson_id': 'test-lesson-456',
                              'file': (io.BytesIO(pdf_content), 'test.pdf')
                          },
                          content_type='multipart/form-data')
    
    assert response.status_code == 200
    data = response.get_json()
    assert data['ok'] is False
    assert data['chunks'] == 0
    
    # Verify Supabase was called with error status
    mock_supabase.table.assert_called_with("lessons")
    update_call = mock_supabase.table.return_value.update
    update_call.assert_called_once()
    update_args = update_call.call_args[0][0]
    assert update_args['ocr_text'] is None
    assert update_args['status'] == 'error'


def test_api_ingest_missing_params(client):
    """Test /api/ingest endpoint with missing parameters."""
    # Missing file
    response = client.post('/api/ingest', data={'lesson_id': 'test-123'})
    assert response.status_code == 400
    data = response.get_json()
    assert 'error' in data
    
    # Missing lesson_id
    pdf_content = b"fake pdf bytes"
    response = client.post('/api/ingest', 
                          data={'file': (io.BytesIO(pdf_content), 'test.pdf')},
                          content_type='multipart/form-data')
    assert response.status_code == 400
    data = response.get_json()
    assert 'error' in data