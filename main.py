import asyncio
import os
import re
import shutil
import sys
import uuid
import subprocess
import traceback
import urllib.parse
import shutil
import tempfile
import httpx
import edge_tts
import time
import hashlib
import struct
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi import Header
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Reels TTS generator Backend")

def _parse_allowed_origins() -> list[str]:
    raw = os.getenv("VOICELAB_ALLOWED_ORIGINS", "").strip()
    if not raw:
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


ALLOWED_ORIGINS = _parse_allowed_origins()
API_KEY = os.getenv("VOICELAB_API_KEY", "").strip()

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _require_api_key(x_api_key: str | None):
    if not API_KEY:
        return
    if not x_api_key or x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

class TTSRequest(BaseModel):
    text: str
    voice: str = "en-US-GuyNeural"

class CloneTTSRequest(BaseModel):
    text: str
    speaker_id: str

class ReelRequest(BaseModel):
    script: str
    mood: str
    voice: str = "en-US-GuyNeural"
    rate: str = "-10%"
    pitch: str = "-5Hz"
    image_description: str = None
    motion: bool = True
    # Paste a Google Fonts stylesheet URL here, for example:
    # https://fonts.googleapis.com/css2?family=Inter:wght@400;700&display=swap
    # We rewrite it server-side to request TTF-compatible faces for FFmpeg subtitles.
    google_font_css_url: str | None = None

MAX_REEL_SCENES = 12
MIN_SCENES_FOR_MULTI = 2
_SCENE_PREP_SEM: asyncio.Semaphore | None = None


def _scene_prep_sem() -> asyncio.Semaphore:
    global _SCENE_PREP_SEM
    if _SCENE_PREP_SEM is None:
        _SCENE_PREP_SEM = asyncio.Semaphore(4)
    return _SCENE_PREP_SEM

def cleanup_files(*files):
    for file in files:
        try:
            if file and os.path.exists(file):
                if os.path.isdir(file):
                    shutil.rmtree(file)
                else:
                    os.remove(file)
        except Exception as e:
            print(f"Error deleting {file}: {e}")

def _resolve_tool(name: str) -> str:
    """Resolve ffmpeg/ffprobe: project ffmpeg_bin (incl. .exe on Windows), then PATH."""
    bin_dir = os.path.join(os.getcwd(), "ffmpeg_bin")
    candidates = [name]
    if sys.platform == "win32":
        candidates = [f"{name}.exe", name]
    for candidate in candidates:
        local = os.path.join(bin_dir, candidate)
        if os.path.isfile(local):
            return local
    found = shutil.which(name)
    if found:
        return found
    if sys.platform == "win32":
        found = shutil.which(f"{name}.exe")
        if found:
            return found
    raise FileNotFoundError(
        f"{name} not found. Install FFmpeg and add it to PATH, or place {name}.exe in ffmpeg_bin/ "
        f"(see https://ffmpeg.org/download.html)."
    )


def _ffmpeg_path() -> str:
    return _resolve_tool("ffmpeg")


def _ffprobe_path() -> str:
    return _resolve_tool("ffprobe")


def _ensure_ffmpeg_tools() -> None:
    _ffmpeg_path()
    _ffprobe_path()


def _run_ffmpeg(cmd: list[str], cwd: str) -> None:
    binary = cmd[0]
    if binary == "ffmpeg":
        cmd[0] = _ffmpeg_path()
    elif binary == "ffprobe":
        cmd[0] = _ffprobe_path()
    process = subprocess.run(
        cmd, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=300,
    )
    if process.returncode != 0:
        detail = (process.stderr or "").strip()[-400:]
        raise Exception(f"FFmpeg failed{f': {detail}' if detail else ''}")


def get_audio_duration(file_path, fallback_text=""):
    try:
        cmd = [
            "ffprobe", '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', file_path
        ]
        ffprobe = _ffprobe_path()
        cmd[0] = ffprobe
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
        if result.returncode == 0:
            return float(result.stdout.strip())
    except Exception as e:
        print(f"Error getting duration: {e}")
    word_count = len(fallback_text.split())
    return max(3.0, word_count / 2.5) if word_count else 10.0

_VOICE_CACHE = {"ts": 0.0, "voices": set()}


