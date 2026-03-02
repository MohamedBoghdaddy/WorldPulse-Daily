import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# ----------------------------------------------------------------------
# Utilities
# ----------------------------------------------------------------------
def run(cmd: List[str]) -> None:
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        print("CMD:", " ".join(cmd))
        print(p.stdout)
        print(p.stderr)
        raise SystemExit(p.returncode)

def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))

def safe_filename(name: str, max_len: int = 120) -> str:
    name = re.sub(r"[^\w\-\.\(\)\[\] ]+", "", name, flags=re.UNICODE).strip()
    name = re.sub(r"\s+", "_", name)
    return name[:max_len] if len(name) > max_len else name

def hhmmss(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    s, ms = divmod(ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def escape_ffmpeg_filter_path(p: Path) -> str:
    # FFmpeg filter args treat ':' as an option separator on Windows, escape it: C\:/...
    return p.resolve().as_posix().replace(":", r"\:")

# ----------------------------------------------------------------------
# Environment & Settings
# ----------------------------------------------------------------------
@dataclass
class Settings:
    gemini_key: str
    gemini_model: str
    pexels_api_key: str
    commons_user_agent: str
    ffmpeg_path: str
    piper_data_dir: Path
    music_dir: Optional[Path]
    font_file: Optional[Path]
    openai_api_key: str          # for Sora
    sora_model: str
    use_sora: bool
    anything_world_api_key: str
    use_anything_world: bool

def load_settings(base_dir: Path) -> Settings:
    load_dotenv(base_dir / ".env")

    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
    pexels_api_key = os.getenv("PEXELS_API_KEY", "").strip()
    commons_user_agent = os.getenv(
        "COMMONS_USER_AGENT",
        "WorldPulse-Daily/1.0 (contact: your@email.com)"
    ).strip()
    ffmpeg_path = os.getenv("FFMPEG_PATH", "ffmpeg").strip()
    piper_data_dir = Path(os.getenv("PIPER_DATA_DIR", str(base_dir / "voices"))).resolve()
    music_dir_raw = os.getenv("MUSIC_DIR", "").strip()
    music_dir = Path(music_dir_raw).resolve() if music_dir_raw else None
    font_raw = os.getenv("FONT_FILE", "").strip()
    font_file = Path(font_raw).resolve() if font_raw else None
    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
    sora_model = os.getenv("SORA_MODEL", "sora-2").strip()
    use_sora = os.getenv("USE_SORA", "0").strip() == "1"
    anything_world_api_key = os.getenv("ANYTHING_WORLD_API_KEY", "").strip()
    use_anything_world = os.getenv("USE_ANYTHING_WORLD", "0").strip() == "1"

    if not gemini_key:
        raise SystemExit("Missing GEMINI_API_KEY in .env")
    if not pexels_api_key:
        raise SystemExit("Missing PEXELS_API_KEY in .env")

    return Settings(
        gemini_key=gemini_key,
        gemini_model=gemini_model,
        pexels_api_key=pexels_api_key,
        commons_user_agent=commons_user_agent,
        ffmpeg_path=ffmpeg_path,
        piper_data_dir=piper_data_dir,
        music_dir=music_dir,
        font_file=font_file,
        openai_api_key=openai_api_key,
        sora_model=sora_model,
        use_sora=use_sora,
        anything_world_api_key=anything_world_api_key,
        use_anything_world=use_anything_world,
    )

# ----------------------------------------------------------------------
# Gemini Client (structured output with retries)
# ----------------------------------------------------------------------
class GeminiClient:
    def __init__(self, api_key: str, model: str, timeout: int = 90):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.session = requests.Session()

    def _post(self, body: Dict[str, Any]) -> Dict[str, Any]:
        url = GEMINI_API.format(model=self.model)
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key
        }
        r = self.session.post(url, headers=headers, json=body, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def generate_json(self, prompt: str, schema: Dict[str, Any], temperature: float = 0.7, tries: int = 3) -> Any:
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "response_mime_type": "application/json",
                "response_schema": schema
            }
        }

        last_err = None
        for _ in range(tries):
            try:
                data = self._post(body)
                text = (
                    data.get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "")
                )
                if not text:
                    raise RuntimeError("Gemini returned empty text")
                return json.loads(text)
            except Exception as e:
                last_err = e
                time.sleep(1.2)
        raise RuntimeError(f"Gemini structured output failed: {last_err}") from last_err

# ----------------------------------------------------------------------
# RAG (keyword‑based)
# ----------------------------------------------------------------------
def load_kb_text(kb_dir: Path) -> List[Tuple[str, str]]:
    docs = []
    if not kb_dir.exists():
        return docs
    for p in list(kb_dir.glob("*.md")) + list(kb_dir.glob("*.txt")):
        try:
            docs.append((p.name, p.read_text(encoding="utf-8", errors="ignore")))
        except Exception:
            pass
    return docs

