"""
YouTube Claude Code Scanner — yt-dlp search + transcript extraction + LLM analysis.
Finds new Claude Code videos from the last 48 hours and extracts actionable patterns.
"""
from __future__ import annotations

import json
import hashlib
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.credentials import get_credential

logging.basicConfig(level=logging.INFO, format="%(asctime)s [YouTubeScanner] %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output" / "youtube"
TRANSCRIPT_DIR = OUTPUT_DIR / "transcripts"
CONFIG_PATH = PROJECT_ROOT / "config" / "settings.json"
SEEN_HASHES_FILE = OUTPUT_DIR / ".seen_hashes.json"


@dataclass
class VideoResult:
    video_id: str
    title: str
    channel: str
    url: str
    upload_date: str
    duration_seconds: int
    description: str
    transcript: str = ""
    extracted_patterns: list[str] | None = None
    relevance_score: float = 0.0
    content_hash: str = ""

    def compute_hash(self) -> str:
        self.content_hash = hashlib.sha256(self.video_id.encode()).hexdigest()[:16]
        return self.content_hash


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def load_seen_hashes() -> set[str]:
    if SEEN_HASHES_FILE.exists():
        return set(json.loads(SEEN_HASHES_FILE.read_text(encoding="utf-8")))
    return set()


def save_seen_hashes(hashes: set[str]) -> None:
    trimmed = sorted(hashes)[-3000:]
    SEEN_HASHES_FILE.write_text(json.dumps(trimmed, indent=2), encoding="utf-8")


def search_youtube(query: str, max_results: int = 15) -> list[VideoResult]:
    """Use yt-dlp to search YouTube for recent videos."""
    results: list[VideoResult] = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)

    try:
        logger.info(f"yt-dlp search: '{query}' (max {max_results})")
        cmd = [
            "yt-dlp",
            f"ytsearch{max_results}:{query}",
            "--dump-json",
            "--no-download",
            "--flat-playlist",
            "--no-warnings",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        if proc.returncode != 0:
            logger.warning(f"yt-dlp search failed: {proc.stderr[:200]}")
            return results

        for line in proc.stdout.strip().splitlines():
            try:
                data = json.loads(line)
                video_id = data.get("id", "")
                if not video_id:
                    continue

                upload_date = data.get("upload_date", "")
                if upload_date:
                    try:
                        upload_dt = datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc)
                        if upload_dt < cutoff:
                            continue  # Skip old videos
                    except ValueError:
                        pass

                video = VideoResult(
                    video_id=video_id,
                    title=data.get("title", "Untitled"),
                    channel=data.get("channel", data.get("uploader", "Unknown")),
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    upload_date=upload_date,
                    duration_seconds=data.get("duration", 0) or 0,
                    description=data.get("description", "")[:500],
                )
                video.compute_hash()
                results.append(video)

            except json.JSONDecodeError:
                continue

        logger.info(f"Found {len(results)} recent videos for '{query}'")

    except subprocess.TimeoutExpired:
        logger.warning(f"yt-dlp search timed out for '{query}'")
    except FileNotFoundError:
        logger.error("yt-dlp not found in PATH")

    return results


