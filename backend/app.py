import json
from pathlib import Path
from typing import Any, Dict, Optional, List

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import load_settings, Settings
from .worker import JobWorker
from . import db

app = FastAPI()

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

st: Settings = load_settings()
worker = JobWorker(st)

def require_token(auth: Optional[str]):
    if not auth:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Use Bearer token")
    token = auth.split(" ", 1)[1].strip()
    if token != st.backend_api_token:
        raise HTTPException(status_code=403, detail="Forbidden")

@app.on_event("startup")
def on_startup():
    db.init_db()
    worker.start()

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/jobs")
def api_list_jobs(authorization: Optional[str] = Header(default=None), limit: int = 50):
    require_token(authorization)
    rows = db.list_jobs(limit=limit)
    return [db.decode_job(r) for r in rows]

@app.get("/api/jobs/{job_id}")
def api_get_job(job_id: str, authorization: Optional[str] = Header(default=None)):
    require_token(authorization)
    row = db.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return db.decode_job(row)

@app.post("/api/jobs")
async def api_create_job(payload: Dict[str, Any], authorization: Optional[str] = Header(default=None)):
    require_token(authorization)

    topic = str(payload.get("topic", "")).strip()
    if not topic:
        raise HTTPException(status_code=400, detail="topic is required")

    keywords = payload.get("keywords") or []
    if not isinstance(keywords, list):
        keywords = []

    duration_sec = int(payload.get("duration_sec") or 60)
    lang = str(payload.get("lang") or st.default_lang).strip()
    voice_model = str(payload.get("voice_model") or st.default_voice_model).strip()
    region = str(payload.get("region") or st.default_region).strip()

    job_id = worker.create_and_enqueue({
        "topic": topic,
        "keywords": [str(x) for x in keywords],
        "duration_sec": duration_sec,
        "lang": lang,
        "voice_model": voice_model,
        "region": region,
    })
    return {"ok": True, "job_id": job_id, "status": "queued"}

@app.post("/api/jobs/{job_id}/retry")
def api_retry(job_id: str, authorization: Optional[str] = Header(default=None)):
    require_token(authorization)
    row = db.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    db.update_job(job_id, {"status": "queued", "error": ""})
    worker.enqueue(job_id)
    return {"ok": True}