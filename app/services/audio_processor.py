"""
Universal Audio Processor — Zero-Failure Pipeline
Accepts ANY audio format, auto-converts, enhances, and normalizes.
Uses FFmpeg for format conversion and audio enhancement.

Pipeline: Detect → Convert → Normalize → Enhance → Ready for Transcription
"""
import os
import uuid
import subprocess
import logging
import json
from pathlib import Path
from typing import Optional, Dict, List

logger = logging.getLogger("docuaction.audio.processor")

# All formats we can handle via FFmpeg
ALL_FORMATS = {
    '.mp3', '.wav', '.m4a', '.aac', '.ogg', '.flac', '.mp4', '.webm',
    '.amr', '.wma', '.opus', '.mpeg', '.mpga', '.oga', '.3gp', '.3gpp',
    '.aiff', '.aif', '.ac3', '.mka', '.mkv', '.mov', '.avi', '.ts',
    '.m4b', '.m4r', '.caf', '.spx', '.gsm', '.voc', '.ra', '.ram',
}

# Whisper-native formats (no conversion needed if clean)
WHISPER_NATIVE = {'.mp3', '.wav', '.m4a', '.flac', '.ogg', '.webm', '.mp4', '.mpeg', '.mpga', '.oga'}

# Standard output format for processing
STANDARD_FORMAT = "wav"
STANDARD_SAMPLE_RATE = 16000
STANDARD_CHANNELS = 1


def _find_ffmpeg() -> str:
    """Find FFmpeg binary."""
    for path in ['/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg', 'ffmpeg']:
        try:
            subprocess.run([path, '-version'], capture_output=True, timeout=5)
            return path
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return 'ffmpeg'  # Hope it's in PATH


FFMPEG = _find_ffmpeg()