async def _get_voice_names(ttl_seconds: int = 6 * 60 * 60) -> set[str]:
    now = time.time()
    if _VOICE_CACHE["voices"] and (now - _VOICE_CACHE["ts"]) < ttl_seconds:
        return _VOICE_CACHE["voices"]

    voices = await edge_tts.list_voices()
    names = {v.get("ShortName") for v in voices if v.get("ShortName")}
    _VOICE_CACHE["ts"] = now
    _VOICE_CACHE["voices"] = names
    return names

_GOOGLE_CSS_HOST_ALLOWLIST = frozenset({"fonts.googleapis.com", "fonts.gstatic.com"})
_USER_AGENT_CSS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36"
)


def _assert_allowed_font_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    scheme = (parsed.scheme or "").lower()
    if scheme != "https":
        raise ValueError("Font URLs must use https")
    if host not in _GOOGLE_CSS_HOST_ALLOWLIST:
        raise ValueError("Only Google Fonts hosts are allowed (fonts.googleapis.com, fonts.gstatic.com)")

def _normalize_google_font_input(url: str) -> str:
    """
    Accept:
    - fonts.googleapis.com/css2?... (preferred)
    - fonts.gstatic.com/...ttf (direct)
    - fonts.google.com/specimen/<Family> (user-friendly page URL)
    """
    parsed = urllib.parse.urlparse(url.strip())
    host = (parsed.hostname or "").lower()

    if host in {"fonts.googleapis.com", "fonts.gstatic.com"}:
        return url.strip()

    if host == "fonts.google.com":
        path = parsed.path or ""
        if "/specimen/" in path:
            family_raw = path.split("/specimen/", 1)[1].split("/", 1)[0].strip()
            if family_raw:
                family = family_raw.replace("+", " ").replace("%20", " ").strip()
                family_q = family.replace(" ", "+")
                return f"https://fonts.googleapis.com/css2?family={family_q}&display=swap"
        raise ValueError(
            "Unsupported fonts.google.com link. Please use a specific family page like "
            "https://fonts.google.com/specimen/Inter or paste a fonts.googleapis.com CSS URL."
        )

    raise ValueError(
        "Unsupported font link. Paste a Google Fonts CSS URL (fonts.googleapis.com), "
        "a direct .ttf link (fonts.gstatic.com), or a fonts.google.com/specimen/<Family> link."
    )


def _extract_font_face_blocks(css_text: str) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    for m in re.finditer(r"@font-face\s*\{([^}]+)\}", css_text, flags=re.IGNORECASE | re.DOTALL):
        body = m.group(1)
        fam_m = re.search(r"font-family\s*:\s*(['\"]?)([^;'\"]+)\1\s*;", body, flags=re.IGNORECASE)
        src_m = re.search(r"src\s*:\s*([^;]+);", body, flags=re.IGNORECASE)
        if not src_m:
            continue
        family = fam_m.group(2).strip() if fam_m else ""
        src = src_m.group(1).strip()
        blocks.append({"family": family, "src": src})
    return blocks


def _pick_ttf_url_from_src(src: str, base_url: str) -> str | None:
    # Prefer explicit url("...ttf") patterns
    for um in re.finditer(r"url\(([^)]+)\)", src):
        raw = um.group(1).strip().strip('"').strip("'")
        if not raw:
            continue
        if raw.lower().endswith(".ttf"):
            full = urllib.parse.urljoin(base_url, raw)
            return full
    return None


async def _download_bytes(client: httpx.AsyncClient, url: str, headers: dict[str, str] | None = None) -> bytes:
    _assert_allowed_font_url(url)
    merged = {"User-Agent": _USER_AGENT_CSS}
    if headers:
        merged.update(headers)
    resp = await client.get(url, timeout=60.0, headers=merged)
    resp.raise_for_status()
    return resp.content


