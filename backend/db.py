import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_PATH = Path(__file__).resolve().parent / "jobs.sqlite3"

def _now() -> int:
    return int(time.time())

def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db() -> None:
    con = connect()
    cur = con.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL,
          status TEXT NOT NULL,
          topic TEXT NOT NULL,
          keywords_json TEXT NOT NULL,
          duration_sec INTEGER NOT NULL,
          lang TEXT NOT NULL,
          voice_model TEXT NOT NULL,
          region TEXT NOT NULL,
          out_dir TEXT NOT NULL,
          stdout TEXT NOT NULL,
          stderr TEXT NOT NULL,
          error TEXT NOT NULL,
          meta_json TEXT NOT NULL,
          drive_json TEXT NOT NULL
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);")
    con.commit()
    con.close()

def create_job(row: Dict[str, Any]) -> None:
    con = connect()
    cur = con.cursor()
    cur.execute(
        """
        INSERT INTO jobs
        (id, created_at, updated_at, status, topic, keywords_json, duration_sec, lang, voice_model, region,
         out_dir, stdout, stderr, error, meta_json, drive_json)
        VALUES
        (:id, :created_at, :updated_at, :status, :topic, :keywords_json, :duration_sec, :lang, :voice_model, :region,
         :out_dir, :stdout, :stderr, :error, :meta_json, :drive_json)
        """,
        row,
    )
    con.commit()
    con.close()

def update_job(job_id: str, patch: Dict[str, Any]) -> None:
    patch = dict(patch)
    patch["updated_at"] = _now()
    sets = ", ".join([f"{k}=:{k}" for k in patch.keys()])
    patch["id"] = job_id

    con = connect()
    cur = con.cursor()
    cur.execute(f"UPDATE jobs SET {sets} WHERE id=:id", patch)
    con.commit()
    con.close()

def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    con = connect()
    cur = con.cursor()
    cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    r = cur.fetchone()
    con.close()
    if not r:
        return None
    return dict(r)

def list_jobs(limit: int = 50) -> List[Dict[str, Any]]:
    con = connect()
    cur = con.cursor()
    cur.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))
    rows = cur.fetchall()
    con.close()
    return [dict(r) for r in rows]

def decode_job(row: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(row)
    for k in ("keywords_json", "meta_json", "drive_json"):
        try:
            row[k] = json.loads(row[k] or "{}")
        except Exception:
            row[k] = {}
    return row

def encode_keywords(keywords: List[str]) -> str:
    return json.dumps(keywords, ensure_ascii=False)

def encode_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)