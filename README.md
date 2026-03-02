``
# WorldPulse-Daily

WorldPulse-Daily is an automated pipeline for generating daily AI videos, storing outputs in Google Drive and letting n8n Cloud orchestrate scheduling, retries and publishing.

The repo is split into two main parts:

- `ai_youtube_factory/`: the generation and rendering layer (scripts, voices, topics, outputs)
- `backend/`: a lightweight web backend (dashboard style UI plus worker utilities)

## Repository structure

``

WORLDPUlse-DAILY/
ai_youtube_factory/
out/
demo_en/
prod_en_50s/
demo_script.json
smoke_test_en.mp4
test_en.wav
topics/
topics.json
voices/
en_US-lessac-medium.onnx
en_US-lessac-medium.onnx.json
.env
make_video.py
runner_api.py

backend/
static/
app.js
style.css
templates/
index.html
.env
app.py
config.py
db.py
drive_client.py
requirements.txt
worker.py

.gitignore
client_secret_*.json
en_US-lessac-medium.onnx
en_US-lessac-medium.onnx.json
LICENSE
runner_start.ps1

``

## How the pieces fit

### ai_youtube_factory

- `topics/topics.json`: your topic list or queue input
- `voices/`: local TTS voice model assets
- `make_video.py`: the video build script (script to audio to video)
- `runner_api.py`: an API entrypoint so n8n can trigger generation remotely
- `out/`: generated artifacts (mp4, wav, json)

### backend

- `app.py`: web backend entrypoint (serves `templates/` and `static/`)
- `drive_client.py`: Google Drive upload and link handling
- `db.py`: persistence for runs, jobs or logs (based on your implementation)
- `worker.py`: background execution support for long running jobs
- `requirements.txt`: Python dependencies for the backend

## Quick start (local)

### 1) Create and activate a virtual environment

Windows PowerShell:

``
python -m venv .venv
.\.venv\Scripts\Activate.ps1
``

### 2) Install dependencies

If your backend requirements cover everything:

```
pip install -r backend/requirements.txt
```

If you later add a separate requirements file for `ai_youtube_factory`, install that too.

### 3) Configure environment files

You have two `.env` files:

* `ai_youtube_factory/.env`
* `backend/.env`

At minimum, set your Google Drive credentials in the place your code expects. Common values:

* Google OAuth client values (client id, client secret, refresh token)
* Drive folder id for uploads
* Any API keys used for script generation, TTS or media assets

Do not commit secrets. Also keep `client_secret_*.json` out of git unless you intentionally want it public.

### 4) Run the backend UI

Typical pattern:

```
python backend/app.py
```

Then open the local URL printed in the terminal.

### 5) Run the runner API

Because `runner_api.py` is inside `ai_youtube_factory`, the common uvicorn command is:

```bash
uvicorn ai_youtube_factory.runner_api:app --host 127.0.0.1 --port 8787
```

If you previously used `uvicorn runner_api:app` and it failed, this module path is usually the fix when the file is not in the repo root.

If you already have `runner_start.ps1`, you can use it as your canonical startup method.

## Using it with n8n Cloud

n8n Cloud is best as the orchestrator, not the renderer. A typical flow:

1. Cron trigger (daily)
2. Pick topic (from `topics.json` or an external queue)
3. HTTP Request to `runner_api` to start a job
4. Poll job status until success or failure
5. Read output link or file id from the runner response
6. Continue in n8n (Drive, YouTube, notifications)

This split avoids n8n Cloud limitations around file access and long running media tasks.

## Outputs

Generated files land under `ai_youtube_factory/out/`. Commit only what you want as examples. Treat large media as build artifacts, not source.

## License

Apache-2.0. See `LICENSE`.

```
