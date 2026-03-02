from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from pathlib import Path
import subprocess
import time
import json
import os

app = FastAPI()

# Paths
BASE = Path(r"C:\Users\Moham\Documents\GitHub\WorldPulse-Daily\ai_youtube_factory")
PY = Path(r"C:\Users\Moham\Documents\GitHub\WorldPulse-Daily\.venv\Scripts\python.exe")
SCRIPT = BASE / "make_video.py"
OUTROOT = BASE / "out"

# Security
RUNNER_TOKEN = os.getenv("RUNNER_TOKEN", "change-me-now")

class GenerateReq(BaseModel):
    topic: str
    keywords: list[str] = []
    duration_sec: int = 60
    lang: str = "en"
    voice_model: str = "en_US-lessac-medium"
    region: str = "global"

def require_token(x_runner_token: str | None):
    if not x_runner_token or x_runner_token != RUNNER_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/generate")
def generate(req: GenerateReq, x_runner_token: str | None = Header(default=None)):
    require_token(x_runner_token)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    job_id = f"job_{stamp}"
    outdir = OUTROOT / job_id
    outdir.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(PY),
        str(SCRIPT),
        "--jobdir", str(outdir),
        "--topic", req.topic,
        "--duration_sec", str(req.duration_sec),
        "--lang", req.lang,
        "--region", req.region,
        "--voice_model", req.voice_model,
    ]

    # add keywords safely
    if req.keywords:
        cmd += ["--keywords", *req.keywords]

    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "job_id": job_id, "error": (p.stderr or p.stdout or "unknown error")}
        )

    video_path = outdir / "final.mp4"
    thumb_path = outdir / "thumbnail.png"
    meta_path = outdir / "youtube_meta.json"
    credits_path = outdir / "CREDITS.txt"

    if not video_path.exists():
        return JSONResponse(status_code=500, content={"ok": False, "job_id": job_id, "error": "final.mp4 missing"})

    meta = {"title": req.topic, "description": "", "tags": []}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    return {
        "ok": True,
        "job_id": job_id,
        "outdir": str(outdir),
        "title": meta.get("title", req.topic),
        "description": meta.get("description", ""),
        "tags": meta.get("tags", []),
        "video_url": f"/job/{job_id}/video",
        "thumb_url": f"/job/{job_id}/thumb",
        "meta_url": f"/job/{job_id}/meta",
        "credits_url": f"/job/{job_id}/credits",
    }

@app.get("/job/{job_id}/video")
def get_video(job_id: str, x_runner_token: str | None = Header(default=None)):
    require_token(x_runner_token)
    path = OUTROOT / job_id / "final.mp4"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, media_type="video/mp4", filename="final.mp4")

@app.get("/job/{job_id}/thumb")
def get_thumb(job_id: str, x_runner_token: str | None = Header(default=None)):
    require_token(x_runner_token)
    path = OUTROOT / job_id / "thumbnail.png"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, media_type="image/png", filename="thumbnail.png")

@app.get("/job/{job_id}/meta")
def get_meta(job_id: str, x_runner_token: str | None = Header(default=None)):
    require_token(x_runner_token)
    path = OUTROOT / job_id / "youtube_meta.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return JSONResponse(content=json.loads(path.read_text(encoding="utf-8")))

@app.get("/job/{job_id}/credits")
def get_credits(job_id: str, x_runner_token: str | None = Header(default=None)):
    require_token(x_runner_token)
    path = OUTROOT / job_id / "CREDITS.txt"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, media_type="text/plain", filename="CREDITS.txt")