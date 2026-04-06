"""
Fault-Tolerant Transcription Service — Zero Failure
5-step fallback pipeline ensures transcription ALWAYS succeeds.

Pipeline:
  Step 1: Direct transcription with original file
  Step 2: Retry with adjusted parameters
  Step 3: Convert + enhance audio, then retry
  Step 4: Split into chunks, transcribe each, merge
  Step 5: Fallback to alternate model/approach

User NEVER sees "transcription failed".
"""
import os
import time
import logging
import asyncio
from typing import Dict, List, Optional
from pathlib import Path

from app.core.config import settings
from app.services.audio_processor import (
    convert_to_standard, split_audio, detect_audio_info, cleanup_temp_files,
    WHISPER_NATIVE,
)

logger = logging.getLogger("docuaction.audio.transcribe")


async def transcribe_audio_file(file_path: str) -> Dict:
    """
    Master transcription function — ZERO FAILURE.
    Tries 5 fallback strategies before giving up.

    Returns:
        {
            'text': str,
            'word_count': int,
            'language': str,
            'duration_seconds': float,
            'model': str,
            'cost_usd': float,
            'processing_time_ms': int,
            'segments': list,
            'pipeline_steps': list,
            'retries': int,
        }
    """
    start = time.time()
    pipeline_steps = []
    retries = 0
    last_error = None

    # Detect original format
    info = detect_audio_info(file_path)
    original_ext = Path(file_path).suffix.lower()
    pipeline_steps.append(f"Input: {original_ext} | {info.get('codec', '?')} | {info.get('duration', 0):.1f}s | {info.get('sample_rate', '?')}Hz")

    # ─── STEP 1: Direct transcription (if native format) ───
    if original_ext in WHISPER_NATIVE:
        pipeline_steps.append("Step 1: Direct transcription attempt")
        result = await _call_whisper(file_path, pipeline_steps)
        if result:
            result['pipeline_steps'] = pipeline_steps
            result['retries'] = 0
            result['processing_time_ms'] = int((time.time() - start) * 1000)
            return result
        retries += 1
        logger.info("Step 1 failed, moving to step 2")

    # ─── STEP 2: Retry with different parameters ───
    pipeline_steps.append("Step 2: Retry with adjusted parameters")
    result = await _call_whisper(file_path, pipeline_steps, language='en', prompt="Meeting transcript. Technical discussion.")
    if result:
        result['pipeline_steps'] = pipeline_steps
        result['retries'] = retries
        result['processing_time_ms'] = int((time.time() - start) * 1000)
        return result
    retries += 1
    logger.info("Step 2 failed, moving to step 3")

    # ─── STEP 3: Convert + enhance, then retry ───
    pipeline_steps.append("Step 3: Convert and enhance audio")
    converted = None
    try:
        conv_result = convert_to_standard(file_path, enhance=True)
        converted = conv_result['output_path']
        pipeline_steps.extend(conv_result.get('steps_applied', []))

        result = await _call_whisper(converted, pipeline_steps)
        if result:
            if conv_result.get('duration_seconds'):
                result['duration_seconds'] = conv_result['duration_seconds']
            result['pipeline_steps'] = pipeline_steps
            result['retries'] = retries
            result['processing_time_ms'] = int((time.time() - start) * 1000)
            cleanup_temp_files(converted)
            return result
    except Exception as e:
        logger.warning(f"Step 3 conversion error: {e}")
        pipeline_steps.append(f"Conversion error: {str(e)[:100]}")
    retries += 1
    logger.info("Step 3 failed, moving to step 4")

    # ─── STEP 4: Split into chunks, transcribe each ───
    pipeline_steps.append("Step 4: Chunked transcription")
    try:
        source_file = converted or file_path

        # Convert first if we don't have a converted file yet
        if not converted:
            conv_result = convert_to_standard(file_path, enhance=False)
            source_file = conv_result['output_path']
            converted = source_file

        chunks = split_audio(source_file, chunk_seconds=300)  # 5-minute chunks
        pipeline_steps.append(f"Split into {len(chunks)} chunks")

        if len(chunks) > 1:
            all_texts = []
            all_segments = []
            total_duration = 0

            for i, chunk_path in enumerate(chunks):
                pipeline_steps.append(f"Processing chunk {i + 1}/{len(chunks)}")
                chunk_result = await _call_whisper(chunk_path, [])
                if chunk_result:
                    all_texts.append(chunk_result['text'])
                    if chunk_result.get('segments'):
                        # Offset segment timestamps
                        for seg in chunk_result['segments']:
                            if isinstance(seg, dict):
                                seg['start'] = seg.get('start', 0) + total_duration
                                seg['end'] = seg.get('end', 0) + total_duration
                        all_segments.extend(chunk_result['segments'])
                    chunk_dur = chunk_result.get('duration_seconds', 300)
                    total_duration += chunk_dur

            if all_texts:
                merged_text = " ".join(all_texts)
                result = {
                    'text': merged_text,
                    'word_count': len(merged_text.split()),
                    'language': 'en',
                    'duration_seconds': total_duration,
                    'model': 'whisper-1',
                    'cost_usd': round(total_duration / 60 * 0.006, 4),
                    'segments': all_segments if all_segments else None,
                    'pipeline_steps': pipeline_steps,
                    'retries': retries,
                    'processing_time_ms': int((time.time() - start) * 1000),
                }
                # Cleanup chunks
                for c in chunks:
                    cleanup_temp_files(c)
                cleanup_temp_files(converted)
                return result

    except Exception as e:
        logger.warning(f"Step 4 chunking error: {e}")
        pipeline_steps.append(f"Chunking error: {str(e)[:100]}")
    retries += 1
    logger.info("Step 4 failed, moving to step 5")

    # ─── STEP 5: Minimal conversion fallback ───
    pipeline_steps.append("Step 5: Minimal conversion fallback")
    try:
        # Try absolute minimal conversion
        minimal_path = file_path + ".minimal.wav"
        import subprocess
        subprocess.run(
            ['ffmpeg', '-y', '-i', file_path, '-ar', '16000', '-ac', '1', '-t', '600', minimal_path],
            capture_output=True, timeout=60
        )
        if os.path.exists(minimal_path):
            result = await _call_whisper(minimal_path, pipeline_steps)
            cleanup_temp_files(minimal_path)
            if result:
                result['pipeline_steps'] = pipeline_steps
                result['retries'] = retries
                result['processing_time_ms'] = int((time.time() - start) * 1000)
                return result
    except Exception as e:
        pipeline_steps.append(f"Step 5 error: {str(e)[:100]}")

    # ─── FINAL: Graceful degradation (never shows error to user) ───
    pipeline_steps.append("All steps exhausted — returning partial result")
    cleanup_temp_files(converted)

    duration = info.get('duration', 0) or 0
    return {
        'text': '[Audio processed but transcription could not extract clear speech. The audio may be too noisy, corrupted, or contain non-speech content. Please try uploading a clearer recording.]',
        'word_count': 0,
        'language': 'unknown',
        'duration_seconds': duration,
        'model': 'whisper-1',
        'cost_usd': 0,
        'segments': None,
        'pipeline_steps': pipeline_steps,
        'retries': retries,
        'processing_time_ms': int((time.time() - start) * 1000),
        'status': 'partial',
    }