def detect_audio_info(file_path: str) -> Dict:
    """
    Detect audio file properties using FFprobe.
    Returns: codec, bitrate, sample_rate, channels, duration, format.
    """
    try:
        cmd = [
            FFMPEG.replace('ffmpeg', 'ffprobe'), '-v', 'quiet',
            '-print_format', 'json', '-show_format', '-show_streams',
            file_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            logger.warning(f"FFprobe failed: {result.stderr[:200]}")
            return _basic_detect(file_path)

        data = json.loads(result.stdout)
        fmt = data.get('format', {})
        streams = data.get('streams', [])

        audio_stream = None
        for s in streams:
            if s.get('codec_type') == 'audio':
                audio_stream = s
                break

        if not audio_stream and streams:
            audio_stream = streams[0]

        info = {
            'codec': audio_stream.get('codec_name', 'unknown') if audio_stream else 'unknown',
            'bitrate': int(fmt.get('bit_rate', 0)) // 1000,
            'sample_rate': int(audio_stream.get('sample_rate', 0)) if audio_stream else 0,
            'channels': int(audio_stream.get('channels', 0)) if audio_stream else 0,
            'duration': float(fmt.get('duration', 0)),
            'format': fmt.get('format_name', 'unknown'),
            'size_bytes': int(fmt.get('size', 0)),
        }
        logger.info(f"Audio detected: {info}")
        return info

    except Exception as e:
        logger.warning(f"Detection failed: {e}")
        return _basic_detect(file_path)


def _basic_detect(file_path: str) -> Dict:
    """Fallback detection using file extension and size."""
    p = Path(file_path)
    return {
        'codec': 'unknown',
        'bitrate': 0,
        'sample_rate': 0,
        'channels': 0,
        'duration': 0,
        'format': p.suffix.replace('.', ''),
        'size_bytes': p.stat().st_size if p.exists() else 0,
    }


def convert_to_standard(
    input_path: str,
    output_dir: str = None,
    enhance: bool = True,
) -> Dict:
    """
    Convert any audio file to standard WAV format (16kHz mono).
    Applies normalization and optional enhancement.

    Returns:
        {
            'output_path': str,
            'original_format': str,
            'converted': bool,
            'enhanced': bool,
            'steps_applied': list,
            'duration_seconds': float,
        }
    """
    input_path = str(input_path)
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Audio file not found: {input_path}")

    steps = []
    p = Path(input_path)
    original_ext = p.suffix.lower()

    # Output path
    if not output_dir:
        output_dir = str(p.parent)
    os.makedirs(output_dir, exist_ok=True)
    output_name = f"{uuid.uuid4().hex}_processed.wav"
    output_path = os.path.join(output_dir, output_name)

    # Detect input properties
    info = detect_audio_info(input_path)
    steps.append(f"Detected: {info['codec']} {info['sample_rate']}Hz {info['channels']}ch")

    # Build FFmpeg filter chain
    filters = []

    # 1. Convert to mono if stereo
    if info['channels'] > 1 or info['channels'] == 0:
        filters.append("pan=mono|c0=0.5*c0+0.5*c1")
        steps.append("Converted to mono")

    # 2. Resample to 16kHz
    if info['sample_rate'] != STANDARD_SAMPLE_RATE or info['sample_rate'] == 0:
        filters.append(f"aresample={STANDARD_SAMPLE_RATE}")
        steps.append(f"Resampled to {STANDARD_SAMPLE_RATE}Hz")

    # 3. Volume normalization (loudnorm)
    filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    steps.append("Volume normalized (EBU R128)")

    if enhance:
        # 4. Noise reduction (highpass filter removes low rumble)
        filters.append("highpass=f=80")
        steps.append("Applied highpass filter (80Hz)")

        # 5. Low-pass to remove hiss
        filters.append("lowpass=f=8000")
        steps.append("Applied lowpass filter (8kHz)")

        # 6. Trim silence from beginning and end
        filters.append("silenceremove=start_periods=1:start_duration=0.5:start_threshold=-50dB:stop_periods=-1:stop_duration=1:stop_threshold=-50dB")
        steps.append("Trimmed silence")

    # Build FFmpeg command
    filter_str = ",".join(filters) if filters else "anull"

    cmd = [
        FFMPEG, '-y', '-i', input_path,
        '-af', filter_str,
        '-ar', str(STANDARD_SAMPLE_RATE),
        '-ac', str(STANDARD_CHANNELS),
        '-c:a', 'pcm_s16le',
        '-f', 'wav',
        output_path
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if result.returncode != 0:
            logger.warning(f"FFmpeg enhanced conversion failed: {result.stderr[:300]}")
            # Fallback: simple conversion without filters
            return _simple_convert(input_path, output_path, info, steps)

        # Get output duration
        out_info = detect_audio_info(output_path)

        logger.info(f"Audio converted: {original_ext} → WAV, {len(steps)} steps applied")
        return {
            'output_path': output_path,
            'original_format': original_ext.replace('.', ''),
            'converted_format': 'wav',
            'converted': True,
            'enhanced': enhance,
            'steps_applied': steps,
            'duration_seconds': out_info.get('duration', info.get('duration', 0)),
            'sample_rate': STANDARD_SAMPLE_RATE,
            'channels': STANDARD_CHANNELS,
        }

    except subprocess.TimeoutExpired:
        logger.error("FFmpeg conversion timed out")
        return _simple_convert(input_path, output_path, info, steps)
    except Exception as e:
        logger.error(f"FFmpeg conversion error: {e}")
        return _simple_convert(input_path, output_path, info, steps)


def _simple_convert(input_path: str, output_path: str, info: Dict, steps: List) -> Dict:
    """Fallback: simplest possible FFmpeg conversion."""
    steps.append("Fallback: simple conversion")
    try:
        cmd = [FFMPEG, '-y', '-i', input_path, '-ar', '16000', '-ac', '1', '-f', 'wav', output_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            return {
                'output_path': output_path,
                'original_format': Path(input_path).suffix.replace('.', ''),
                'converted_format': 'wav',
                'converted': True,
                'enhanced': False,
                'steps_applied': steps,
                'duration_seconds': info.get('duration', 0),
                'sample_rate': 16000,
                'channels': 1,
            }
    except Exception:
        pass

    # Last resort: copy file as-is and hope Whisper handles it
    import shutil
    whisper_path = output_path.replace('.wav', Path(input_path).suffix)
    shutil.copy2(input_path, whisper_path)
    steps.append("Fallback: raw file passthrough")
    return {
        'output_path': whisper_path,
        'original_format': Path(input_path).suffix.replace('.', ''),
        'converted_format': Path(input_path).suffix.replace('.', ''),
        'converted': False,
        'enhanced': False,
        'steps_applied': steps,
        'duration_seconds': info.get('duration', 0),
        'sample_rate': info.get('sample_rate', 0),
        'channels': info.get('channels', 0),
    }


def split_audio(file_path: str, chunk_seconds: int = 600, output_dir: str = None) -> List[str]:
    """
    Split long audio into chunks for parallel processing.
    Default: 10-minute chunks.
    """
    if not output_dir:
        output_dir = os.path.join(os.path.dirname(file_path), "chunks")
    os.makedirs(output_dir, exist_ok=True)

    info = detect_audio_info(file_path)
    duration = info.get('duration', 0)

    if duration <= chunk_seconds:
        return [file_path]  # No splitting needed

    chunks = []
    offset = 0
    chunk_num = 0

    while offset < duration:
        chunk_path = os.path.join(output_dir, f"chunk_{chunk_num:03d}.wav")
        cmd = [
            FFMPEG, '-y', '-i', file_path,
            '-ss', str(offset),
            '-t', str(chunk_seconds),
            '-ar', '16000', '-ac', '1',
            '-c:a', 'pcm_s16le', '-f', 'wav',
            chunk_path
        ]

        try:
            subprocess.run(cmd, capture_output=True, timeout=120)
            if os.path.exists(chunk_path) and os.path.getsize(chunk_path) > 1000:
                chunks.append(chunk_path)
        except Exception as e:
            logger.warning(f"Chunk {chunk_num} failed: {e}")

        offset += chunk_seconds
        chunk_num += 1

    logger.info(f"Split into {len(chunks)} chunks ({chunk_seconds}s each)")
    return chunks if chunks else [file_path]


def cleanup_temp_files(*paths):
    """Clean up temporary processing files."""
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except Exception:
            pass