def _read_ttf_name_family(ttf_path: str) -> str | None:
    """
    Minimal TrueType/OpenType name-table reader: returns Name ID 1 (Font Family).
    Enough for subtitles Fontname matching in libass/ffmpeg.
    """
    try:
        with open(ttf_path, "rb") as f:
            data = f.read()
        if len(data) < 12:
            return None

        scaler = struct.unpack(">I", data[0:4])[0]
        if scaler == 0x00010000:
            num_tables = struct.unpack(">H", data[4:6])[0]
            offset = 12
            for _ in range(num_tables):
                if offset + 16 > len(data):
                    break
                tag = data[offset:offset + 4].decode("ascii", errors="ignore")
                rec_off = struct.unpack(">I", data[offset + 8:offset + 12])[0]
                if tag == "name":
                    if rec_off + 6 > len(data):
                        return None
                    count = struct.unpack(">H", data[rec_off + 2 : rec_off + 4])[0]
                    stroff = struct.unpack(">H", data[rec_off + 4 : rec_off + 6])[0] + rec_off
                    pos = rec_off + 6
                    for _i in range(count):
                        if pos + 12 > len(data):
                            return None
                        platform_id = struct.unpack(">H", data[pos : pos + 2])[0]
                        encoding_id = struct.unpack(">H", data[pos + 2 : pos + 4])[0]
                        name_id = struct.unpack(">H", data[pos + 6 : pos + 8])[0]
                        length = struct.unpack(">H", data[pos + 8 : pos + 10])[0]
                        string_off = struct.unpack(">H", data[pos + 10 : pos + 12])[0]
                        pos += 12
                        if name_id != 1:
                            continue
                        s_pos = stroff + string_off
                        s_bytes = data[s_pos : s_pos + length]

                        # Common cases: UCS-2BE (platform 3 encodings), or Macintosh Roman (ASCII-ish)
                        try:
                            if platform_id == 3:  # Windows
                                return s_bytes.decode("utf_16_be", errors="ignore").strip() or None
                            if platform_id == 1:  # Macintosh
                                return s_bytes.decode("latin-1", errors="ignore").strip() or None
                            # Fallback
                            return s_bytes.decode("utf_16_be", errors="ignore").strip() or None
                        except Exception:
                            return None
                    return None
                offset += 16
            return None
        return None
    except Exception:
        return None


def _finalize_downloaded_font(font_path: str, fallback_family_from_css: str, filename_for_fallback: str) -> tuple[str, str | None]:
    internal = _read_ttf_name_family(font_path)
    if internal:
        return internal, "fonts"
    fallback = (fallback_family_from_css or "").strip() or os.path.splitext(filename_for_fallback)[0]
    return fallback, "fonts"


async def prepare_google_font_for_subtitles(css_url: str, dest_dir: str) -> tuple[str, str | None]:
    """
    Returns (font_family_name_for_ass, fontsdir_relative_or_none).
    Downloads a .ttf into dest_dir/fonts and points libass via ffmpeg subtitles=...:fontsdir=fonts

    Raises ValueError with a user-actionable message on failure.
    """
    stripped = _normalize_google_font_input(css_url.strip())
    if not stripped:
        raise ValueError("Empty google_font_css_url")

    _assert_allowed_font_url(stripped)
    parsed_css = urllib.parse.urlparse(stripped)
    hostname = (parsed_css.hostname or "").lower()
    os.makedirs(dest_dir, exist_ok=True)
    fonts_subdir = os.path.join(dest_dir, "fonts")
    os.makedirs(fonts_subdir, exist_ok=True)

    async with httpx.AsyncClient(follow_redirects=True) as client:
        # Direct font file shortcut (often easiest / most reliable for FFmpeg).
        if hostname == "fonts.gstatic.com" and stripped.lower().endswith(".ttf"):
            font_bytes = await _download_bytes(client, stripped)
            digest = hashlib.sha256(font_bytes).hexdigest()[:12]
            filename = os.path.basename(parsed_css.path) or f"{digest}.ttf"
            if not filename.lower().endswith(".ttf"):
                filename = f"{digest}.ttf"
            font_path = os.path.join(fonts_subdir, filename)
            with open(font_path, "wb") as f:
                f.write(font_bytes)
            font_name, fontsdir_rel = _finalize_downloaded_font(font_path, fallback_family_from_css="", filename_for_fallback=filename)
            return font_name, fontsdir_rel

        if hostname != "fonts.googleapis.com":
            raise ValueError(
                "Paste either:\n"
                "- a Google Fonts **CSS** link from fonts.googleapis.com, or\n"
                "- a direct **.ttf** link from fonts.gstatic.com"
            )

        # Google's CSS differs by User-Agent; this UA tends to yield legacy stacks that include usable TTF.
        css_headers = {
            "User-Agent": _USER_AGENT_CSS,
            "Accept": "text/css,*/*;q=0.1",
        }
        css_bytes = await _download_bytes(client, stripped, headers=css_headers)
        css_text = css_bytes.decode("utf-8", errors="replace")

        blocks = _extract_font_face_blocks(css_text)
        ttf_url = None
        family = ""
        for b in blocks:
            u = _pick_ttf_url_from_src(b["src"], stripped)
            if u:
                ttf_url = u
                family = b["family"] or ""
                break

        if not ttf_url:
            raise ValueError(
                "Could not find a downloadable .ttf in the Google Fonts CSS response "
                "(Google may have only returned woff2). "
                "Try a different Google Font, or paste a direct **.ttf** URL from fonts.gstatic.com "
                '(often visible as `url(https://fonts.gstatic.com/.../*.ttf)` in the CSS response).'
            )

        font_bytes = await _download_bytes(client, ttf_url)
        digest = hashlib.sha256(font_bytes).hexdigest()[:12]
        filename = os.path.basename(urllib.parse.urlparse(ttf_url).path) or "font.ttf"
        if not filename.lower().endswith(".ttf"):
            filename = f"{digest}.ttf"
        font_path = os.path.join(fonts_subdir, filename)
        with open(font_path, "wb") as f:
            f.write(font_bytes)

    font_name, fontsdir_rel = _finalize_downloaded_font(font_path, fallback_family_from_css=family, filename_for_fallback=filename)
    return font_name, fontsdir_rel