def rag_retrieve(kb_docs: List[Tuple[str, str]], query: str, k: int = 4) -> str:
    q = query.lower()
    q_words = [w for w in re.findall(r"[a-zA-Z]{3,}", q)]
    if not q_words or not kb_docs:
        return ""

    scored = []
    for name, txt in kb_docs:
        t = txt.lower()
        score = sum(t.count(w) for w in q_words)
        if score > 0:
            scored.append((score, name, txt))

    scored.sort(reverse=True, key=lambda x: x[0])
    chunks = []
    for _, name, txt in scored[:k]:
        snippet = txt.strip()
        if len(snippet) > 1200:
            snippet = snippet[:1200] + "..."
        chunks.append(f"[{name}]\n{snippet}")
    return "\n\n".join(chunks)

# ----------------------------------------------------------------------
# Sentiment engine
# ----------------------------------------------------------------------
SENTIMENT_SCHEMA = {
    "type": "OBJECT",
    "required": ["valence", "energy", "tone", "do", "dont"],
    "properties": {
        "valence": {"type": "STRING"},  # positive, neutral, negative
        "energy": {"type": "STRING"},   # low, medium, high
        "tone": {"type": "STRING"},     # motivational, calm, serious, funny, urgent
        "do": {"type": "ARRAY", "items": {"type": "STRING"}},
        "dont": {"type": "ARRAY", "items": {"type": "STRING"}}
    },
}

def sentiment_plan(gem: GeminiClient, lang: str, topic: str) -> Dict[str, Any]:
    prompt = f"""
Analyze the best emotional tone for a YouTube video about:
Topic: {topic}
Language: {lang}

Return JSON with:
- valence: positive, neutral or negative
- energy: low, medium or high
- tone: motivational, calm, serious, funny or urgent
- do: 5 style rules
- dont: 5 style rules
"""
    return gem.generate_json(prompt.strip(), SENTIMENT_SCHEMA, temperature=0.2, tries=2)

# ----------------------------------------------------------------------
# Media providers: Pexels
# ----------------------------------------------------------------------
def pexels_headers(api_key: str) -> Dict[str, str]:
    return {"Authorization": api_key}

def pexels_search_videos(api_key: str, query: str, per_page: int = 8) -> List[Dict[str, Any]]:
    url = "https://api.pexels.com/videos/search"
    params = {"query": query, "per_page": per_page}
    r = requests.get(url, headers=pexels_headers(api_key), params=params, timeout=40)
    r.raise_for_status()
    return r.json().get("videos", [])

def pexels_search_photos(api_key: str, query: str, per_page: int = 8) -> List[Dict[str, Any]]:
    url = "https://api.pexels.com/v1/search"
    params = {"query": query, "per_page": per_page}
    r = requests.get(url, headers=pexels_headers(api_key), params=params, timeout=40)
    r.raise_for_status()
    return r.json().get("photos", [])

def download_file(url: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)

# ----------------------------------------------------------------------
# Wikimedia Commons (AI‑powered selection)
# ----------------------------------------------------------------------
BAD_TITLE_WORDS = {
    "cover", "front cover", "back cover", "spine", "binding", "book",
    "volume", "title page", "frontispiece", "dust jacket", "hardcover"
}

def commons_headers(ua: str) -> Dict[str, str]:
    return {"User-Agent": ua}