def _create_transcript_client() -> "YouTubeTranscriptApi | None":
    """Create a YouTubeTranscriptApi client, using Webshare proxy if available."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        # Priority 1: Webshare residential proxy (bypasses IP blocks reliably)
        try:
            from config.credentials import get_credential
            webshare_key = get_credential("WEBSHARE_API_KEY")
            if webshare_key:
                import requests as _req
                proxy_resp = _req.get(
                    "https://proxy.webshare.io/api/v2/proxy/config/",
                    headers={"Authorization": f"Token {webshare_key}"},
                    timeout=10,
                )
                if proxy_resp.status_code == 200:
                    config = proxy_resp.json()
                    user = config["username"]
                    pwd = config["password"]
                    from youtube_transcript_api.proxies import WebshareProxyConfig
                    proxy_config = WebshareProxyConfig(
                        proxy_username=user,
                        proxy_password=pwd,
                        filter_ip_locations=["US"],
                        retries_when_blocked=5,
                    )
                    ytt = YouTubeTranscriptApi(proxy_config=proxy_config)
                    logger.info("Using Webshare residential proxy (US) for transcript fetching")
                    return ytt
        except Exception as e:
            logger.debug(f"Webshare proxy setup failed: {e}")

        # Priority 2: Cookie-authenticated session
        cookie_file = PROJECT_ROOT / "config" / "youtube_cookies.txt"
        if cookie_file.exists():
            import http.cookiejar
            from requests import Session
            jar = http.cookiejar.MozillaCookieJar(str(cookie_file))
            jar.load(ignore_discard=True, ignore_expires=True)
            session = Session()
            session.cookies.update(jar)
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            })
            ytt = YouTubeTranscriptApi(http_client=session)
            logger.info("Using cookie-authenticated session for transcript fetching")
            return ytt

        # Priority 3: No auth (works when IP is not blocked)
        return YouTubeTranscriptApi()
    except Exception as e:
        logger.debug(f"Failed to create transcript API client: {e}")
        return None


# Module-level client — created once, reused across all transcript fetches
_transcript_client: "YouTubeTranscriptApi | None" = None


def _get_transcript_client() -> "YouTubeTranscriptApi | None":
    """Get or create the shared transcript client."""
    global _transcript_client
    if _transcript_client is None:
        _transcript_client = _create_transcript_client()
    return _transcript_client


# Max videos to fetch transcripts for per run (prevents IP blocks)
MAX_TRANSCRIPT_FETCHES = 3


def fetch_transcript(video_id: str) -> str:
    """Fetch video transcript using youtube-transcript-api v1.2+.

    Uses Webshare proxy if WEBSHARE_API_KEY is set in credentials.env.
    Falls back to cookie auth, then unauthenticated requests.
    """
    try:
        ytt = _get_transcript_client()
        if ytt is None:
            return ""

        # Try fetching English transcript
        try:
            transcript = ytt.fetch(video_id, languages=["en"])
        except Exception:
            # Fallback: fetch any available language
            try:
                transcript = ytt.fetch(video_id)
            except Exception:
                return ""

        # Extract text from snippets
        full_text = " ".join(snippet.text for snippet in transcript.snippets)
        return full_text[:15000]  # Cap at 15k chars to manage token costs

    except Exception as e:
        err_name = type(e).__name__
        if "IpBlocked" in err_name or "RequestBlocked" in err_name:
            logger.warning(f"YouTube IP-blocked transcript for {video_id} — title+description scoring will be used")
        else:
            logger.debug(f"Transcript unavailable for {video_id}: {e}")
        return ""


def extract_patterns(videos: list[VideoResult]) -> list[VideoResult]:
    """Use Claude Code CLI (or API) to extract actionable Claude Code patterns from transcripts."""
    try:
        from config.llm import llm_call
    except ImportError:
        logger.error("Could not import llm module")
        for v in videos:
            v.relevance_score = 0.5 if v.transcript else 0.2
        return videos

    for video in videos:
        if not video.transcript:
            # Score based on title + description keywords when transcript unavailable
            searchable = (video.title + " " + video.description).lower()

            # High-signal keywords (directly Claude Code related)
            high_keywords = [
                "claude code", "claude agent", "anthropic claude", "mcp server",
                "claude hooks", "claude cli", "claude sdk", "agent sdk",
                "claude.md", "claude code agent", "agentic coding",
            ]
            # Medium-signal keywords (AI coding tools, related ecosystem)
            medium_keywords = [
                "cursor", "windsurf", "copilot", "ai coding", "ai agent",
                "coding agent", "vscode ai", "llm coding", "prompt engineering",
                "ai development", "ai workflow", "anthropic",
            ]

            high_hits = sum(1 for kw in high_keywords if kw in searchable)
            medium_hits = sum(1 for kw in medium_keywords if kw in searchable)

            # Base 0.3, +0.2 per high hit, +0.08 per medium hit, cap at 0.85
            video.relevance_score = min(0.85, 0.3 + high_hits * 0.2 + medium_hits * 0.08)
            video.extracted_patterns = []

            logger.info(
                f"Title+desc scored '{video.title[:50]}': "
                f"{high_hits} high, {medium_hits} medium → {video.relevance_score:.2f}"
            )
            continue

        try:
            prompt = f"""Analyze this YouTube video transcript about Claude Code. Extract:
1. Actionable workflow patterns or techniques (bullet points)
2. Configuration tips (settings, hooks, MCP servers, CLAUDE.md patterns)
3. Relevance score 0.0-1.0 (how useful is this for a power user of Claude Code agents)

Return JSON: {{"patterns": ["pattern1", "pattern2"], "relevance": 0.8}}

