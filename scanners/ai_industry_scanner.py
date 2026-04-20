"""
AI Industry Scanner — Broad AI industry news via Gemini grounded search.

Covers: major org announcements (OpenAI, Anthropic, Google, Meta, etc.),
new AI startups, trending GitHub repos for AI agents, AI frameworks,
and general industry developments.
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
from dataclasses import dataclass, asdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "Tools" / "GeminiSearch" / "python"))
from config.credentials import get_credential
from config.quota import gemini_quota
from gemini_search import GeminiSearchClient, SearchResult

logging.basicConfig(level=logging.INFO, format="%(asctime)s [AIIndustryScanner] %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output" / "ai_industry"
CONFIG_PATH = PROJECT_ROOT / "config" / "settings.json"
SEEN_HASHES_FILE = OUTPUT_DIR / ".seen_hashes.json"


@dataclass
class NewsItem:
    title: str
    url: str
    source: str
    author: str
    snippet: str
    timestamp: str
    category: str = ""  # "startup", "major_org", "github", "framework", "research", "general"
    relevance_score: float = 0.0
    content_hash: str = ""

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


_gemini_client: Optional[GeminiSearchClient] = None


def _get_gemini_client() -> GeminiSearchClient:
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = GeminiSearchClient()
    return _gemini_client


def search_gemini(query: str, max_results: int = 10) -> list[NewsItem]:
    """Search using Gemini grounded search."""
    items: list[NewsItem] = []
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

        result = client.search(query, focus="general")

        for source in result.sources[:max_results]:
            item = NewsItem(
                title=source.title[:200],
                url=source.url,
                source="gemini_grounded",
                author=source.url.split("/")[2] if "/" in source.url else "web",
                snippet=result.text[:500],
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            item.compute_hash()
            items.append(item)

        if not items and result.text:
            item = NewsItem(
                title=query[:200],
                url="",
                source="gemini_grounded",
                author="gemini",
                snippet=result.text[:500],
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            item.compute_hash()
            items.append(item)

        logger.info(f"Gemini grounded search returned {len(items)} results (model: {result.model})")

    except Exception as e:
        logger.warning(f"Gemini grounded search failed: {e}")

    return items


def classify_category(items: list[NewsItem]) -> list[NewsItem]:
    """Classify each item into a news category based on URL and title patterns."""
    major_orgs = ["openai", "anthropic", "google", "meta", "microsoft", "nvidia", "deepmind", "mistral", "cohere", "xai"]
    github_signals = ["github.com", "github trending", "open source", "repository", "starred"]
    startup_signals = ["startup", "funding", "seed round", "series a", "series b", "launch", "raised", "yc", "y combinator"]
    framework_signals = ["framework", "sdk", "library", "toolkit", "agent framework", "langchain", "crewai", "autogen"]
    research_signals = ["paper", "arxiv", "research", "benchmark", "evaluation", "study"]

    for item in items:
        combined = f"{item.url} {item.title} {item.snippet}".lower()

        if any(kw in combined for kw in github_signals):
            item.category = "github"
        elif any(kw in combined for kw in startup_signals):
            item.category = "startup"
        elif any(org in combined for org in major_orgs):
            item.category = "major_org"
        elif any(kw in combined for kw in framework_signals):
            item.category = "framework"
        elif any(kw in combined for kw in research_signals):
            item.category = "research"
        else:
            item.category = "general"

    return items


def score_relevance(items: list[NewsItem]) -> list[NewsItem]:
    """Score items by relevance to AI industry developments using LLM."""
    if not items:
        return items

    try:
        from config.llm import llm_call

        batch_text = "\n\n".join(
            f"[{i}] {item.title}\n{item.snippet[:200]}"
            for i, item in enumerate(items)
        )

        prompt = f"""Rate each item's relevance to "significant AI industry developments — new products, features, startups, open-source tools, GitHub repos, and organizational announcements" on a 0.0-1.0 scale.

High relevance (0.7-1.0): New product launches, major feature releases from OpenAI/Anthropic/Google/Meta, trending AI agent repos on GitHub, notable AI startup funding/launches, new AI frameworks
Medium relevance (0.4-0.7): Industry analysis, opinion pieces with substance, AI policy developments, conference announcements
Low relevance (0.0-0.3): Old news, generic AI hype, unrelated content, paywalled with no substance

Return ONLY a JSON array of numbers, one per item. Example: [0.8, 0.3, 0.9]

Items:
{batch_text}"""

        text, usage = llm_call(prompt, purpose="ai-industry-scoring")

        if not text:
            logger.warning("LLM returned empty response, falling back to keyword scoring")
            return _keyword_score(items)

        start = text.index("[")
        end = text.rindex("]") + 1
        scores = json.loads(text[start:end])

        for i, score in enumerate(scores):
            if i < len(items):
                items[i].relevance_score = float(score)

        logger.info(f"LLM scored {len(items)} items (backend: {usage.backend})")

    except Exception as e:
        logger.warning(f"LLM scoring failed ({e}), falling back to keyword scoring")
        items = _keyword_score(items)

    return items


def _keyword_score(items: list[NewsItem]) -> list[NewsItem]:
    """Keyword-based relevance scoring as fallback."""
    high_signal = [
        "openai", "anthropic", "claude", "gpt-5", "gpt-4", "gemini",
        "ai agent", "agent framework", "github trending", "open source ai",
        "series a", "series b", "seed funding", "ai startup",
        "model release", "new feature", "api update", "sdk release",
    ]
    medium_signal = [
        "artificial intelligence", "machine learning", "deep learning",
        "llm", "large language model", "transformer", "fine-tuning",
        "rag", "vector database", "embedding", "multimodal",
    ]

    for item in items:
        text = f"{item.title} {item.snippet}".lower()
        score = 0.3
        for kw in high_signal:
            if kw in text:
                score += 0.12
        for kw in medium_signal:
            if kw in text:
                score += 0.06
        item.relevance_score = min(score, 1.0)

    return items


def run_scanner() -> Path:
    """Execute the full AI industry scanning pipeline."""
    config = load_config()
    ai_config = config.get("ai_industry", {})
    seen_hashes = load_seen_hashes()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    all_items: list[NewsItem] = []

    # Broad AI industry queries via Gemini grounded search
    search_queries = ai_config.get("search_queries", [
        "AI industry news today 2026",
        "OpenAI new features announcements 2026",
        "Anthropic Claude new features updates 2026",
        "Google AI announcements Gemini updates 2026",
        "AI startup funding launch 2026",
        "trending AI agent GitHub repositories",
        "new AI agent frameworks tools 2026",
        "Meta AI open source releases 2026",
        "AI coding assistant new features 2026",
        "AI industry acquisitions partnerships 2026",
    ])

    for query in search_queries:
        gemini_items = search_gemini(query, max_results=8)
        all_items.extend(gemini_items)
        time.sleep(1)

    # Deduplicate
    unique_items: list[NewsItem] = []
    current_hashes: set[str] = set()
    for item in all_items:
        if item.content_hash not in seen_hashes and item.content_hash not in current_hashes:
            unique_items.append(item)
            current_hashes.add(item.content_hash)

    logger.info(f"Collected {len(all_items)} total, {len(unique_items)} after dedup")

    # Classify categories
    unique_items = classify_category(unique_items)

    # Score relevance
    unique_items = score_relevance(unique_items)

    # Filter by minimum score
    min_score = ai_config.get("min_relevance_score", 0.3)
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
    print(f"AI industry scan complete: {output}")