def commons_search_files(query: str, limit: int, headers: Dict[str, str]) -> List[str]:
    params = {
        "action": "query",
        "format": "json",
        "list": "search",
        "srsearch": f"File:{query}",
        "srnamespace": 6,
        "srlimit": limit,
    }
    r = requests.get(COMMONS_API, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    return [it["title"] for it in r.json().get("query", {}).get("search", [])]

def commons_imageinfo(title: str, headers: Dict[str, str], want_width: int = 1920) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    params = {
        "action": "query",
        "format": "json",
        "titles": title,
        "prop": "imageinfo",
        "iiprop": "url|extmetadata|mime|size|mediatype",
        "iiurlwidth": want_width
    }
    r = requests.get(COMMONS_API, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    pages = r.json().get("query", {}).get("pages", {})
    page = next(iter(pages.values()))
    ii = (page.get("imageinfo") or [{}])[0]
    meta = ii.get("extmetadata") or {}
    info = {
        "url": ii.get("url"),
        "thumburl": ii.get("thumburl"),
        "mime": ii.get("mime"),
        "mediatype": ii.get("mediatype"),
        "size": ii.get("size"),
        "width": ii.get("width"),
        "height": ii.get("height"),
    }
    url = info.get("thumburl") or info.get("url")
    if not url:
        raise RuntimeError(f"No URL for {title}")
    return url, meta, info

def download(url: str, out_path: Path, headers: Dict[str, str]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, headers=headers, timeout=90) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)

def _meta_text(meta: dict) -> str:
    parts = []
    for k in ("ImageDescription", "ObjectName", "Credit", "Artist"):
        v = (meta.get(k) or {}).get("value", "")
        if v:
            parts.append(str(v))
    return " ".join(parts).lower()

def _is_bad_candidate(title: str, meta: dict) -> bool:
    t = title.lower()
    m = _meta_text(meta)
    for w in BAD_TITLE_WORDS:
        if w in t or w in m:
            return True
    return False

def _is_too_small(info: dict, min_w: int = 1200, min_h: int = 700) -> bool:
    w = int(info.get("width") or 0)
    h = int(info.get("height") or 0)
    return w < min_w or h < min_h

def ai_asset_relevance(gem: GeminiClient, scene: dict, title: str, meta: dict) -> dict:
    prompt = f"""
Decide if this Wikimedia Commons file fits the scene.

Scene narration: {scene.get("narration")}
Scene on_screen_text: {scene.get("on_screen_text")}
Desired visual_query: {scene.get("visual_query")}

Candidate title: {title}
Candidate description/meta: {_meta_text(meta)[:600]}

Return JSON:
{{
  "use": boolean,
  "reason": "short",
  "better_query": "short improved query if use=false"
}}
"""
    schema = {
        "type": "OBJECT",
        "required": ["use", "reason", "better_query"],
        "properties": {
            "use": {"type": "BOOLEAN"},
            "reason": {"type": "STRING"},
            "better_query": {"type": "STRING"}
        }
    }
    return gem.generate_json(prompt, schema, temperature=0.2, tries=2)

def pick_best_commons_asset(
    headers: dict,
    gem: GeminiClient,
    scene: dict,
    query: str,
    limit: int = 25
) -> Tuple[str, str, dict, dict]:
    titles = commons_search_files(query, limit=limit, headers=headers)

    if not titles:
        titles = commons_search_files("procrastination brain diagram", limit=limit, headers=headers)

    best = None

    for title in titles:
        try:
            url, meta, info = commons_imageinfo(title, headers=headers, want_width=1920)
        except Exception:
            continue

        if _is_bad_candidate(title, meta):
            continue
        if _is_too_small(info):
            continue

        verdict = ai_asset_relevance(gem, scene, title, meta)
        if not verdict.get("use", False):
            bq = verdict.get("better_query", "").strip()
            if bq:
                try:
                    t2 = commons_search_files(bq, limit=10, headers=headers)
                    if t2:
                        url2, meta2, info2 = commons_imageinfo(t2[0], headers=headers, want_width=1920)
                        if not _is_bad_candidate(t2[0], meta2) and not _is_too_small(info2):
                            verdict2 = ai_asset_relevance(gem, scene, t2[0], meta2)
                            if verdict2.get("use", False):
                                return t2[0], url2, meta2, info2
                except Exception:
                    pass
            continue

        best = (title, url, meta, info)
        break

    if not best:
        for title in titles[:10]:
            try:
                url, meta, info = commons_imageinfo(title, headers=headers, want_width=1920)
            except Exception:
                continue
            if _is_bad_candidate(title, meta):
                continue
            best = (title, url, meta, info)
            break

    if not best:
        raise SystemExit("No suitable Commons assets found. Add local fallback images as a backup.")

    return best

# ----------------------------------------------------------------------
# Video building (fixed thumbnail filter)
# ----------------------------------------------------------------------
def motion_filter(motion: str, duration: float, w: int, h: int, fps: int) -> str:
    """
    Returns an FFmpeg filterchain string (usually starting with zoompan) that approximates
    trendy YouTube motion styles + videography vibes using only 2D transforms.

    Use `motion` as a preset key.

    Core moves (classic):
      - zoom_in, zoom_out
      - pan_left, pan_right, pan_up, pan_down
      - drift (subtle parallax-like)
      - punch_in (micro-zoom emphasis)

    Trendy editing vibes mapped to 2D:
      - dopamine        -> punch_in + tiny shake
      - beat_sync       -> clean drift (you cut on the beat outside this filter)
      - ramp_burst      -> zoom curve that feels like a speed-ramp moment
      - whip_left/right -> fast pan + blur
      - handheld_doc    -> drift + shake + slight film grain
      - film_emulation  -> grain + vignette + tiny gate weave
      - mixed_media     -> texture-like sharpening + grain
      - hyperlapse      -> faster pan drift

    Notes:
      - True 2.5D parallax needs multiple layers (foreground/background) so here we fake it with drift.
      - Kinetic captions and UI callouts are separate (drawtext/overlay in another layer).
    """
    d = max(1, int(round(duration * fps)))
    motion = (motion or "zoom_in").strip().lower()

    # ---------- helpers ----------
    def _zoom_in(step=0.0012, zmax=1.25):
        return f"zoompan=z='min(zoom+{step},{zmax})':d={d}:s={w}x{h}:fps={fps}"

    def _zoom_out(step=0.0012, zstart=1.25):
        return (
            f"zoompan=z='if(eq(on,0),{zstart},max(1.0,zoom-{step}))'"
            f":d={d}:s={w}x{h}:fps={fps}"
        )

    def _pan(z=1.10, x_expr="0", y_expr="0"):
        return (
            f"zoompan=z='{z}':x='{x_expr}':y='{y_expr}'"
            f":d={d}:s={w}x{h}:fps={fps}"
        )

    def _center_x(z):
        return "iw*(1-1/zoom)/2"

    def _center_y(z):
        return "ih*(1-1/zoom)/2"

    def _drift(z=1.12, strength=0.018, speed=1.0):
        # Subtle left/right and up/down drift, sells “premium” motion on stills
        # strength is a fraction of frame travel
        amp_x = f"iw*(1-1/zoom)*{strength}"
        amp_y = f"ih*(1-1/zoom)*{strength}"
        # use on/d instead of t so it stays stable even if your timeline has no timestamps
        x = f"{_center_x(z)} + sin(2*PI*{speed}*on/{d})*({amp_x})"
        y = f"{_center_y(z)} + cos(2*PI*{speed}*on/{d})*({amp_y})"
        return _pan(z=z, x_expr=x, y_expr=y)

    def _shake(px=6, py=5, hz_x=3.0, hz_y=2.3):
        # Adds a light handheld feel after zoompan output
        # Crop slightly then scale back to avoid black borders.
        return (
            f"crop=iw*0.98:ih*0.98:"
            f"x='(iw-ow)/2 + sin(2*PI*{hz_x}*t)*{px}':"
            f"y='(ih-oh)/2 + sin(2*PI*{hz_y}*t)*{py}',"
            f"scale={w}:{h}"
        )

    def _film(grain=10, gate=0.0012):
        # Grain + vignette + tiny gate weave
        return (
            f"noise=alls={grain}:allf=t+u,"
            f"vignette,"
            f"rotate='{gate}*sin(2*PI*0.7*t)':c=black@0,"
            f"scale={w}:{h}"
        )

    def _blur(amount=2.2):
        return f"gblur=sigma={amount}:steps=1"

    # ---------- base presets ----------
    if motion == "zoom_out":
        return _zoom_out()

    if motion == "pan_left":
        return _pan(
            z=1.10,
            x_expr=f"iw*(1-1/zoom)*on/{d}",
            y_expr=f"ih*(1-1/zoom)/2",
        )

    if motion == "pan_right":
        return _pan(
            z=1.10,
            x_expr=f"iw*(1-1/zoom)*(1-on/{d})",
            y_expr=f"ih*(1-1/zoom)/2",
        )

    if motion == "pan_up":
        return _pan(
            z=1.10,
            x_expr=f"iw*(1-1/zoom)/2",
            y_expr=f"ih*(1-1/zoom)*(1-on/{d})",
        )

    if motion == "pan_down":
        return _pan(
            z=1.10,
            x_expr=f"iw*(1-1/zoom)/2",
            y_expr=f"ih*(1-1/zoom)*on/{d}",
        )

    if motion in ("drift", "parallax", "parallax_25d"):
        # “2.5D vibe” on a single layer (fake parallax)
        return _drift(z=1.13, strength=0.020, speed=0.85)

    # ---------- trendy editing vibes ----------
    if motion in ("punch_in", "push_in", "micro_zoom"):
        # Emphasis move for shorts and talking head moments
        return _zoom_in(step=0.0019, zmax=1.35)

    if motion == "dopamine":
        # Hyper-energy feel: a punchy zoom + tiny handheld shake
        return f"{_zoom_in(step=0.0021, zmax=1.33)},{_shake(px=7, py=6, hz_x=3.6, hz_y=2.8)}"

    if motion == "beat_sync":
        # Clean, consistent motion (you still cut on the beat in your editor)
        return _drift(z=1.10, strength=0.016, speed=1.15)

    if motion in ("ramp_burst", "speed_ramp_burst"):
        # Feels like a speed-ramp moment but stays same duration
        # Fast zoom up, settle down, slow creep
        z_expr = (
            f"if(lt(on,{int(d*0.22)}),"
            f" 1 + 0.28*on/{max(1,int(d*0.22))},"
            f" if(lt(on,{int(d*0.62)}),"
            f" 1.28 - 0.14*(on-{int(d*0.22)})/{max(1,int(d*0.40))},"
            f" 1.14 + 0.06*(on-{int(d*0.62)})/{max(1,int(d*0.38))}"
            f" ))"
        )
        return f"zoompan=z='{z_expr}':d={d}:s={w}x{h}:fps={fps}"

    if motion in ("whip_left", "whip_pan_left"):
        zp = _pan(
            z=1.06,
            x_expr=f"iw*(1-1/zoom)*pow(on/{d},1.6)",
            y_expr=f"ih*(1-1/zoom)/2",
        )
        return f"{zp},{_blur(2.4)}"

    if motion in ("whip_right", "whip_pan_right"):
        zp = _pan(
            z=1.06,
            x_expr=f"iw*(1-1/zoom)*(1-pow(on/{d},1.6))",
            y_expr=f"ih*(1-1/zoom)/2",
        )
        return f"{zp},{_blur(2.4)}"

    if motion in ("handheld", "handheld_doc", "doc_realness"):
        # Documentary feel: drift + shake + light grain
        return f"{_drift(z=1.12, strength=0.018, speed=0.9)},{_shake(px=6, py=5)},{_film(grain=7, gate=0.0009)}"

    if motion in ("film", "film_emulation", "nostalgia"):
        return f"{_drift(z=1.10, strength=0.014, speed=0.75)},{_film(grain=12, gate=0.0013)}"

    if motion in ("mixed_media", "collage"):
        # Texture-ish pop (not true collage, but gives that gritty edited vibe)
        return f"{_drift(z=1.11, strength=0.017, speed=0.95)},unsharp=5:5:1.1:5:5:0.0,noise=alls=8:allf=t+u"

    if motion in ("hyperlapse", "travel_lines"):
        # Faster travel pan feel
        return _pan(
            z=1.12,
            x_expr=f"iw*(1-1/zoom)*on/{max(1,int(d*0.55))}",
            y_expr=f"ih*(1-1/zoom)/2",
        )

    # Default: classic slow zoom in
    return _zoom_in()

def make_clip_from_image(
    ffmpeg: str,
    img: Path,
    duration: float,
    out_clip: Path,
    motion: str,
    w: int = 1920,
    h: int = 1080,
    fps: int = 30,
    fade: float = 0.35
) -> None:
    fade = clamp(fade, 0.0, duration / 3.0)
    fade_out_start = max(0.0, duration - fade)

    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
        f"{motion_filter(motion, duration, w, h, fps)},"
        f"fade=t=in:st=0:d={fade},"
        f"fade=t=out:st={fade_out_start}:d={fade},"
        f"format=yuv420p"
    )

    run([
        ffmpeg, "-y",
        "-loop", "1",
        "-t", str(duration),
        "-i", str(img),
        "-vf", vf,
        "-r", str(fps),
        "-c:v", "libx264",
        "-crf", "18",
        "-preset", "veryfast",
        str(out_clip)
    ])

