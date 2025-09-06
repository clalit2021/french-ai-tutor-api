# app/ocr_abbyy.py
import os, time, base64, requests, xmltodict, re

ABBYY_APP_ID       = os.getenv("ABBYY_APP_ID", "")
ABBYY_APP_PASSWORD = os.getenv("ABBYY_APP_PASSWORD", "")
ABBYY_LOCATION     = os.getenv("ABBYY_LOCATION", "cloud-eu")  # e.g. cloud-eu, cloud
OCR_MIN_CONF       = float(os.getenv("OCR_MIN_CONF", "0.85") or 0.85)

# Legacy ABBYY Cloud OCR SDK endpoints are region-scoped subdomains
# Example base: https://cloud-eu.ocrsdk.com
BASE = f"https://{ABBYY_LOCATION}.ocrsdk.com"

def _auth_header() -> dict:
    # Basic auth with appId:password
    token = base64.b64encode(f"{ABBYY_APP_ID}:{ABBYY_APP_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}"}

def _poll_task(task_id: str, timeout=180):
    delay = 3.0
    waited = 0.0
    while waited < timeout:
        r = requests.get(f"{BASE}/v2/getTaskStatus",
                         params={"taskId": task_id},
                         headers=_auth_header(),
                         timeout=(15, 120))
        r.raise_for_status()
        st = r.json()
        status = st.get("status")
        if status in ("Completed", "ProcessingFailed"):
            return st
        time.sleep(delay)
        waited += delay
        delay = min(10.0, delay * 1.2)
    raise TimeoutError("ABBYY polling timed out")

def _avg_conf_from_xml(xml_text: str) -> float:
    try:
        data = xmltodict.parse(xml_text)
        # Grab all confidence="NN" style tokens
        vals = [float(x)/100.0 for x in re.findall(r'confidence="(\d+(?:\.\d+)?)"', xml_text)]
        if not vals: 
            return 1.0  # if ABBYY didn’t return per-token confidences
        return sum(vals)/len(vals)
    except Exception:
        return 1.0

def ocr_file_to_text(file_bytes: bytes, is_pdf: bool, language: str = "French") -> str:
    """
    Sends either a PDF (processDocument) or a single image (processImage) to ABBYY.
    Returns plain text if OK and confidence >= OCR_MIN_CONF; else returns "".
    """
    if not (ABBYY_APP_ID and ABBYY_APP_PASSWORD):
        # No ABBYY configured; skip
        return ""

    try:
        export_form = "txt,xml"
        endpoint = f"{BASE}/v2/processDocument" if is_pdf else f"{BASE}/v2/processImage"
        files = {"file": ("file.pdf" if is_pdf else "image.png", file_bytes)}
        data = {
            "exportFormat": export_form,
            "language": language,
        }
        r = requests.post(endpoint, headers=_auth_header(), files=files, data=data, timeout=(15, 120))
        r.raise_for_status()
        resp = r.json()
        task_id = resp.get("taskId")
        if not task_id:
            return ""

        st = _poll_task(task_id)
        if st.get("status") != "Completed":
            return ""

        # Download result URLs
        res_urls = st.get("resultUrls") or []
        txt_url = next((u for u in res_urls if u.lower().endswith(".txt")), None)
        xml_url = next((u for u in res_urls if u.lower().endswith(".xml")), None)

        txt = ""
        conf = 1.0
        if txt_url:
            t = requests.get(txt_url, timeout=(15, 120))
            t.raise_for_status()
            txt = t.text

        if xml_url:
            x = requests.get(xml_url, timeout=(15, 120))
            x.raise_for_status()
            conf = _avg_conf_from_xml(x.text)

        if conf < OCR_MIN_CONF:
            # Supervisor-style log to stderr (so you can auto-detect)
            import sys, json as _json
            print("__OCR_CONFIDENCE_FAIL__", _json.dumps({"confidence": conf}), file=sys.stderr)
            return ""

        return txt.strip()
    except Exception as e:
        # Soft-fail: return empty so the pipeline continues
        print(f"[OCR][ABBYY][ERROR] {e}")
        return ""
