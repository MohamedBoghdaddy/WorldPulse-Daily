import json
import time
import uuid
import subprocess
from pathlib import Path
from queue import Queue
from threading import Thread
from typing import Any, Dict, Optional, List

from .config import Settings
from . import db
from .drive_client import make_drive_service, upload_file

def _job_id() -> str:
    return f"job_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

def build_command(st: Settings, out_dir: Path, payload: Dict[str, Any]) -> List[str]:
    cmd = [
        st.python_exe,
        st.make_video_py,
        "--jobdir", str(out_dir),
        "--topic", payload["topic"],
        "--duration_sec", str(payload["duration_sec"]),
        "--lang", payload["lang"],
        "--region", payload["region"],
        "--voice_model", payload["voice_model"],
    ]
    kws = payload.get("keywords") or []
    if kws:
        cmd += ["--keywords", *kws]
    return cmd

def read_json_safe(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

class JobWorker:
    def __init__(self, st: Settings):
        self.st = st
        self.q: "Queue[str]" = Queue()
        self.thread: Optional[Thread] = None
        self.drive = make_drive_service(st.google_application_credentials)

    def start(self):
        self.thread = Thread(target=self._run, daemon=True)
        self.thread.start()

    def enqueue(self, job_id: str):
        self.q.put(job_id)

    def create_and_enqueue(self, payload: Dict[str, Any]) -> str:
        jid = _job_id()
        out_dir = Path(self.st.output_root) / jid
        out_dir.mkdir(parents=True, exist_ok=True)

        row = {
            "id": jid,
            "created_at": int(time.time()),
            "updated_at": int(time.time()),
            "status": "queued",
            "topic": payload["topic"],
            "keywords_json": db.encode_keywords(payload.get("keywords") or []),
            "duration_sec": int(payload["duration_sec"]),
            "lang": payload["lang"],
            "voice_model": payload["voice_model"],
            "region": payload["region"],
            "out_dir": str(out_dir),
            "stdout": "",
            "stderr": "",
            "error": "",
            "meta_json": db.encode_json({}),
            "drive_json": db.encode_json({}),
        }
        db.create_job(row)
        self.enqueue(jid)
        return jid

    def _run(self):
        while True:
            job_id = self.q.get()
            try:
                self._process(job_id)
            except Exception as e:
                db.update_job(job_id, {"status": "failed", "error": str(e)})
            finally:
                self.q.task_done()

    def _process(self, job_id: str):
        row = db.get_job(job_id)
        if not row:
            return

        out_dir = Path(row["out_dir"])
        payload = {
            "topic": row["topic"],
            "keywords": json.loads(row["keywords_json"] or "[]"),
            "duration_sec": row["duration_sec"],
            "lang": row["lang"],
            "voice_model": row["voice_model"],
            "region": row["region"],
        }

        db.update_job(job_id, {"status": "running", "error": ""})

        cmd = build_command(self.st, out_dir, payload)
        p = subprocess.run(cmd, capture_output=True, text=True)

        stdout = (p.stdout or "").strip()
        stderr = (p.stderr or "").strip()

        db.update_job(job_id, {"stdout": stdout, "stderr": stderr})

        if p.returncode != 0:
            db.update_job(job_id, {"status": "failed", "error": stderr or stdout or "Generator failed"})
            return

        final_mp4 = out_dir / "final.mp4"
        thumb_png = out_dir / "thumbnail.png"
        meta_json = out_dir / "youtube_meta.json"
        credits_txt = out_dir / "CREDITS.txt"

        if not final_mp4.exists():
            db.update_job(job_id, {"status": "failed", "error": "final.mp4 missing"})
            return

        if final_mp4.stat().st_size < 300_000:
            db.update_job(job_id, {"status": "failed", "error": f"final.mp4 too small ({final_mp4.stat().st_size})"})
            return

        meta = read_json_safe(meta_json) if meta_json.exists() else {}
        db.update_job(job_id, {"status": "uploading", "meta_json": db.encode_json(meta)})

        drive_ids: Dict[str, str] = {}

        drive_ids["final_mp4_file_id"] = upload_file(
            self.drive,
            self.st.drive_folder_id,
            str(final_mp4),
            "video/mp4",
            name=f"{job_id}_final.mp4"
        )

        if thumb_png.exists():
            drive_ids["thumbnail_file_id"] = upload_file(
                self.drive,
                self.st.drive_folder_id,
                str(thumb_png),
                "image/png",
                name=f"{job_id}_thumbnail.png"
            )

        if meta_json.exists():
            drive_ids["meta_file_id"] = upload_file(
                self.drive,
                self.st.drive_folder_id,
                str(meta_json),
                "application/json",
                name=f"{job_id}_youtube_meta.json"
            )

        if credits_txt.exists():
            drive_ids["credits_file_id"] = upload_file(
                self.drive,
                self.st.drive_folder_id,
                str(credits_txt),
                "text/plain",
                name=f"{job_id}_CREDITS.txt"
            )

        db.update_job(job_id, {"status": "done", "drive_json": db.encode_json(drive_ids)})