def trim_video_clip(ffmpeg: str, src: Path, duration: float, out_clip: Path, w: int = 1920, h: int = 1080, fps: int = 30) -> None:
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={fps},"
        f"format=yuv420p"
    )
    run([
        ffmpeg, "-y",
        "-t", str(duration),
        "-i", str(src),
        "-vf", vf,
        "-an",
        "-c:v", "libx264",
        "-crf", "18",
        "-preset", "veryfast",
        str(out_clip)
    ])

def write_srt_from_scenes(scenes: List[Dict[str, Any]], out_path: Path) -> None:
    t = 0.0
    lines: List[str] = []
    for i, sc in enumerate(scenes, start=1):
        dur = float(sc["duration_sec"])
        start = hhmmss(t)
        end = hhmmss(t + dur)
        text = (sc.get("caption_text") or sc.get("on_screen_text") or sc.get("narration") or "").strip()
        if not text:
            text = " "
        lines += [str(i), f"{start} --> {end}", text, ""]
        t += dur
    out_path.write_text("\n".join(lines), encoding="utf-8")

def render_final(
    ffmpeg: str,
    clips: List[Path],
    voice_wav: Path,
    srt: Path,
    out_mp4: Path,
    music_path: Optional[Path] = None,
    music_duck: float = 0.18
) -> None:
    out_mp4.parent.mkdir(parents=True, exist_ok=True)

    concat_txt = out_mp4.parent / "concat.txt"
    concat_txt.write_text("\n".join([f"file '{c.as_posix()}'" for c in clips]), encoding="utf-8")

    srt_fs = escape_ffmpeg_filter_path(srt)

    cmd = [
        ffmpeg, "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_txt),
        "-i", str(voice_wav),
    ]

    if music_path:
        cmd += ["-i", str(music_path)]
        af = (
            f"[2:a]volume={music_duck}[bg];"
            f"[1:a][bg]amix=inputs=2:duration=shortest:dropout_transition=2[aout]"
        )
        cmd += [
            "-filter_complex", af,
            "-map", "0:v:0",
            "-map", "[aout]"
        ]
    else:
        cmd += ["-map", "0:v:0", "-map", "1:a:0"]

    cmd += [
        "-vf", f"subtitles='{srt_fs}'",
        "-c:v", "libx264",
        "-crf", "18",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-shortest",
        "-movflags", "+faststart",
        str(out_mp4)
    ]

    run(cmd)

