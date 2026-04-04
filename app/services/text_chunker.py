"""
Context Window Management
Handles documents exceeding ~20,000 words by:
1. Splitting into chunks
2. Summarizing each chunk via Haiku
3. Combining chunk summaries
4. Passing combined summary to Sonnet for final extraction
"""

import logging
import asyncio

logger = logging.getLogger("docuaction.chunker")

CHUNK_SIZE = 5000       # words per chunk
OVERLAP = 200           # word overlap between chunks


def split_into_chunks(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = OVERLAP) -> list:
    """Split text into overlapping chunks by word count."""
    words = text.split()
    chunks = []
    start = 0

    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunk = ' '.join(words[start:end])
        chunks.append(chunk)
        start = end - overlap
        if start >= len(words):
            break

    logger.info(f"Split {len(words)} words into {len(chunks)} chunks")
    return chunks


async def summarize_chunk(chunk: str, chunk_index: int, total_chunks: int) -> str:
    """Summarize a single chunk using Haiku (lightweight, fast)."""
    from app.services.ai_engine import _call_model

    prompt = f"""You are processing chunk {chunk_index + 1} of {total_chunks} from a long document.
Summarize the key information, decisions, action items, and important data points from this section.
Keep your summary concise (200-400 words) but preserve all specific names, dates, numbers, and decisions.
Return plain text summary only — no JSON.

--- CHUNK {chunk_index + 1}/{total_chunks} ---
{chunk}
--- END CHUNK ---"""

    try:
        summary = await _call_model(
            model_type="haiku",
            system_prompt="You are a document summarization assistant. Preserve all specific details.",
            user_prompt=prompt,
            timeout=10,
        )
        return summary.strip()
    except Exception as e:
        logger.error(f"Chunk {chunk_index + 1} summarization failed: {e}")
        # Fallback: return first 500 words of chunk
        words = chunk.split()
        return ' '.join(words[:500])


async def chunk_and_summarize(text: str) -> str:
    """
    Full chunking pipeline for long documents.
    Returns a combined summary suitable for final AI processing.
    """
    chunks = split_into_chunks(text)

    if len(chunks) <= 1:
        return text

    logger.info(f"Summarizing {len(chunks)} chunks in parallel...")

    # Process chunks in parallel (max 5 concurrent)
    semaphore = asyncio.Semaphore(5)

    async def bounded_summarize(chunk, idx):
        async with semaphore:
            return await summarize_chunk(chunk, idx, len(chunks))

    tasks = [bounded_summarize(chunk, i) for i, chunk in enumerate(chunks)]
    summaries = await asyncio.gather(*tasks)

    # Combine chunk summaries
    combined = f"[DOCUMENT ANALYSIS — {len(chunks)} sections processed]\n\n"
    for i, summary in enumerate(summaries):
        combined += f"--- Section {i + 1} of {len(chunks)} ---\n{summary}\n\n"

    logger.info(f"Combined summary: {len(combined.split())} words from {len(chunks)} chunks")
    return combined