Title: {video.title}
Channel: {video.channel}
Transcript (first 5000 chars):
{video.transcript[:5000]}"""

            text, usage = llm_call(prompt, purpose="youtube-extraction")

            if not text:
                video.relevance_score = 0.3
                video.extracted_patterns = []
                continue

            start = text.index("{")
            end = text.rindex("}") + 1
            parsed = json.loads(text[start:end])

            video.extracted_patterns = parsed.get("patterns", [])
            video.relevance_score = float(parsed.get("relevance", 0.5))

            logger.info(f"Extracted {len(video.extracted_patterns)} patterns from '{video.title[:50]}' (score: {video.relevance_score}, backend: {usage.backend}, est cost: ${usage.estimated_cost_usd:.6f})")

        except Exception as e:
            logger.warning(f"LLM extraction failed for '{video.title[:40]}': {e}")
            video.relevance_score = 0.3
            video.extracted_patterns = []

        time.sleep(1)  # Courtesy delay between calls

    return videos


def run_scanner() -> Path:
    """Execute the full YouTube scanning pipeline. Returns path to output file."""
    config = load_config()
    yt_config = config["youtube"]
    seen_hashes = load_seen_hashes()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)

    all_videos: list[VideoResult] = []

    # Phase 1: Search for videos
    for query in yt_config["search_queries"]:
        videos = search_youtube(query, yt_config["max_results_per_query"])
        all_videos.extend(videos)
        time.sleep(2)

    # Deduplicate by video ID
    seen_ids: set[str] = set()
    unique_videos: list[VideoResult] = []
    for video in all_videos:
        if video.video_id not in seen_ids and video.content_hash not in seen_hashes:
            unique_videos.append(video)
            seen_ids.add(video.video_id)

    logger.info(f"Found {len(all_videos)} total, {len(unique_videos)} unique new videos")

    # Phase 2: Fetch transcripts (limited to top-scored videos to avoid IP blocks)
    # Sort by relevance so we prioritize the most valuable videos
    videos_by_score = sorted(unique_videos, key=lambda v: v.relevance_score, reverse=True)
    transcript_budget = MAX_TRANSCRIPT_FETCHES
    for video in videos_by_score:
        if transcript_budget <= 0:
            logger.info(f"Transcript budget exhausted ({MAX_TRANSCRIPT_FETCHES} max) — skipping remaining videos")
            break
        logger.info(f"Fetching transcript ({MAX_TRANSCRIPT_FETCHES - transcript_budget + 1}/{MAX_TRANSCRIPT_FETCHES}): '{video.title[:50]}...'")
        video.transcript = fetch_transcript(video.video_id)
        if video.transcript:
            # Save transcript
            today = datetime.now().strftime("%Y-%m-%d")
            transcript_dir = TRANSCRIPT_DIR / today
            transcript_dir.mkdir(exist_ok=True)
            transcript_file = transcript_dir / f"{video.video_id}.txt"
            transcript_file.write_text(video.transcript, encoding="utf-8")
        transcript_budget -= 1
        time.sleep(2)  # Respectful delay between requests

    videos_with_transcripts = [v for v in unique_videos if v.transcript]
    logger.info(f"Got transcripts for {len(videos_with_transcripts)}/{len(unique_videos)} videos")

    # Phase 3: LLM extraction
    unique_videos = extract_patterns(unique_videos)

    # Filter by relevance
    min_score = yt_config["min_relevance_score"]
    filtered = [v for v in unique_videos if v.relevance_score >= min_score]
    filtered.sort(key=lambda x: x.relevance_score, reverse=True)

    logger.info(f"Filtered to {len(filtered)} videos above {min_score} relevance")

    # Save output (exclude full transcript from output to save space)
    today = datetime.now().strftime("%Y-%m-%d")
    output_file = OUTPUT_DIR / f"{today}.json"
    output_items = []
    for v in filtered:
        item = asdict(v)
        item["transcript_length"] = len(v.transcript)
        item["transcript"] = ""  # Don't include full transcript in summary
        output_items.append(item)

    output_data = {
        "date": today,
        "scan_time": datetime.now(timezone.utc).isoformat(),
        "total_found": len(all_videos),
        "unique_new": len(unique_videos),
        "with_transcripts": len(videos_with_transcripts),
        "after_filter": len(filtered),
        "items": output_items,
    }
    output_file.write_text(json.dumps(output_data, indent=2, ensure_ascii=False), encoding="utf-8")

    # Update seen hashes
    new_hashes = {v.content_hash for v in unique_videos}
    seen_hashes.update(new_hashes)
    save_seen_hashes(seen_hashes)

    logger.info(f"Output saved to {output_file}")
    return output_file


if __name__ == "__main__":
    output = run_scanner()
    print(f"YouTube scan complete: {output}")
