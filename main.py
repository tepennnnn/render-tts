import os
import uuid
import subprocess
import urllib.parse
import shutil
import tempfile
import httpx
import edge_tts
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="VoiceLab TTS Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class TTSRequest(BaseModel):
    text: str
    voice: str = "en-US-GuyNeural"

class ReelRequest(BaseModel):
    script: str
    mood: str
    voice: str = "en-US-GuyNeural"

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

def get_audio_duration(file_path, fallback_text=""):
    try:
        cmd = [
            'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', file_path
        ]
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            return float(result.stdout.strip())
        print(f"ffprobe failed: {result.stderr or result.stdout}")
    except FileNotFoundError:
        print("ffprobe is not installed or is not available on PATH")
    except subprocess.TimeoutExpired:
        print("ffprobe timed out")
    except Exception as e:
        print(f"Error getting duration: {e}")
    word_count = len(fallback_text.split())
    return max(3.0, word_count / 2.5) if word_count else 10.0

def format_srt_time(seconds):
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"

def create_srt(text, duration, srt_path):
    words = text.split()
    if not words:
        return

    chunks = []
    for index in range(0, len(words), 7):
        chunk_words = words[index:index + 7]
        if chunks and len(chunk_words) < 5:
            previous_words = chunks[-1].split()
            if len(previous_words) + len(chunk_words) <= 8:
                chunks[-1] = " ".join(previous_words + chunk_words)
                continue
        chunks.append(" ".join(chunk_words))

    chunk_duration = duration / len(chunks)

    with open(srt_path, "w", encoding="utf-8") as f:
        for i, chunk in enumerate(chunks):
            start_time = i * chunk_duration
            end_time = (i + 1) * chunk_duration
            f.write(f"{i+1}\n")
            f.write(f"{format_srt_time(start_time)} --> {format_srt_time(end_time)}\n")
            f.write(f"{chunk}\n\n")

def build_image_prompt(text, mood):
    return (
        "vertical 9:16 cinematic motivational poster, dramatic lighting, "
        f"no text, no watermark, ultra detailed, mood: {mood}, concept: {text}"
    )

def ffmpeg_error_tail(stderr):
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    return "\n".join(lines[-12:]) if lines else "Unknown FFmpeg error"

@app.get("/")
def root():
    return {"status": "VoiceLab backend is running"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/tts")
async def tts(request: TTSRequest, background_tasks: BackgroundTasks):
    if not request.text.strip():
        return JSONResponse({"error": "Text is required"}, status_code=400)

    temp_dir = tempfile.mkdtemp(prefix=f"tts-{uuid.uuid4()}-")
    filename = os.path.join(temp_dir, "tts.mp3")

    try:
        communicate = edge_tts.Communicate(request.text, request.voice)
        await communicate.save(filename)
        background_tasks.add_task(cleanup_files, temp_dir)
        return FileResponse(filename, media_type="audio/mpeg", filename="tts.mp3")
    except Exception as e:
        cleanup_files(temp_dir)
        error_detail = str(e)
        print(f"Error in /tts endpoint: {error_detail}")
        return JSONResponse({"error": f"TTS generation failed: {error_detail}"}, status_code=500)

@app.post("/reel")
async def create_reel(request: ReelRequest, background_tasks: BackgroundTasks):
    print(f"Received reel request: script={request.script}, mood={request.mood}, voice={request.voice}")
    
    if not request.script.strip():
        return JSONResponse({"error": "Script is required"}, status_code=400)
    if not request.mood.strip():
        return JSONResponse({"error": "Mood is required"}, status_code=400)
    if not request.voice.strip():
        return JSONResponse({"error": "Voice is required"}, status_code=400)

    job_id = str(uuid.uuid4())
    temp_dir = tempfile.mkdtemp(prefix=f"reel-{job_id}-")
    audio_path = os.path.join(temp_dir, "narration.mp3")
    image_path = os.path.join(temp_dir, "background.jpg")
    srt_path = os.path.join(temp_dir, "subtitles.srt")
    output_path = os.path.join(temp_dir, "reel.mp4")

    try:
        # 1. Build prompt and download image from Pollinations.
        prompt = build_image_prompt(request.script, request.mood)
        encoded_prompt = urllib.parse.quote(prompt)
        image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1080&height=1920&nologo=true"

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(image_url, timeout=90.0)
                resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            detail = e.response.text[:300] if e.response.text else "No response body"
            raise HTTPException(
                status_code=502,
                detail=f"Pollinations image generation failed with status {status_code}: {detail}",
            )
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=502,
                detail=f"Could not reach Pollinations image service: {e}",
            )

        content_type = resp.headers.get("content-type", "")
        if "image" not in content_type.lower():
            raise HTTPException(
                status_code=502,
                detail=f"Pollinations did not return an image. Content-Type: {content_type or 'unknown'}",
            )

        with open(image_path, "wb") as f:
            f.write(resp.content)

        # 2. Generate TTS narration.
        communicate = edge_tts.Communicate(request.script, request.voice)
        await communicate.save(audio_path)

        if not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
            raise HTTPException(status_code=500, detail="TTS generation produced an empty audio file")

        # 3. Estimate subtitle timing from the generated audio.
        duration = get_audio_duration(audio_path, request.script)

        # 4. Create SRT subtitles.
        create_srt(request.script, duration, srt_path)

        # 5. Create vertical MP4 with a still background, narration, and burned subtitles.
        subtitle_filter = (
            "subtitles=subtitles.srt:"
            "force_style='Alignment=2,FontSize=64,MarginV=120,"
            "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
            "BorderStyle=1,Outline=3,Shadow=1'"
        )
        video_filter = (
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,"
            f"{subtitle_filter}"
        )

        cmd = [
            'ffmpeg', '-loop', '1', '-framerate', '30', '-i', 'background.jpg', '-i', 'narration.mp3',
            '-vf', video_filter,
            '-c:v', 'libx264', '-tune', 'stillimage', '-c:a', 'aac', '-b:a', '192k',
            '-pix_fmt', 'yuv420p', '-r', '30', '-shortest', '-movflags', '+faststart',
            '-y', 'reel.mp4'
        ]

        try:
            process = subprocess.run(
                cmd,
                cwd=temp_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except FileNotFoundError:
            raise HTTPException(
                status_code=500,
                detail="FFmpeg is not installed or is not available on PATH",
            )

        if process.returncode != 0:
            error_msg = ffmpeg_error_tail(process.stderr)
            print(f"FFmpeg error: {error_msg}")
            raise HTTPException(status_code=500, detail=f"Video processing failed: {error_msg}")

        background_tasks.add_task(cleanup_files, temp_dir)

        return FileResponse(
            output_path,
            media_type="video/mp4",
            filename="reel.mp4"
        )

    except Exception as e:
        cleanup_files(temp_dir)
        if isinstance(e, HTTPException):
            raise e
        error_detail = str(e)
        print(f"Error in /reel endpoint: {error_detail}")
        return JSONResponse({"error": f"Reel generation failed: {error_detail}"}, status_code=500)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
