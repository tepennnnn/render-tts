import os
import uuid
import edge_tts
from fastapi import FastAPI
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

@app.get("/")
def root():
    return {"status": "VoiceLab backend is running"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/tts")
async def tts(request: TTSRequest):
    if not request.text.strip():
        return JSONResponse({"error": "Text is required"}, status_code=400)

    filename = f"/tmp/{uuid.uuid4()}.mp3"

    communicate = edge_tts.Communicate(request.text, request.voice)
    await communicate.save(filename)

    return FileResponse(
        filename,
        media_type="audio/mpeg",
        filename="tts.mp3"
    )