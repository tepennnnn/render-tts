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

class CloneTTSRequest(BaseModel):
    text: str
    speaker_id: str

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
    # Path to local ffprobe
    ffprobe_path = os.path.join(os.getcwd(), "ffmpeg_bin", "ffprobe")
    if not os.path.exists(ffprobe_path):
        ffprobe_path = 'ffprobe' # Fallback to system path

    try:
        cmd = [
            ffprobe_path, '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', file_path
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
        if result.returncode == 0:
            return float(result.stdout.strip())
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
    if not words: return
    chunks = []
    for index in range(0, len(words), 7):
        chunks.append(" ".join(words[index:index + 7]))
    chunk_duration = duration / len(chunks)
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, chunk in enumerate(chunks):
            f.write(f"{i+1}\n{format_srt_time(i * chunk_duration)} --> {format_srt_time((i + 1) * chunk_duration)}\n{chunk}\n\n")

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
async def clone_tts(request: CloneTTSRequest, background_tasks: BackgroundTasks):
    if not request.text.strip():
        return JSONResponse({"error": "Text is required"}, status_code=400)
    temp_dir = tempfile.mkdtemp()
    filename = os.path.join(temp_dir, "clone.mp3")
    try:
        # Using a standard voice as fallback for cloning
        communicate = edge_tts.Communicate(request.text, "en-US-ChristopherNeural")
        await communicate.save(filename)
        background_tasks.add_task(cleanup_files, temp_dir)
        return FileResponse(filename, media_type="audio/mpeg", filename="clone_tts.mp3")
    except Exception as e:
        cleanup_files(temp_dir)
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/reel")
async def create_reel(request: ReelRequest, background_tasks: BackgroundTasks):
    if not request.script.strip():
        return JSONResponse({"error": "Script is required"}, status_code=400)

    temp_dir = tempfile.mkdtemp()
    audio_path = os.path.join(temp_dir, "narration.mp3")
    image_path = os.path.join(temp_dir, "background.jpg")
    srt_path = os.path.join(temp_dir, "subtitles.srt")
    output_path = os.path.join(temp_dir, "reel.mp4")

    try:
        # 1. Image Generation (Pollinations)
        prompt = f"vertical 9:16 cinematic motivational, mood: {request.mood}, {request.script[:100]}"
        encoded_prompt = urllib.parse.quote(prompt)
        async with httpx.AsyncClient() as client:
            # Reduced resolution for memory efficiency (720x1280 instead of 1080x1920)
            resp = await client.get(f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=720&height=1280&nologo=true", timeout=60.0)
            if resp.status_code != 200: raise Exception("Image generation failed")
            with open(image_path, "wb") as f: f.write(resp.content)

        # 2. TTS Generation
        communicate = edge_tts.Communicate(request.script, request.voice)
        await communicate.save(audio_path)
        duration = get_audio_duration(audio_path, request.script)

        # 3. Subtitles (SRT)
        create_srt(request.script, duration, srt_path)

        # 4. Video Assembly (FFmpeg)
        # Optimized for Render Free Tier (512MB RAM)
        ffmpeg_path = os.path.join(os.getcwd(), "ffmpeg_bin", "ffmpeg")
        if not os.path.exists(ffmpeg_path):
            ffmpeg_path = 'ffmpeg' # Fallback to system path

        video_filter = (
            "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,"
            "subtitles=subtitles.srt:force_style='Alignment=2,FontSize=20,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=1'"
        )
        cmd = [
            ffmpeg_path, '-loop', '1', '-i', 'background.jpg', '-i', 'narration.mp3',
            '-vf', video_filter, '-shortest',
            '-c:v', 'libx264', '-preset', 'ultrafast', # 'ultrafast' uses less RAM/CPU
            '-crf', '28', # Higher CRF = lower quality = lower memory
            '-threads', '1', # Limit to 1 thread to save memory
            '-c:a', 'aac', '-b:a', '96k',
            '-pix_fmt', 'yuv420p', '-y', 'reel.mp4'
        ]

        # Don't capture output in memory (save RAM)
        process = subprocess.run(cmd, cwd=temp_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if process.returncode != 0:
            raise Exception("FFmpeg failed to generate video.")

        background_tasks.add_task(cleanup_files, temp_dir)
        return FileResponse(output_path, media_type="video/mp4", filename="reel.mp4")

    except Exception as e:
        cleanup_files(temp_dir)
        return JSONResponse({"error": str(e)}, status_code=500)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