def render_thumbnail(ffmpeg: str, background: Path, text: str, out_png: Path, font_file: Optional[Path]) -> None:
    out_png.parent.mkdir(parents=True, exist_ok=True)
    txt = text.replace(":", r"\:").replace("'", r"\'")

    if font_file and font_file.exists():
        font_opt = f":fontfile='{escape_ffmpeg_filter_path(font_file)}'"
    else:
        font_opt = ""

    # Fixed: proper cover effect (scale to cover then crop)
    vf = (
        "scale=1280:720:force_original_aspect_ratio=increase,"
        "crop=1280:720,"
        "eq=contrast=1.08:saturation=1.10,"
        f"drawtext=text='{txt}'{font_opt}:x=(w-text_w)/2:y=(h*0.72):"
        "fontsize=64:fontcolor=white:borderw=4:bordercolor=black@0.75"
    )

    run([
        ffmpeg, "-y",
        "-i", str(background),
        "-frames:v", "1",
        "-vf", vf,
        str(out_png)
    ])

# ----------------------------------------------------------------------
# Piper TTS
# ----------------------------------------------------------------------
def piper_tts(voice_model: str, text: str, out_wav: Path, data_dir: Path) -> None:
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    txt = out_wav.parent / "narration.txt"
    txt.write_text(text, encoding="utf-8")

    run([
        sys.executable, "-m", "piper",
        "--data-dir", str(data_dir),
        "-m", voice_model,
        "-f", str(out_wav),
        "--input-file", str(txt),
    ])