def format_srt_time(seconds):
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"

def _format_ass_time(seconds: float) -> str:
    # ASS time format: H:MM:SS.CC (centiseconds)
    if seconds < 0:
        seconds = 0.0
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    cs = int((seconds % 1) * 100)  # centiseconds
    return f"{hrs}:{mins:02d}:{secs:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    # Basic escaping for libass
    return (
        text.replace("\\", r"\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("\n", r"\N")
    )


def create_srt(text, duration, srt_path):
    words = text.split()
    if not words: return
    chunks = []
    for index in range(0, len(words), 7):
        chunks.append(" ".join(words[index:index + 7]))
    chunk_duration = duration / len(chunks)
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, chunk in enumerate(chunks):
            f.write(f"{i+1}\n{format_srt_time(i * chunk_duration)} --> {format_srt_time((i + 1) * chunk_duration)}\n{chunk}\n\n")

def create_ass(
    text: str,
    duration: float,
    ass_path: str,
    font_name_override: str | None = None,
    font_size_override: int | None = None,
    margin_v_override: int | None = None,
):
    """
    ASS subtitles let us control font and add simple per-line animation.
    We generate evenly-timed chunks (same as SRT), and apply a subtle fade to each line.
    """
    words = text.split()
    if not words:
        return

    chunks = []
    for index in range(0, len(words), 7):
        chunks.append(" ".join(words[index:index + 7]))
    chunk_duration = duration / len(chunks)

    font_name = (font_name_override or os.getenv("VOICELAB_SUB_FONT", "Arial")).strip() or "Arial"
    font_size = font_size_override if font_size_override is not None else int(os.getenv("VOICELAB_SUB_FONT_SIZE", "46"))
    margin_v = margin_v_override if margin_v_override is not None else int(os.getenv("VOICELAB_SUB_MARGIN_V", "140"))

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,2,0,2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(header)
        for i, chunk in enumerate(chunks):
            start = i * chunk_duration
            end = (i + 1) * chunk_duration
            # Fade in/out (ms). Keep subtle so it feels premium, not flashy.
            line = _ass_escape(chunk)
            text_with_fx = r"{\fad(180,220)}" + line
            f.write(
                f"Dialogue: 0,{_format_ass_time(start)},{_format_ass_time(end)},Default,,0,0,0,,{text_with_fx}\n"
            )


def split_script_scenes(script: str) -> list[str]:
    return [ln.strip() for ln in script.splitlines() if ln.strip()]


def _scene_image_prompt(mood: str, line: str, image_description: str | None) -> str:
    style = ""
    if image_description and image_description.strip():
        style = f"{image_description.strip()}, "
    return (
        f"Professional cinematic vertical 9:16 photography, {style}{mood} mood, "
        f"scene depicting: {line[:200]}, high detail, 4k, photorealistic, "
        f"aesthetic composition, dramatic lighting, depth of field"
    )


async def _fetch_pollinations_image(client: httpx.AsyncClient, prompt: str, dest_path: str) -> None:
    encoded_prompt = urllib.parse.quote(prompt)
    seed = uuid.uuid4().int % 10000
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=720&height=1280&nologo=true&seed={seed}"
    resp = await client.get(url, timeout=90.0)
    if resp.status_code != 200:
        raise Exception("Image generation failed")
    with open(dest_path, "wb") as f:
        f.write(resp.content)


def create_ass_scenes(
    lines: list[str],
    durations: list[float],
    ass_path: str,
    font_name_override: str | None = None,
    font_size_override: int | None = None,
    margin_v_override: int | None = None,
):
    """One subtitle line per scene, timed to each segment's narration duration."""
    if not lines or len(lines) != len(durations):
        return

    font_name = (font_name_override or os.getenv("VOICELAB_SUB_FONT", "Arial")).strip() or "Arial"
    font_size = font_size_override if font_size_override is not None else int(os.getenv("VOICELAB_SUB_FONT_SIZE", "46"))
    margin_v = margin_v_override if margin_v_override is not None else int(os.getenv("VOICELAB_SUB_MARGIN_V", "140"))

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,2,0,2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    t = 0.0
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(header)
        for line, dur in zip(lines, durations):
            start = t
            end = t + dur
            t = end
            text_with_fx = r"{\fad(180,220)}" + _ass_escape(line)
            f.write(
                f"Dialogue: 0,{_format_ass_time(start)},{_format_ass_time(end)},Default,,0,0,0,,{text_with_fx}\n"
            )


def _write_concat_list(filenames: list[str], list_path: str) -> None:
    with open(list_path, "w", encoding="utf-8") as f:
        for name in filenames:
            # Use forward slashes so FFmpeg concat demuxer works on Windows.
            safe = name.replace("\\", "/")
            f.write(f"file '{safe}'\n")


def _concat_audio_segments(segment_names: list[str], output_name: str, temp_dir: str) -> None:
    list_path = os.path.join(temp_dir, "audio_concat.txt")
    _write_concat_list(segment_names, list_path)
    # Re-encode: edge-tts MP3 segments often fail with stream copy concat.
    _run_ffmpeg([
        "ffmpeg", "-f", "concat", "-safe", "0", "-i", "audio_concat.txt",
        "-c:a", "aac", "-b:a", "128k", "-y", output_name,
    ], temp_dir)


def _concat_video_segments(segment_names: list[str], output_name: str, temp_dir: str) -> None:
    list_path = os.path.join(temp_dir, "video_concat.txt")
    _write_concat_list(segment_names, list_path)
    _run_ffmpeg([
        "ffmpeg", "-f", "concat", "-safe", "0", "-i", "video_concat.txt",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-pix_fmt", "yuv420p", "-an", "-y", output_name,
    ], temp_dir)


def _zoompan_filter(motion: bool, total_frames: int, fps: int) -> str:
    if motion:
        return (
            f"zoompan=z='min(zoom+0.0008,1.15)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={total_frames}:fps={fps}:s=720x1280"
        )
    return f"zoompan=z=1:d={total_frames}:fps={fps}:s=720x1280"


def _render_scene_clip(
    image_name: str,
    duration: float,
    motion: bool,
    out_name: str,
    temp_dir: str,
    fps: int = 30,
) -> None:
    total_frames = max(1, int(duration * fps))
    vf = ",".join([
        "scale=720:1280:force_original_aspect_ratio=increase",
        "crop=720:1280",
        _zoompan_filter(motion, total_frames, fps),
    ])
    _run_ffmpeg([
        "ffmpeg", "-loop", "1", "-i", image_name,
        "-vf", vf, "-t", str(duration), "-r", str(fps),
        "-pix_fmt", "yuv420p", "-an",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-threads", "1",
        "-y", out_name,
    ], temp_dir)


async def _prepare_scene(
    index: int,
    line: str,
    client: httpx.AsyncClient,
    temp_dir: str,
    mood: str,
    voice: str,
    rate: str,
    pitch: str,
    image_description: str | None,
) -> tuple[int, str, str, float]:
    image_name = f"scene_{index}.jpg"
    audio_name = f"seg_{index}.mp3"
    image_path = os.path.join(temp_dir, image_name)
    audio_path = os.path.join(temp_dir, audio_name)

    async with _scene_prep_sem():
        prompt = _scene_image_prompt(mood, line, image_description)
        communicate = edge_tts.Communicate(line, voice, rate=rate, pitch=pitch)
        await asyncio.gather(
            _fetch_pollinations_image(client, prompt, image_path),
            communicate.save(audio_path),
        )
    duration = get_audio_duration(audio_path, line)
    return index, image_name, audio_name, duration


async def _build_multi_scene_reel(
    request: ReelRequest,
    scenes: list[str],
    temp_dir: str,
    output_path: str,
) -> None:
    _ensure_ffmpeg_tools()
    rate = request.rate if request.rate else "-10%"
    pitch = request.pitch if request.pitch else "-5Hz"
    fps = 30

    async with httpx.AsyncClient() as client:
        prepared = await asyncio.gather(*[
            _prepare_scene(
                i, line, client, temp_dir, request.mood, request.voice,
                rate, pitch, request.image_description,
            )
            for i, line in enumerate(scenes)
        ])

    prepared.sort(key=lambda x: x[0])
    audio_files = [p[2] for p in prepared]
    durations = [p[3] for p in prepared]

    _concat_audio_segments(audio_files, "narration.m4a", temp_dir)

    ass_path = os.path.join(temp_dir, "subtitles.ass")
    font_override: str | None = None
    fontsdir_opt = ""
    gf = (request.google_font_css_url or "").strip()
    if gf:
        font_override, fontsdir_rel = await prepare_google_font_for_subtitles(gf, temp_dir)
        if fontsdir_rel:
            fontsdir_opt = f":fontsdir={fontsdir_rel}"

    create_ass_scenes(scenes, durations, ass_path, font_name_override=font_override)

    clip_names: list[str] = []
    for i, dur in enumerate(durations):
        clip_name = f"clip_{i}.mp4"
        _render_scene_clip(f"scene_{i}.jpg", dur, request.motion, clip_name, temp_dir, fps)
        clip_names.append(clip_name)

    _concat_video_segments(clip_names, "video_only.mp4", temp_dir)

    subtitles_filter = f"subtitles=subtitles.ass{fontsdir_opt}"
    _run_ffmpeg([
        "ffmpeg", "-i", "video_only.mp4", "-i", "narration.m4a",
        "-vf", subtitles_filter,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-threads", "1",
        "-c:a", "aac", "-b:a", "96k", "-pix_fmt", "yuv420p", "-shortest",
        "-y", "reel.mp4",
    ], temp_dir)


async def _build_single_scene_reel(
    request: ReelRequest,
    script: str,
    temp_dir: str,
    output_path: str,
) -> None:
    _ensure_ffmpeg_tools()
    audio_path = os.path.join(temp_dir, "narration.mp3")
    image_path = os.path.join(temp_dir, "background.jpg")
    ass_path = os.path.join(temp_dir, "subtitles.ass")

    if request.image_description and request.image_description.strip():
        refined_prompt = (
            f"Professional cinematic vertical 9:16 photography, {request.image_description}, "
            f"high detail, 4k, photorealistic, aesthetic composition"
        )
    else:
        refined_prompt = (
            f"Professional cinematic vertical 9:16 photography, {request.mood} mood, "
            f"{script[:120]}, high detail, 4k, photorealistic, aesthetic composition, "
            f"dramatic lighting, depth of field"
        )

    rate = request.rate if request.rate else "-10%"
    pitch = request.pitch if request.pitch else "-5Hz"

    async with httpx.AsyncClient() as client:
        await asyncio.gather(
            _fetch_pollinations_image(client, refined_prompt, image_path),
            edge_tts.Communicate(script, request.voice, rate=rate, pitch=pitch).save(audio_path),
        )

    duration = get_audio_duration(audio_path, script)

    font_override: str | None = None
    fontsdir_opt = ""
    gf = (request.google_font_css_url or "").strip()
    if gf:
        font_override, fontsdir_rel = await prepare_google_font_for_subtitles(gf, temp_dir)
        if fontsdir_rel:
            fontsdir_opt = f":fontsdir={fontsdir_rel}"

    create_ass(script, duration, ass_path, font_name_override=font_override)
    subtitles_filter = f"subtitles=subtitles.ass{fontsdir_opt}"

    fps = 30
    total_frames = max(1, int(duration * fps))
    filters = [
        "scale=720:1280:force_original_aspect_ratio=increase",
        "crop=720:1280",
        _zoompan_filter(request.motion, total_frames, fps),
        subtitles_filter,
    ]
    video_filter = ",".join(filters)

    _run_ffmpeg([
        "ffmpeg", "-loop", "1", "-i", "background.jpg", "-i", "narration.mp3",
        "-vf", video_filter, "-shortest", "-t", str(duration),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-threads", "1",
        "-c:a", "aac", "-b:a", "96k", "-pix_fmt", "yuv420p", "-y", "reel.mp4",
    ], temp_dir)


@app.get("/voices")
async def voices(x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    _require_api_key(x_api_key)
    try:
        names = await _get_voice_names()
        return {"voices": sorted(names)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/")
def root():
    return {"status": "Reels TTS generator backend is running"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/tts")
async def tts(
    request: TTSRequest,
    background_tasks: BackgroundTasks,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    _require_api_key(x_api_key)
    if not request.text.strip():
        return JSONResponse({"error": "Text is required"}, status_code=400)
    temp_dir = tempfile.mkdtemp()
    filename = os.path.join(temp_dir, "tts.mp3")
    try:
        communicate = edge_tts.Communicate(request.text, request.voice)
        await communicate.save(filename)
        background_tasks.add_task(cleanup_files, temp_dir)
        return FileResponse(filename, media_type="audio/mpeg", filename="tts.mp3")
    except Exception as e:
        cleanup_files(temp_dir)
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/clone-tts")
async def clone_tts(
    request: CloneTTSRequest,
    background_tasks: BackgroundTasks,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    _require_api_key(x_api_key)
    if not request.text.strip():
        return JSONResponse({"error": "Text is required"}, status_code=400)
    if not request.speaker_id.strip():
        return JSONResponse({"error": "speaker_id is required"}, status_code=400)
    temp_dir = tempfile.mkdtemp()
    filename = os.path.join(temp_dir, "clone.mp3")
    try:
        # Pragmatic "clone": treat speaker_id as an Edge TTS voice ShortName.
        voice = request.speaker_id.strip()
        voice_names = await _get_voice_names()
        if voice not in voice_names:
            return JSONResponse(
                {
                    "error": "Unknown speaker_id/voice. Use a valid Edge voice ShortName.",
                    "example": "en-US-ChristopherNeural",
                },
                status_code=400,
            )

        communicate = edge_tts.Communicate(request.text, voice)
        await communicate.save(filename)
        background_tasks.add_task(cleanup_files, temp_dir)
        return FileResponse(filename, media_type="audio/mpeg", filename="clone_tts.mp3")
    except Exception as e:
        cleanup_files(temp_dir)
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/reel")
async def create_reel(
    request: ReelRequest,
    background_tasks: BackgroundTasks,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    _require_api_key(x_api_key)
    if not request.script.strip():
        return JSONResponse({"error": "Script is required"}, status_code=400)

    scenes = split_script_scenes(request.script)
    if len(scenes) > MAX_REEL_SCENES:
        return JSONResponse(
            {
                "error": (
                    f"Multi-scene reels support up to {MAX_REEL_SCENES} lines "
                    f"(one AI image per line). You have {len(scenes)}."
                ),
            },
            status_code=400,
        )

    multi = len(scenes) >= MIN_SCENES_FOR_MULTI
    print(f"DEBUG: Creating reel. scenes={len(scenes)} multi={multi} motion={request.motion}")

    temp_dir = tempfile.mkdtemp()
    output_path = os.path.join(temp_dir, "reel.mp4")

    try:
        if multi:
            await _build_multi_scene_reel(request, scenes, temp_dir, output_path)
        else:
            script = scenes[0] if scenes else request.script.strip()
            await _build_single_scene_reel(request, script, temp_dir, output_path)

        background_tasks.add_task(cleanup_files, temp_dir)
        return FileResponse(output_path, media_type="video/mp4", filename="reel.mp4")

    except ValueError as e:
        cleanup_files(temp_dir)
        return JSONResponse({"error": str(e)}, status_code=400)
    except FileNotFoundError as e:
        cleanup_files(temp_dir)
        return JSONResponse({"error": str(e)}, status_code=500)
    except Exception as e:
        traceback.print_exc()
        cleanup_files(temp_dir)
        return JSONResponse({"error": str(e)}, status_code=500)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
