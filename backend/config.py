import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

@dataclass
class Settings:
    backend_api_token: str
    drive_folder_id: str
    google_application_credentials: str

    python_exe: str
    make_video_py: str
    output_root: str

    host: str
    port: int

    default_lang: str
    default_voice_model: str
    default_region: str


def load_settings() -> Settings:
    load_dotenv(Path(__file__).resolve().parent / ".env")

    backend_api_token = os.getenv("BACKEND_API_TOKEN", "").strip()
    drive_folder_id = os.getenv("DRIVE_FOLDER_ID", "").strip()
    google_application_credentials = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()

    python_exe = os.getenv("PYTHON_EXE", "").strip()
    make_video_py = os.getenv("MAKE_VIDEO_PY", "").strip()
    output_root = os.getenv("OUTPUT_ROOT", "").strip()

    host = os.getenv("HOST", "127.0.0.1").strip()
    port = int(os.getenv("PORT", "8787").strip())

    default_lang = os.getenv("DEFAULT_LANG", "en").strip()
    default_voice_model = os.getenv("DEFAULT_VOICE_MODEL", "en_US-lessac-medium").strip()
    default_region = os.getenv("DEFAULT_REGION", "global").strip()

    missing = []
    for k, v in {
        "BACKEND_API_TOKEN": backend_api_token,
        "DRIVE_FOLDER_ID": drive_folder_id,
        "GOOGLE_APPLICATION_CREDENTIALS": google_application_credentials,
        "PYTHON_EXE": python_exe,
        "MAKE_VIDEO_PY": make_video_py,
        "OUTPUT_ROOT": output_root,
    }.items():
        if not v:
            missing.append(k)

    if missing:
        raise RuntimeError(f"Missing env vars: {', '.join(missing)}")

    return Settings(
        backend_api_token=backend_api_token,
        drive_folder_id=drive_folder_id,
        google_application_credentials=google_application_credentials,
        python_exe=python_exe,
        make_video_py=make_video_py,
        output_root=output_root,
        host=host,
        port=port,
        default_lang=default_lang,
        default_voice_model=default_voice_model,
        default_region=default_region,
    )