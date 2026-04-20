"""
Gemini Platform Scanner — Tracks new documentation, guides, tutorials, and methods
for Google Gemini API, Firebase Studio, Genkit, and related game creation tools.

Uses Gemini grounded search to find fresh content, then LLM-scores for relevance.
"""
from __future__ import annotations

import json
import hashlib
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field, asdict

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "Tools" / "GeminiSearch" / "python"))
from config.credentials import get_credential
from config.quota import gemini_quota
from gemini_search import GeminiSearchClient, SearchResult

logging.basicConfig(level=logging.INFO, format="%(asctime)s [GeminiDocsScanner] %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output" / "gemini_docs"
CONFIG_PATH = PROJECT_ROOT / "config" / "settings.json"
SEEN_HASHES_FILE = OUTPUT_DIR / ".seen_hashes.json"


@dataclass
class DocItem:
    title: str
    url: str
    source: str
    snippet: str
    timestamp: str
    content_type: str = ""  # "docs", "guide", "tutorial", "blog", "video", "changelog"
    relevance_score: float = 0.0
    content_hash: str = ""
    freshness_window: str = "24h"

    def compute_hash(self) -> str:
        raw = f"{self.url}{self.title}".lower().strip()
        self.content_hash = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return self.content_hash


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def load_seen_hashes() -> set[str]:
    if SEEN_HASHES_FILE.exists():
        return set(json.loads(SEEN_HASHES_FILE.read_text(encoding="utf-8")))
    return set()


def save_seen_hashes(hashes: set[str]) -> None:
    trimmed = sorted(hashes)[-5000:]
    SEEN_HASHES_FILE.write_text(json.dumps(trimmed, indent=2), encoding="utf-8")


def _get_gemini_client() -> GeminiSearchClient:
    """Get or create the shared Gemini search client."""
    global _gemini_client
    if "_gemini_client" not in globals() or _gemini_client is None:
        _gemini_client = GeminiSearchClient()
    return _gemini_client


_gemini_client: Optional[GeminiSearchClient] = None


def search_gemini(query: str, max_results: int = 10) -> list[DocItem]:
    """Search for Gemini platform docs using Gemini grounded search."""
    items: list[DocItem] = []
    try:
        from gemini_search import DEFAULT_MODEL

        client = _get_gemini_client()

        # Check quota before calling; wait up to 65s for a slot
        if not gemini_quota.can_call(DEFAULT_MODEL):
            logger.info(f"Quota limit reached — waiting for slot before: '{query}'")
            if not gemini_quota.wait_for_quota(DEFAULT_MODEL):
                logger.warning(f"Quota wait timed out — skipping query: '{query}'")
                return items
        gemini_quota.record_call(DEFAULT_MODEL)

        logger.info(f"Gemini grounded search: '{query}'")

        result = client.search(query, focus="documentation")

        for source in result.sources[:max_results]:
            item = DocItem(
                title=source.title[:200],
                url=source.url,
                source="gemini_grounded",
                snippet=result.text[:500],
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            item.compute_hash()
            items.append(item)

        if not items and result.text:
            item = DocItem(
                title=query[:200],
                url="",
                source="gemini_grounded",
                snippet=result.text[:500],
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            item.compute_hash()
            items.append(item)

        logger.info(f"Gemini grounded search returned {len(items)} results (model: {result.model})")

    except Exception as e:
        logger.warning(f"Gemini grounded search failed: {e}")

    return items


def score_relevance(items: list[DocItem]) -> list[DocItem]:
    """Score items by relevance to Gemini platform / game creation using LLM."""
    if not items:
        return items

    try:
        from config.llm import llm_call

        batch_text = "\n\n".join(
            f"[{i}] {item.title}\n{item.snippet[:200]}"
            for i, item in enumerate(items)
        )

        prompt = f"""Rate each item's relevance to "Google Gemini API, Firebase Studio, Genkit, AI-powered app/game creation, and Google AI platform updates" on a 0.0-1.0 scale.

High relevance (0.7-1.0): New Gemini API features, Firebase Studio updates, Genkit tutorials, AI game creation methods, Google AI Studio guides, Gemini model releases, Imagen/Veo updates
Medium relevance (0.4-0.7): General Google AI news, cloud platform updates tangentially related, web app development with AI
Low relevance (0.0-0.3): Unrelated content, old news, generic AI content not specific to Google's platform

Return ONLY a JSON array of numbers, one per item. Example: [0.8, 0.3, 0.9]

Items:
{batch_text}"""

        text, usage = llm_call(prompt, purpose="gemini-docs-scoring")

        if not text:
            logger.warning("LLM returned empty response, falling back to keyword scoring")
            return _keyword_score(items)

        start = text.index("[")
        end = text.rindex("]") + 1
        scores = json.loads(text[start:end])

        for i, score in enumerate(scores):
            if i < len(items):
                items[i].relevance_score = float(score)

        logger.info(f"LLM scored {len(items)} items (backend: {usage.backend}, est cost: ${usage.estimated_cost_usd:.6f})")

    except Exception as e:
        logger.warning(f"LLM scoring failed ({e}), falling back to keyword scoring")
        items = _keyword_score(items)

    return items


def _keyword_score(items: list[DocItem]) -> list[DocItem]:
    """Keyword-based relevance scoring as fallback."""
    high_signal = [
        "gemini api", "firebase studio", "genkit", "google ai studio",
        "gemini 3", "gemini 2.5", "imagen 4", "veo 3", "live api",
        "app prototyping", "ai game", "gemini flash", "gemini pro",
        "google antigravity", "firebase app hosting", "gemini model",
    ]
    medium_signal = [
        "google ai", "firebase", "cloud functions", "firestore",
        "ai app builder", "ai code generation", "structured output",
        "function calling", "ai tutorial", "google cloud ai",
        "vertex ai", "ai agent", "deep research",
    ]

    for item in items:
        text = f"{item.title} {item.snippet}".lower()
        score = 0.2  # base
        for kw in high_signal:
            if kw in text:
                score += 0.15
        for kw in medium_signal:
            if kw in text:
                score += 0.07
        item.relevance_score = min(score, 1.0)

    return items


def classify_content_type(items: list[DocItem]) -> list[DocItem]:
    """Classify each item's content type based on URL and title patterns."""
    for item in items:
        url_lower = item.url.lower()
        title_lower = item.title.lower()
        combined = f"{url_lower} {title_lower}"

        if "ai.google.dev" in url_lower or "firebase.google.com/docs" in url_lower:
            item.content_type = "docs"
        elif any(kw in combined for kw in ["tutorial", "how to", "step by step", "getting started"]):
            item.content_type = "tutorial"
        elif any(kw in combined for kw in ["guide", "walkthrough", "best practice"]):
            item.content_type = "guide"
        elif any(kw in combined for kw in ["changelog", "release note", "what's new", "update"]):
            item.content_type = "changelog"
        elif any(kw in combined for kw in ["youtube.com", "video"]):
            item.content_type = "video"
        elif any(kw in combined for kw in ["blog", "medium.com", "dev.to", "hashnode"]):
            item.content_type = "blog"
        else:
            item.content_type = "article"

    return items


def run_scanner() -> Path:
    """Execute the full Gemini docs scanning pipeline. Returns path to output file."""
    config = load_config()
    gemini_config = config.get("gemini_docs", {})
    seen_hashes = load_seen_hashes()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    all_items: list[DocItem] = []

    # Phase 1: Gemini grounded search (replaces DuckDuckGo + Firecrawl)
    search_queries = gemini_config.get("search_queries", [
        "Google Gemini API new documentation 2026",
        "Firebase Studio tutorial guide",
        "Genkit AI framework tutorial",
        "Gemini API game development",
        "Google AI Studio app creation guide",
        "Gemini structured output tutorial",
        "Firebase Studio app prototyping agent",
        "Gemini Live API tutorial",
        "Imagen 4 API guide",
        "Google Gemini 3 new features",
    ])

    for query in search_queries:
        gemini_items = search_gemini(query, max_results=8)
        all_items.extend(gemini_items)
        time.sleep(1)

    # Deduplicate
    unique_items: list[DocItem] = []
    current_hashes: set[str] = set()
    for item in all_items:
        if item.content_hash not in seen_hashes and item.content_hash not in current_hashes:
            unique_items.append(item)
            current_hashes.add(item.content_hash)

    logger.info(f"Collected {len(all_items)} total, {len(unique_items)} after dedup")

    # Classify content types
    unique_items = classify_content_type(unique_items)

    # Score relevance
    unique_items = score_relevance(unique_items)

    # Filter by minimum score
    min_score = gemini_config.get("min_relevance_score", 0.3)
    filtered = [item for item in unique_items if item.relevance_score >= min_score]
    filtered.sort(key=lambda x: x.relevance_score, reverse=True)

    logger.info(f"Filtered to {len(filtered)} items above {min_score} relevance threshold")

    # Save output
    today = datetime.now().strftime("%Y-%m-%d")
    output_file = OUTPUT_DIR / f"{today}.json"
    output_data = {
        "date": today,
        "scan_time": datetime.now(timezone.utc).isoformat(),
        "total_scraped": len(all_items),
        "after_dedup": len(unique_items),
        "after_filter": len(filtered),
        "items": [asdict(item) for item in filtered],
    }
    output_file.write_text(json.dumps(output_data, indent=2, ensure_ascii=False), encoding="utf-8")

    # Update seen hashes
    seen_hashes.update(current_hashes)
    save_seen_hashes(seen_hashes)

    logger.info(f"Output saved to {output_file}")
    return output_file


if __name__ == "__main__":
    output = run_scanner()
    print(f"Gemini docs scan complete: {output}")
