"""
Audio Transcription Service — OpenAI Whisper API
$0.006/minute. 95%+ accuracy. 57 languages.
"""
import time
import logging
from pathlib import Path
from app.core.config import settings

logger = logging.getLogger("docuaction.audio")

async def transcribe_audio_file(file_path: str, language: str = None) -> dict:
    """
    Transcribe audio file using OpenAI Whisper API.
    Returns: {text, word_count, language, duration_seconds, cost_usd, model, processing_time_ms, segments}
    """
    import httpx

    start = time.time()
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {file_path}")

    file_bytes = path.read_bytes()
    file_size_mb = len(file_bytes) / (1024 * 1024)
    logger.info(f"Transcribing: {path.name} ({file_size_mb:.1f} MB)")

    # Estimate duration from file size (rough: ~1MB per minute for MP3)
    est_minutes = max(file_size_mb * 1.0, 0.5)

    # Build multipart form
    ext = path.suffix.lower().replace(".", "")
    mime_types = {"mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4", "webm": "audio/webm", "ogg": "audio/ogg"}
    mime = mime_types.get(ext, "audio/mpeg")

    data = {"model": settings.WHISPER_MODEL, "response_format": "verbose_json"}
    if language:
        data["language"] = language

    files = {"file": (path.name, file_bytes, mime)}

    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
            data=data,
            files=files,
        )

    if response.status_code != 200:
        raise Exception(f"Whisper API error {response.status_code}: {response.text[:200]}")

    result = response.json()
    processing_ms = int((time.time() - start) * 1000)

    text = result.get("text", "")
    duration = result.get("duration", est_minutes * 60)
    detected_lang = result.get("language", "en")
    cost = (duration / 60) * 0.006

    # Extract segments for speaker timestamps
    segments = []
    for seg in result.get("segments", []):
        segments.append({
            "start": seg.get("start", 0),
            "end": seg.get("end", 0),
            "text": seg.get("text", "").strip(),
        })

    logger.info(f"Transcribed: {len(text.split())} words, {duration:.0f}s, ${cost:.4f}, {processing_ms}ms")

    return {
        "text": text,
        "word_count": len(text.split()),
        "language": detected_lang,
        "duration_seconds": duration,
        "cost_usd": round(cost, 4),
        "model": settings.WHISPER_MODEL,
        "processing_time_ms": processing_ms,
        "segments": segments,
    }

def estimate_cost(file_size_bytes: int) -> dict:
    """Estimate transcription cost before processing."""
    minutes = max((file_size_bytes / (1024 * 1024)) * 1.0, 0.5)
    return {
        "estimated_minutes": round(minutes, 1),
        "estimated_cost_usd": round(minutes * 0.006, 4),
        "rate_per_minute": 0.006,
        "model": "whisper-1",
    }
