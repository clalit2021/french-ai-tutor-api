# app/ocr_abbyy.py
import os, time, base64, requests, xmltodict, re

ABBYY_APP_ID       = os.getenv("ABBYY_APP_ID", "")
ABBYY_APP_PASSWORD = os.getenv("ABBYY_APP_PASSWORD", "")
ABBYY_LOCATION     = os.getenv("ABBYY_LOCATION", "cloud-eu")  # e.g. cloud-eu, cloud
OCR_MIN_CONF       = float(os.getenv("OCR_MIN_CONF", "0.85") or 0.85)

BASE = f"https://{ABBYY_LOCATION}.ocrsdk.com"

def _auth_header() -> dict:
    token = base64.b64encode(f"{ABBYY_APP_ID}:{ABBYY_APP_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}"}

def _as_json(resp: requests.Response) -> dict:
    """Handle ABBYY JSON or XML task payloads gracefully."""
    try:
        return resp.json()
    except ValueError:
        try:
            data = xmltodict.parse(resp.text)
            # Typical XML shape: <response><task id="..." status="..."/></response>
            task = data.get("response", {}).get("task") or {}
            # Normalize to JSON-ish dict
            if isinstance(task, dict):
                task_id = task.get("@id") or task.get("id")
                status = task.get("@status") or task.get("status")
                # resultUrls may be a list or single url in XML
                res = task.get("resultUrls", {}).get("url") if isinstance(task.get("resultUrls"), dict) else None
                if isinstance(res, list):
                    result_urls = res
                elif isinstance(res, str):
                    result_urls = [res]
                else:
                    result_urls = []
                return {"taskId": task_id, "status": status, "resultUrls": result_urls}
        except Exception:
            pass
    raise ValueError("Unexpected ABBYY response format")

def _poll_task(task_id: str, timeout=180):
    delay = 3.0
    waited = 0.0
    while waited < timeout:
        r = requests.get(
            f"{BASE}/v2/getTaskStatus",
            params={"taskId": task_id},
            headers={**_auth_header(), "Accept": "application/json"},
            timeout=(15, 120),
        )
        r.raise_for_status()
        st = _as_json(r)
        status = st.get("status")
        if status in ("Completed", "ProcessingFailed", "NotEnoughCredits"):
            return st
        time.sleep(delay)
        waited += delay
        delay = min(10.0, delay * 1.2)
    raise TimeoutError("ABBYY polling timed out")

def _avg_conf_from_xml(xml_text: str) -> float:
    try:
        # Prefer regex per brief; ABBYY often includes confidence="NN"
        vals = [float(x) / 100.0 for x in re.findall(r'confidence="(\d+(?:\.\d+)?)"', xml_text)]
        if vals:
            return sum(vals) / len(vals)
        # Fallback: try to average words if present in parsed XML
        data = xmltodict.parse(xml_text)
        words = []
        for page in (data.get("document", {}).get("page") or []):
            # normalize to list
            lines = page.get("line") or []
            if isinstance(lines, dict):
                lines = [lines]
            for line in lines:
                ws = line.get("formatting", {}).get("charParams")
                if isinstance(ws, list):
                    words.extend(ws)
        if words:
            cs = []
            for w in words:
                c = w.get("@confidence") or w.get("confidence")
                if c is not None:
                    try: cs.append(float(c) / 100.0)
                    except: pass
            if cs:
                return sum(cs) / len(cs)
    except Exception:
        pass
    return 1.0  # if no confidences, assume OK

def ocr_file_to_text(file_bytes: bytes, is_pdf: bool, language: str = "French") -> str:
    """
    Sends either a PDF (processDocument) or a single image (processImage) to ABBYY.
    Returns plain text if OK and confidence >= OCR_MIN_CONF; else signals supervisor fail (exit 3) or returns "" if not configured.
    """
    if not (ABBYY_APP_ID and ABBYY_APP_PASSWORD):
        # No ABBYY configured; skip so pipeline can continue
        return ""

    try:
        endpoint = f"{BASE}/v2/processDocument" if is_pdf else f"{BASE}/v2/processImage"
        files = {"file": ("file.pdf" if is_pdf else "image.png", file_bytes)}
        data = {
            "exportFormats": "txt,xml",   # <-- plural
            "language": language,
        }
        r = requests.post(
            endpoint,
            headers=_auth_header(),
            files=files,
            data=data,
            timeout=(15, 120),
        )
        r.raise_for_status()
        resp = _as_json(r)
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
            import sys, json as _json
            print("__OCR_CONFIDENCE_FAIL__", _json.dumps({"confidence": conf}), file=sys.stderr)
            sys.exit(3)  # <-- per brief: let supervisor catch & auto-fix

        return txt.strip()
    except SystemExit:
        raise
    except Exception as e:
        # Soft-fail: return empty so the pipeline continues
        print(f"[OCR][ABBYY][ERROR] {e}")
        return ""
