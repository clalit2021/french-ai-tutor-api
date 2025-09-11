import os, io, fitz, re
from . import ocr_abbyy
from .vector_store import upsert_lesson_chunks

def _is_mostly_image(page: fitz.Page) -> bool:
    try:
        txt = page.get_text("text") or ""
        letters = len(re.sub(r"\s+", "", txt))
        images = len(page.get_images(full=True))
        return letters < 80 or images >= 1  # tiny text or has images ⇒ OCR
    except Exception:
        return True

def extract_text_from_pdf_bytes(pdf_bytes: bytes, language="French") -> str:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    parts = []
    for i, page in enumerate(doc):
        raw = page.get_text("text") or ""
        if _is_mostly_image(page) or len(raw.strip()) < 60:
            # fallback OCR per page render
            pix = page.get_pixmap(dpi=220)
            img_bytes = pix.tobytes("png")
            ocr_txt = ocr_abbyy.ocr_file_to_text(img_bytes, is_pdf=False, language=language)
            parts.append(ocr_txt.strip())
            print(f"[OCR] page={i+1} via ABBYY len={len(ocr_txt)}")
        else:
            parts.append(raw.strip())
            print(f"[INGEST] page={i+1} via PyMuPDF len={len(raw)}")
    return "\n\n".join(p for p in parts if p)

def chunk_text(s: str, min_len=500, max_len=900):
    # sentence-aware chunking, keeps boundaries
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    sentences = re.split(r'(?<=[\.\?\!\:])\s+', s)
    buf, cur = [], ""
    for sent in sentences:
        if len(cur) + len(sent) < max_len:
            cur = (cur + " " + sent).strip()
        else:
            if len(cur) >= min_len: buf.append(cur); cur = sent
            else: cur = (cur + " " + sent).strip()
    if cur: buf.append(cur)
    return [c.strip() for c in buf if c.strip()]

def ingest_pdf_to_vectors(pdf_bytes: bytes, lesson_id: str):
    text = extract_text_from_pdf_bytes(pdf_bytes)
    if not text:
        print("__OCR_CONFIDENCE_FAIL__ {\"reason\":\"no_text_after_ocr\"}")
        return {"text": "", "chunks": 0}
    chunks = chunk_text(text)
    upsert_lesson_chunks(lesson_id, chunks)  # writes to content.db
    print(f"[INGEST] chunks={len(chunks)} embeds={len(chunks)}")
    return {"text": text, "chunks": len(chunks)}