# ----------------------------------------------------------------------
# SORA (optional)
# ----------------------------------------------------------------------
def sora_generate_scene_clip(openai_key: str, model: str, prompt: str, out_mp4: Path) -> bool:
    if not openai_key:
        return False

    base = "https://api.openai.com/v1/videos"
    headers = {"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"}

    body = {"model": model, "prompt": prompt}
    r = requests.post(base, headers=headers, json=body, timeout=60)
    if r.status_code >= 400:
        return False
    job = r.json()
    vid = job.get("id")
    if not vid:
        return False

    for _ in range(120):
        g = requests.get(f"{base}/{vid}", headers=headers, timeout=30)
        if g.status_code >= 400:
            time.sleep(2)
            continue
        st = g.json().get("status", "")
        if st == "completed":
            break
        if st in {"failed", "canceled"}:
            return False
        time.sleep(2)

    d = requests.get(f"{base}/{vid}/content", headers=headers, timeout=120)
    if d.status_code >= 400:
        return False
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    out_mp4.write_bytes(d.content)
    return True

# ----------------------------------------------------------------------
# AI script generation (with RAG + sentiment)
# ----------------------------------------------------------------------
SCRIPT_SCHEMA: Dict[str, Any] = {
    "type": "OBJECT",
    "required": ["title", "description", "tags", "thumbnail", "scenes", "quality"],
    "properties": {
        "title": {"type": "STRING"},
        "description": {"type": "STRING"},
        "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
        "thumbnail": {
            "type": "OBJECT",
            "required": ["text", "style_hint"],
            "properties": {
                "text": {"type": "STRING"},
                "style_hint": {"type": "STRING"}
            }
        },
        "quality": {
            "type": "OBJECT",
            "required": ["retention_score", "clarity_score", "risk_flags"],
            "properties": {
                "retention_score": {"type": "NUMBER"},
                "clarity_score": {"type": "NUMBER"},
                "risk_flags": {"type": "ARRAY", "items": {"type": "STRING"}}
            }
        },
        "scenes": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "required": ["narration", "on_screen_text", "visual_query", "duration_sec", "motion_style", "broll_preference", "caption_text"],
                "properties": {
                    "narration": {"type": "STRING"},
                    "on_screen_text": {"type": "STRING"},
                    "caption_text": {"type": "STRING"},
                    "visual_query": {"type": "STRING"},
                    "duration_sec": {"type": "NUMBER"},
                    "motion_style": {"type": "STRING"},
                    "broll_preference": {"type": "STRING"}  # "image" or "video"
                }
            }
        }
    }
}

VISUAL_REWRITE_SCHEMA: Dict[str, Any] = {
    "type": "OBJECT",
    "required": ["visual_query", "fallback_query"],
    "properties": {
        "visual_query": {"type": "STRING"},
        "fallback_query": {"type": "STRING"}
    }
}

def normalize_durations(scenes: List[Dict[str, Any]], target_sec: int) -> None:
    total = sum(float(s.get("duration_sec", 0)) for s in scenes)
    if total <= 0:
        raise RuntimeError("Bad scene durations")
    diff = float(target_sec) - float(total)
    if abs(diff) >= 0.01:
        scenes[-1]["duration_sec"] = max(1.0, float(scenes[-1]["duration_sec"]) + diff)

def build_chapters(scenes: List[Dict[str, Any]]) -> str:
    t = 0.0
    lines = []
    for idx, sc in enumerate(scenes, start=1):
        ts = hhmmss(t).replace(",", ".")[:-4]  # 00:00:00
        label = (sc.get("on_screen_text") or f"Scene {idx}").strip()
        lines.append(f"{ts} {label}")
        t += float(sc["duration_sec"])
    return "\n".join(lines)

def pick_music_track(music_dir: Path, vibe_hint: str) -> Optional[Path]:
    if not music_dir.exists():
        return None
    tracks = list(music_dir.glob("*.mp3")) + list(music_dir.glob("*.wav"))
    if not tracks:
        return None
    random.shuffle(tracks)
    return tracks[0]

