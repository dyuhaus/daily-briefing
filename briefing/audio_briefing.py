"""
Audio Briefing Generator — creates a NotebookLM audio overview from the daily
briefing plain text summary, downloads it, and sends it to Telegram.

Runs autonomously as a post-compilation step in the DailyBriefing pipeline.
Uses the notebooklm_tools Python client for all NLM interactions.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# NotebookLM notebook ID for DailyBriefing (from registry)
DAILYBRIEFING_NOTEBOOK_ID: str = "your-notebooklm-notebook-id"

# Output directory for downloaded audio files
AUDIO_OUTPUT_DIR: Path = Path(__file__).resolve().parent.parent / "output" / "audio"

# Telegram client import path
_TELEGRAM_CLIENT_DIR: Path = (
    Path(__file__).resolve().parent.parent.parent / "Tools" / "TelegramDispatcher"
)

# Audio generation config
AUDIO_FORMAT: str = "brief"
AUDIO_POLL_INTERVAL_SECONDS: int = 15
AUDIO_POLL_MAX_WAIT_SECONDS: int = 900
AUDIO_FOCUS_PROMPT: str = (
    "Summarize today's daily briefing as a concise morning podcast for an AI "
    "consultant and algorithmic trader. Cover the key headlines across Anthropic, "
    "OpenAI, Google, AI industry, markets, and project-relevant insights. "
    "Keep it actionable and focused on what matters for someone building AI agent "
    "systems and trading algorithms."
)


@dataclass(frozen=True)
class AudioBriefingResult:
    """Immutable result from audio briefing generation."""

    success: bool
    audio_path: Optional[str]
    telegram_sent: bool
    duration_seconds: Optional[float]
    error: Optional[str]


def _get_nlm_client() -> object:
    """Create a NotebookLM client with cached auth tokens."""
    from notebooklm_tools import NotebookLMClient
    from notebooklm_tools.core.auth import load_cached_tokens

    tokens = load_cached_tokens()
    return NotebookLMClient(cookies=tokens.cookies)


def _cleanup_old_sources(client: object, notebook_id: str) -> int:
    """
    Remove old 'Daily Briefing' text sources from the notebook to prevent bloat.

    NotebookLM generation slows as sources accumulate. This removes all pasted-text
    sources with 'Daily Briefing' in the title, keeping only non-briefing sources.

    Returns the number of sources deleted.
    """
    deleted: int = 0
    try:
        sources: list[dict] = client.get_notebook_sources_with_types(notebook_id)
        if not isinstance(sources, list):
            logger.warning("Could not list notebook sources for cleanup")
            return 0

        for src in sources:
            title: str = src.get("title", "")
            source_id: str = src.get("source_id", "")
            source_type: str = src.get("type", "")
            # Only delete pasted-text briefing sources, not manually added reference docs
            if "Daily Briefing" in title and source_type in ("pasted_text", "text", ""):
                if source_id:
                    try:
                        client.delete_source(notebook_id, source_id)
                        deleted += 1
                        logger.info(f"Deleted old source: {title} ({source_id[:12]}...)")
                    except Exception as e:
                        logger.warning(f"Failed to delete source {source_id}: {e}")

        if deleted > 0:
            logger.info(f"Cleaned up {deleted} old briefing source(s)")
    except Exception as e:
        logger.warning(f"Source cleanup failed (non-fatal): {e}")

    return deleted


def _add_source_and_generate(
    client: object,
    plain_text: str,
    notebook_id: str,
    poll_timeout: int = AUDIO_POLL_MAX_WAIT_SECONDS,
) -> tuple[Optional[str], Optional[str]]:
    """
    Add briefing text as source and generate audio.

    Note: add_text_source, create_audio_overview, and get_studio_status
    are synchronous methods. Only download_audio is async.

    Returns (artifact_id, audio_url) or (None, None) on failure.
    """
    today: str = datetime.now().strftime("%Y-%m-%d")
    title: str = f"Daily Briefing — {today}"

    # Add text source (sync)
    logger.info(f"Adding briefing source ({len(plain_text)} chars) to notebook")
    source_result: dict = client.add_text_source(
        notebook_id=notebook_id,
        text=plain_text,
        title=title,
    )
    source_id: str = source_result.get("source_id", "")
    logger.info(f"Source added: {source_id}")

    # Generate audio overview (sync)
    # format_code: 1=deep_dive, 2=brief, per notebooklm_tools constants
    BRIEF_FORMAT_CODE: int = 2
    logger.info(f"Creating audio overview (format_code={BRIEF_FORMAT_CODE}/brief)")
    audio_result: dict = client.create_audio_overview(
        notebook_id=notebook_id,
        format_code=BRIEF_FORMAT_CODE,
        source_ids=[source_id] if source_id else None,
        focus_prompt=AUDIO_FOCUS_PROMPT,
    )
    artifact_id: str = audio_result.get("artifact_id", "")
    logger.info(f"Audio generation started: {artifact_id}")

    # Poll for completion (sync)
    elapsed: float = 0.0
    while elapsed < poll_timeout:
        time.sleep(AUDIO_POLL_INTERVAL_SECONDS)
        elapsed += AUDIO_POLL_INTERVAL_SECONDS

        studio_result = client.get_studio_status(notebook_id)
        # Python client returns list[dict] directly, not {"artifacts": [...]}
        artifacts: list[dict] = (
            studio_result if isinstance(studio_result, list)
            else studio_result.get("artifacts", [])
        )

        for art in artifacts:
            if art.get("artifact_id") == artifact_id:
                art_status: str = art.get("status", "")
                if art_status == "completed":
                    audio_url: Optional[str] = art.get("audio_url")
                    logger.info(f"Audio completed in {elapsed:.0f}s")
                    return artifact_id, audio_url
                elif art_status == "failed":
                    logger.error("Audio generation failed")
                    return None, None

        logger.info(f"Audio still generating... ({elapsed:.0f}s)")

    logger.error(f"Audio generation timed out after {poll_timeout}s")
    return None, None


async def _download_audio(
    client: object,
    notebook_id: str,
    artifact_id: str,
    output_path: Path,
) -> bool:
    """Download audio artifact to local file."""
    # Remove existing file to avoid Windows rename conflict
    if output_path.exists():
        output_path.unlink()

    try:
        await client.download_audio(
            notebook_id,
            output_path=str(output_path),
            artifact_id=artifact_id,
        )
        if output_path.exists() and output_path.stat().st_size > 1000:
            logger.info(
                f"Audio downloaded: {output_path} "
                f"({output_path.stat().st_size / 1024:.0f} KB)"
            )
            return True
        else:
            logger.error("Downloaded file is missing or too small")
            return False
    except Exception as e:
        logger.error(f"Audio download failed: {e}")
        return False


def send_audio_telegram(
    audio_path: str,
    title: Optional[str] = None,
    caption: Optional[str] = None,
) -> bool:
    """Send an audio file to Telegram using the shared client."""
    try:
        sys.path.insert(0, str(_TELEGRAM_CLIENT_DIR))
        from telegram_client import send_audio

        today: str = datetime.now().strftime("%Y-%m-%d")

        result: dict = send_audio(
            audio_file_path=audio_path,
            title=title or f"Daily Briefing {today}",
            caption=caption or f"Daily Briefing Audio — {today}",
        )

        if result.get("ok"):
            logger.info("Audio briefing sent to Telegram successfully")
            return True
        else:
            logger.error(f"Telegram send failed: {result}")
            return False

    except Exception as e:
        logger.error(f"Failed to send audio to Telegram: {e}")
        return False
    finally:
        if str(_TELEGRAM_CLIENT_DIR) in sys.path:
            sys.path.remove(str(_TELEGRAM_CLIENT_DIR))


def _run_full_pipeline(
    plain_text: str,
    notebook_id: str,
    send_telegram: bool,
) -> AudioBriefingResult:
    """Pipeline: cleanup → source → generate → poll → download → telegram.

    On timeout, retries once after cleaning up old sources from the notebook.
    """
    today: str = datetime.now().strftime("%Y-%m-%d")
    AUDIO_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    audio_path: Path = AUDIO_OUTPUT_DIR / f"briefing_{today}.m4a"
    start_time: float = time.monotonic()

    try:
        client = _get_nlm_client()
    except Exception as e:
        return AudioBriefingResult(
            success=False,
            audio_path=None,
            telegram_sent=False,
            duration_seconds=None,
            error=f"Failed to initialize NotebookLM client: {e}",
        )

    # Clean up old briefing sources before adding today's
    _cleanup_old_sources(client, notebook_id)

    try:
        # Step 1-2: Add source and generate audio (sync calls)
        artifact_id, audio_url = _add_source_and_generate(
            client, plain_text, notebook_id
        )

        # Retry once on timeout — re-clean sources and try with fresh state
        if not artifact_id:
            logger.info("First attempt failed — retrying after source cleanup")
            _cleanup_old_sources(client, notebook_id)
            artifact_id, audio_url = _add_source_and_generate(
                client, plain_text, notebook_id
            )

        if not artifact_id:
            return AudioBriefingResult(
                success=False,
                audio_path=None,
                telegram_sent=False,
                duration_seconds=time.monotonic() - start_time,
                error="Audio generation failed or timed out (after retry)",
            )

        # Step 3: Download (async — only download_audio is a coroutine)
        downloaded: bool = asyncio.run(
            _download_audio(client, notebook_id, artifact_id, audio_path)
        )
        if not downloaded:
            return AudioBriefingResult(
                success=False,
                audio_path=None,
                telegram_sent=False,
                duration_seconds=time.monotonic() - start_time,
                error="Audio download failed",
            )

        # Step 4: Send to Telegram
        telegram_sent: bool = False
        if send_telegram:
            telegram_sent = send_audio_telegram(str(audio_path))

        duration: float = time.monotonic() - start_time
        return AudioBriefingResult(
            success=True,
            audio_path=str(audio_path),
            telegram_sent=telegram_sent,
            duration_seconds=duration,
            error=None,
        )

    except Exception as e:
        return AudioBriefingResult(
            success=False,
            audio_path=None,
            telegram_sent=False,
            duration_seconds=time.monotonic() - start_time,
            error=str(e),
        )


def generate_audio_briefing(
    plain_text: str,
    notebook_id: str = DAILYBRIEFING_NOTEBOOK_ID,
    send_telegram: bool = True,
) -> AudioBriefingResult:
    """
    Full autonomous audio briefing pipeline.

    1. Add plain text as NotebookLM source
    2. Generate audio overview (brief format)
    3. Poll for completion
    4. Download audio to local file
    5. Send to Telegram

    Args:
        plain_text: The daily briefing plain text summary.
        notebook_id: NotebookLM notebook ID (defaults to DailyBriefing).
        send_telegram: Whether to send audio to Telegram after download.

    Returns:
        AudioBriefingResult with success status, file path, and Telegram delivery status.
    """
    return _run_full_pipeline(plain_text, notebook_id, send_telegram)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [AudioBriefing] %(message)s",
    )
    # Test: read today's digest and generate audio
    digest_dir: Path = Path(__file__).resolve().parent.parent / "output" / "digests"
    today: str = datetime.now().strftime("%Y-%m-%d")
    digest_path: Path = digest_dir / f"{today}.json"

    if not digest_path.exists():
        logger.error(f"No digest found for today: {digest_path}")
        sys.exit(1)

    digest: dict = json.loads(digest_path.read_text(encoding="utf-8"))

    # Build a plain text summary from the digest sections
    sections: list[str] = []
    for key in ["anthropic_news", "openai_news", "google_ai_news", "ai_industry",
                "market_news", "claude_workflows", "project_applicability"]:
        section: Optional[dict] = digest.get(key)
        if section and section.get("cliff_notes"):
            sections.append(f"{key.upper().replace('_', ' ')}: {section['cliff_notes']}")

    plain_text: str = f"Daily Briefing — {today}\n\n" + "\n\n".join(sections)

    result: AudioBriefingResult = generate_audio_briefing(plain_text)
    if result.success:
        logger.info(f"Audio: {result.audio_path}")
        logger.info(f"Telegram: {'sent' if result.telegram_sent else 'not sent'}")
        logger.info(f"Duration: {result.duration_seconds:.0f}s")
    else:
        logger.error(f"Failed: {result.error}")
        sys.exit(1)