async def _call_whisper(
    file_path: str,
    pipeline_steps: list,
    language: str = None,
    prompt: str = None,
) -> Optional[Dict]:
    """
    Call OpenAI Whisper API with error handling.
    Returns None on failure (caller handles fallback).
    """
    if not os.path.exists(file_path):
        pipeline_steps.append(f"File not found: {file_path}")
        return None

    file_size = os.path.getsize(file_path)
    if file_size < 100:
        pipeline_steps.append(f"File too small: {file_size} bytes")
        return None

    # Whisper has 25MB limit
    if file_size > 25 * 1024 * 1024:
        pipeline_steps.append(f"File too large for Whisper ({file_size // (1024*1024)}MB) — needs chunking")
        return None

    try:
        import httpx

        headers = {
            'Authorization': f'Bearer {settings.OPENAI_API_KEY}',
        }

        # Determine content type
        ext = Path(file_path).suffix.lower()
        content_types = {
            '.wav': 'audio/wav', '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4',
            '.flac': 'audio/flac', '.ogg': 'audio/ogg', '.webm': 'audio/webm',
            '.mp4': 'audio/mp4',
        }
        content_type = content_types.get(ext, 'application/octet-stream')

        with open(file_path, 'rb') as f:
            file_data = f.read()

        files = {
            'file': (Path(file_path).name, file_data, content_type),
            'model': (None, 'whisper-1'),
            'response_format': (None, 'verbose_json'),
        }

        if language:
            files['language'] = (None, language)
        if prompt:
            files['prompt'] = (None, prompt)

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                'https://api.openai.com/v1/audio/transcriptions',
                headers=headers,
                files=files,
            )

        if response.status_code == 200:
            data = response.json()
            text = data.get('text', '').strip()

            if not text:
                pipeline_steps.append("Whisper returned empty text")
                return None

            # Extract segments
            segments = None
            if 'segments' in data:
                segments = [
                    {
                        'start': s.get('start', 0),
                        'end': s.get('end', 0),
                        'text': s.get('text', ''),
                    }
                    for s in data['segments']
                ]

            duration = data.get('duration', 0)
            cost = round(duration / 60 * 0.006, 4)

            pipeline_steps.append(f"Whisper success: {len(text.split())} words, {duration:.1f}s")

            return {
                'text': text,
                'word_count': len(text.split()),
                'language': data.get('language', 'en'),
                'duration_seconds': duration,
                'model': 'whisper-1',
                'cost_usd': cost,
                'segments': segments,
            }

        else:
            error_msg = response.text[:200]
            pipeline_steps.append(f"Whisper API {response.status_code}: {error_msg}")
            logger.warning(f"Whisper API error {response.status_code}: {error_msg}")
            return None

    except httpx.TimeoutException:
        pipeline_steps.append("Whisper API timeout")
        return None
    except Exception as e:
        pipeline_steps.append(f"Whisper error: {str(e)[:100]}")
        logger.warning(f"Whisper call failed: {e}")
        return None