def ai_generate_script_pack(
    gem: GeminiClient,
    topic: str,
    keywords: List[str],
    duration_sec: int,
    lang: str,
    region: str,
    sentiment: Dict[str, Any],
    rag_context: str
) -> Dict[str, Any]:
    kw = ", ".join([k for k in keywords if k]) if keywords else ""
    style_do = "\n".join([f"- {x}" for x in sentiment.get("do", [])])
    style_dont = "\n".join([f"- {x}" for x in sentiment.get("dont", [])])

    prompt = f"""
You are a production YouTube script engine.

Goal: produce a short video that keeps retention.
Output language: {lang}
Target region: {region}
Topic: {topic}
Keywords: {kw}
Target duration seconds: {duration_sec}

Sentiment plan:
- valence: {sentiment.get("valence")}
- energy: {sentiment.get("energy")}
- tone: {sentiment.get("tone")}
Do:
{style_do}
Dont:
{style_dont}

RAG context (use if relevant, do not invent sources):
{rag_context}

Hard rules:
- First 5 seconds must be a strong hook
- Avoid generic lines
- Avoid risky claims, medical advice, financial advice
- Scenes durations must sum exactly to target duration seconds
- caption_text must be short, punchy, max ~70 chars
- motion_style must use few of these randomized or all: zoom_in, zoom_out, pan_left, pan_right, pan_up, pan_down, drift, punch_in, dopamine, beat_sync, ramp_burst, whip_left, whip_right, handheld_doc, film_emulation, mixed_media, hyperlapse
- broll_preference must be image or video
- Provide retention_score and clarity_score from 0 to 10
- If anything is risky, put it in risk_flags and rewrite safer

Return ONLY JSON matching schema.
""".strip()

    pack = gem.generate_json(prompt, SCRIPT_SCHEMA, temperature=0.75, tries=3)
    normalize_durations(pack["scenes"], duration_sec)
    return pack

def ai_refine_visual_query(gem: GeminiClient, scene: Dict[str, Any], lang: str) -> Dict[str, str]:
    prompt = f"""
Rewrite this Wikimedia Commons search query to find better results.
Keep it short, concrete and visual.

Language: {lang}
Original visual_query: {scene.get("visual_query")}
Scene narration: {scene.get("narration")}

Return JSON with:
- visual_query: improved query
- fallback_query: safe generic fallback for the same concept
""".strip()
    return gem.generate_json(prompt, VISUAL_REWRITE_SCHEMA, temperature=0.4, tries=2)

