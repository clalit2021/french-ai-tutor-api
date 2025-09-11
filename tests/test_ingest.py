import os
import sys
import io
import pytest
from unittest.mock import patch, MagicMock

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from app.ingest import chunk_text, extract_text_from_pdf_bytes, ingest_pdf_to_vectors


def test_chunk_text():
    """Test that text chunking works correctly."""
    text = "This is a sentence. This is another sentence! And here is a third sentence? Finally, the last sentence."
    chunks = chunk_text(text, min_len=50, max_len=100)
    assert len(chunks) > 0
    for chunk in chunks:
        assert len(chunk) <= 100
        assert chunk.strip() == chunk  # no leading/trailing whitespace


def test_chunk_text_empty():
    """Test that empty text returns empty chunks."""
    chunks = chunk_text("")
    assert chunks == []


@patch('app.ingest.fitz')
@patch('app.ingest.ocr_abbyy')
def test_extract_text_from_pdf_bytes_text_extraction(mock_ocr, mock_fitz):
    """Test PDF text extraction when text is available."""
    # Mock PyMuPDF to return readable text
    mock_doc = MagicMock()
    mock_page = MagicMock()
    mock_page.get_text.return_value = "This is readable text from the PDF. " * 10  # Long enough text
    mock_page.get_images.return_value = []  # No images
    mock_doc.__iter__ = MagicMock(return_value=iter([mock_page]))
    mock_fitz.open.return_value = mock_doc
    
    pdf_bytes = b"fake pdf content"
    result = extract_text_from_pdf_bytes(pdf_bytes)
    
    assert "This is readable text from the PDF." in result
    mock_fitz.open.assert_called_once_with(stream=pdf_bytes, filetype="pdf")


@patch('app.ingest.fitz')
@patch('app.ingest.ocr_abbyy')
def test_extract_text_from_pdf_bytes_ocr_fallback(mock_ocr, mock_fitz):
    """Test PDF text extraction falls back to OCR for image-heavy pages."""
    # Mock PyMuPDF to return minimal text (triggering OCR)
    mock_doc = MagicMock()
    mock_page = MagicMock()
    mock_page.get_text.return_value = "a"  # Very short text
    mock_page.get_images.return_value = [{"dummy": "image"}]  # Has images
    mock_pixmap = MagicMock()
    mock_pixmap.tobytes.return_value = b"fake image bytes"
    mock_page.get_pixmap.return_value = mock_pixmap
    mock_doc.__iter__ = MagicMock(return_value=iter([mock_page]))
    mock_fitz.open.return_value = mock_doc
    
    # Mock OCR to return text
    mock_ocr.ocr_file_to_text.return_value = "OCR extracted text from image"
    
    pdf_bytes = b"fake pdf content"
    result = extract_text_from_pdf_bytes(pdf_bytes)
    
    assert "OCR extracted text from image" in result
    mock_ocr.ocr_file_to_text.assert_called_once()


@patch('app.ingest.upsert_lesson_chunks')
@patch('app.ingest.extract_text_from_pdf_bytes')
def test_ingest_pdf_to_vectors_success(mock_extract, mock_upsert):
    """Test successful PDF ingestion."""
    mock_extract.return_value = "This is extracted text. " * 100  # Long text for chunking
    
    pdf_bytes = b"fake pdf content"
    lesson_id = "test-lesson-123"
    
    result = ingest_pdf_to_vectors(pdf_bytes, lesson_id)
    
    assert result["text"] == mock_extract.return_value
    assert result["chunks"] > 0
    mock_extract.assert_called_once_with(pdf_bytes)
    mock_upsert.assert_called_once()


@patch('app.ingest.upsert_lesson_chunks')
@patch('app.ingest.extract_text_from_pdf_bytes')
def test_ingest_pdf_to_vectors_no_text(mock_extract, mock_upsert):
    """Test PDF ingestion when no text is extracted."""
    mock_extract.return_value = ""  # No text extracted
    
    pdf_bytes = b"fake pdf content"
    lesson_id = "test-lesson-123"
    
    result = ingest_pdf_to_vectors(pdf_bytes, lesson_id)
    
    assert result["text"] == ""
    assert result["chunks"] == 0
    mock_extract.assert_called_once_with(pdf_bytes)
    mock_upsert.assert_not_called()  # Should not try to upsert empty content