# ----------------------------------------------------------------------
# Main pipeline
# ----------------------------------------------------------------------
def main():
    base_dir = Path(__file__).resolve().parent
    st = load_settings(base_dir)
    gem = GeminiClient(api_key=st.gemini_key, model=st.gemini_model)

    ap = argparse.ArgumentParser()
    ap.add_argument("--jobdir", required=True)
    ap.add_argument("--script_json", default="")
    ap.add_argument("--topic", default="")
    ap.add_argument("--keywords", nargs="*", default=[])
    ap.add_argument("--duration_sec", type=int, default=10)
    ap.add_argument("--lang", default="en")
    ap.add_argument("--region", default="global")
    ap.add_argument("--voice_model", required=True)
    ap.add_argument("--ffmpeg", default=st.ffmpeg_path)
    ap.add_argument("--keep_clips", action="store_true")
    ap.add_argument("--use_music", action="store_true")
    args = ap.parse_args()

    headers = commons_headers(st.commons_user_agent)

    jobdir = Path(args.jobdir).resolve()
    jobdir.mkdir(parents=True, exist_ok=True)

    # Load KB for RAG
    kb_docs = load_kb_text(base_dir / "kb")

    # 1) Script pack
    if args.script_json:
        pack = json.loads(Path(args.script_json).read_text(encoding="utf-8"))
        scenes = pack["scenes"]
        normalize_durations(scenes, args.duration_sec)
    else:
        if not args.topic:
            raise SystemExit("Provide --script_json or provide --topic")
        # RAG + sentiment
        rag_ctx = rag_retrieve(kb_docs, args.topic, k=4)
        sentiment = sentiment_plan(gem, args.lang, args.topic)
        pack = ai_generate_script_pack(
            gem=gem,
            topic=args.topic,
            keywords=args.keywords,
            duration_sec=args.duration_sec,
            lang=args.lang,
            region=args.region,
            sentiment=sentiment,
            rag_context=rag_ctx
        )
        (jobdir / "script_pack.json").write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
        scenes = pack["scenes"]

    # 2) Captions and narration
    narration = "\n".join([s.get("narration", "").strip() for s in scenes if s.get("narration")]).strip()
    if not narration:
        raise SystemExit("No narration found")

    voice_wav = jobdir / "voice.wav"
    piper_tts(args.voice_model, narration, voice_wav, st.piper_data_dir)

    srt = jobdir / "captions.srt"
    write_srt_from_scenes(scenes, srt)

    # 3) Assets
    imgs_dir = jobdir / "assets"
    clips_dir = jobdir / "clips"
    imgs_dir.mkdir(parents=True, exist_ok=True)
    clips_dir.mkdir(parents=True, exist_ok=True)

    credits: List[str] = []
    clips: List[Path] = []

    for i, sc in enumerate(scenes, start=1):
        # 3a) AI refine visual query
        try:
            rewritten = ai_refine_visual_query(gem, sc, args.lang)
            q = rewritten["visual_query"]
        except Exception:
            q = sc.get("visual_query") or sc.get("on_screen_text") or "abstract background"

        dur = float(sc["duration_sec"])
        motion = sc.get("motion_style") or "zoom_in"
        pref = (sc.get("broll_preference") or "image").lower()

        chosen_asset = None

        # 3b) Pexels video first
        try:
            vids = pexels_search_videos(st.pexels_api_key, q, per_page=8)
            if vids:
                v = vids[0]
                files = v.get("video_files", [])
                files = sorted(files, key=lambda x: (x.get("width", 0) or 0), reverse=True)
                vurl = files[0].get("link") if files else None
                if vurl:
                    asset_path = imgs_dir / f"scene_{i:03d}.mp4"
                    download_file(vurl, asset_path)
                    credits.append(f"Pexels video: {v.get('url','')} by {v.get('user',{}).get('name','')}")
                    clip_path = clips_dir / f"clip_{i:03d}.mp4"
                    trim_video_clip(args.ffmpeg, asset_path, dur, clip_path)
                    clips.append(clip_path)
                    chosen_asset = "pexels_video"
        except Exception:
            pass

        # 3c) Pexels photo
        if not chosen_asset:
            try:
                photos = pexels_search_photos(st.pexels_api_key, q, per_page=8)
                if photos:
                    p = photos[0]
                    src = p.get("src", {})
                    iurl = src.get("large2x") or src.get("large") or src.get("original")
                    if iurl:
                        asset_path = imgs_dir / f"scene_{i:03d}.jpg"
                        download_file(iurl, asset_path)
                        credits.append(f"Pexels photo: {p.get('url','')} by {p.get('photographer','')}")
                        clip_path = clips_dir / f"clip_{i:03d}.mp4"
                        make_clip_from_image(args.ffmpeg, asset_path, dur, clip_path, motion)
                        clips.append(clip_path)
                        chosen_asset = "pexels_photo"
            except Exception:
                pass

        # 3d) Wikimedia Commons with AI filtering
        if not chosen_asset:
            try:
                title, url, meta, info = pick_best_commons_asset(
                    headers=headers,
                    gem=gem,
                    scene=sc,
                    query=q,
                    limit=25
                )
            except SystemExit:
                # Last resort: simple search without AI filtering
                titles = commons_search_files(q, limit=8, headers=headers)
                if not titles:
                    titles = commons_search_files("abstract background", limit=8, headers=headers)
                if not titles:
                    raise SystemExit(f"No asset found for scene {i}, query {q}")
                title = titles[0]
                url, meta, info = commons_imageinfo(title, headers=headers, want_width=1920)

            ext = Path(url).suffix or ".jpg"
            asset_path = imgs_dir / f"scene_{i:03d}{ext}"
            download(url, asset_path, headers=headers)

            artist = (meta.get("Artist") or {}).get("value", "")
            lic = (meta.get("LicenseShortName") or {}).get("value", "")
            lic_url = (meta.get("LicenseUrl") or {}).get("value", "")
            credits.append(f"{title} | {lic} | {lic_url} | {artist}".strip())

            clip_path = clips_dir / f"clip_{i:03d}.mp4"

            is_video = str(info.get("mediatype") or "").lower() in {"video", "audio"} or str(info.get("mime") or "").startswith("video/")
            if pref == "video" and is_video:
                trim_video_clip(args.ffmpeg, asset_path, dur, clip_path)
            else:
                make_clip_from_image(args.ffmpeg, asset_path, dur, clip_path, motion)

            clips.append(clip_path)

        time.sleep(0.5)

    (jobdir / "CREDITS.txt").write_text("\n".join(credits), encoding="utf-8")

    # 4) Thumbnail
    thumb_text = (pack.get("thumbnail") or {}).get("text") or pack.get("title", "New video")
    background_for_thumb = imgs_dir / "scene_001.jpg"
    if not background_for_thumb.exists():
        assets = sorted(imgs_dir.glob("scene_*"))
        if assets:
            background_for_thumb = assets[0]
    thumbnail_png = jobdir / "thumbnail.png"
    if background_for_thumb.exists():
        render_thumbnail(args.ffmpeg, background_for_thumb, thumb_text, thumbnail_png, st.font_file)

    # 5) Optional music
    music_path = None
    if args.use_music and st.music_dir:
        vibe = (pack.get("thumbnail") or {}).get("style_hint") or "calm"
        music_path = pick_music_track(st.music_dir, vibe)

    # 6) Render final
    final_mp4 = jobdir / "final.mp4"
    render_final(
        ffmpeg=args.ffmpeg,
        clips=clips,
        voice_wav=voice_wav,
        srt=srt,
        out_mp4=final_mp4,
        music_path=music_path
    )

    # 7) Description extras
    chapters = build_chapters(scenes)
    meta_out = {
        "title": pack.get("title", ""),
        "description": pack.get("description", "") + "\n\nChapters:\n" + chapters,
        "tags": pack.get("tags", []),
        "thumbnail_text": thumb_text,
        "quality": pack.get("quality", {})
    }
    (jobdir / "youtube_meta.json").write_text(json.dumps(meta_out, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.keep_clips:
        for c in clips_dir.glob("clip_*.mp4"):
            try:
                c.unlink()
            except Exception:
                pass
        try:
            clips_dir.rmdir()
        except Exception:
            pass

    print(str(final_mp4))

if __name__ == "__main__":
    main()