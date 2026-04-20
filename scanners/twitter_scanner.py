"""
Automated Trading Scanner — Gemini grounded search + Nitter fallback.
Searches for new methods, strategies, and ideas in the automated/algorithmic trading space.
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
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "Tools" / "GeminiSearch" / "python"))
from config.credentials import get_credential, require_credential
from config.quota import gemini_quota
from gemini_search import GeminiSearchClient, SearchResult

logging.basicConfig(level=logging.INFO, format="%(asctime)s [TwitterScanner] %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output" / "twitter"
CONFIG_PATH = PROJECT_ROOT / "config" / "settings.json"
SEEN_HASHES_FILE = OUTPUT_DIR / ".seen_hashes.json"


@dataclass
class ScrapedItem:
    title: str
    url: str
    source: str
    author: str
    snippet: str
    timestamp: str
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
    # Keep last 5000 hashes to prevent unbounded growth
    trimmed = sorted(hashes)[-5000:]
    SEEN_HASHES_FILE.write_text(json.dumps(trimmed, indent=2), encoding="utf-8")


def scrape_nitter(query: str, instances: list[str], max_results: int = 20) -> list[ScrapedItem]:
    """Scrape Nitter instances for Twitter search results."""
    items: list[ScrapedItem] = []
    encoded_query = requests.utils.quote(query)

    for instance in instances:
        try:
            url = f"{instance}/search?f=tweets&q={encoded_query}"
            logger.info(f"Scraping Nitter: {instance} for '{query}'")
            resp = requests.get(url, timeout=15, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            if resp.status_code != 200:
                logger.warning(f"Nitter {instance} returned {resp.status_code}, skipping")
                continue

            soup = BeautifulSoup(resp.text, "lxml")
            tweets = soup.select(".timeline-item")

            for tweet in tweets[:max_results]:
                try:
                    username_el = tweet.select_one(".username")
                    content_el = tweet.select_one(".tweet-content")
                    link_el = tweet.select_one(".tweet-link")
                    time_el = tweet.select_one(".tweet-date a")

                    if not content_el:
                        continue

                    username = username_el.get_text(strip=True) if username_el else "unknown"
                    content = content_el.get_text(strip=True)
                    tweet_path = link_el.get("href", "") if link_el else ""
                    tweet_url = f"https://x.com{tweet_path}" if tweet_path else ""
                    timestamp = time_el.get("title", "") if time_el else ""

                    item = ScrapedItem(
                        title=content[:120] + ("..." if len(content) > 120 else ""),
                        url=tweet_url,
                        source="nitter",
                        author=username,
                        snippet=content[:500],
                        timestamp=timestamp,
                    )
                    item.compute_hash()
                    items.append(item)
                except Exception as e:
                    logger.debug(f"Failed to parse tweet: {e}")
                    continue

            if items:
                logger.info(f"Got {len(items)} results from {instance}")
                break  # Use first working instance

        except requests.RequestException as e:
            logger.warning(f"Nitter {instance} failed: {e}")
            continue

    return items


def _get_gemini_client() -> GeminiSearchClient:
    """Get or create the shared Gemini search client."""
    global _gemini_client
    if "_gemini_client" not in globals() or _gemini_client is None:
        _gemini_client = GeminiSearchClient()
    return _gemini_client


_gemini_client: Optional[GeminiSearchClient] = None


def search_gemini(query: str, max_results: int = 10) -> list[ScrapedItem]:
    """Search the web using Gemini grounded search."""
    items: list[ScrapedItem] = []
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

        # Each grounding source becomes a ScrapedItem
        for source in result.sources[:max_results]:
            item = ScrapedItem(
                title=source.title[:200],
                url=source.url,
                source="gemini_grounded",
                author=source.url.split("/")[2] if "/" in source.url else "web",
                snippet=result.text[:500],
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            item.compute_hash()
            items.append(item)

        # If grounding returned fewer sources than expected, create one item
        # from the synthesized response itself
        if not items and result.text:
            item = ScrapedItem(
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


def score_relevance(items: list[ScrapedItem]) -> list[ScrapedItem]:
    """Score items by relevance using Claude Code CLI (or API fallback)."""
    if not items:
        return items

    try:
        from config.llm import llm_call

        batch_text = "\n\n".join(
            f"[{i}] {item.title}\n{item.snippet[:200]}"
            for i, item in enumerate(items)
        )

        prompt = f"""Rate each item's relevance to "new methods, strategies, and techniques for automated/algorithmic trading, prediction markets, and quantitative finance" on a 0.0-1.0 scale.

High relevance (0.7-1.0): Novel trading strategies, new algorithmic approaches, backtesting methods, prediction market techniques, quantitative research breakthroughs
Medium relevance (0.4-0.7): General AI trading discussion with some substance, market microstructure analysis
Low relevance (0.0-0.3): Generic AI hype, old news, unrelated content

Return ONLY a JSON array of numbers, one per item. Example: [0.8, 0.3, 0.9]

Items:
{batch_text}"""

        text, usage = llm_call(prompt, purpose="twitter-scoring")

        if not text:
            logger.warning("LLM returned empty response, falling back to keyword scoring")
            return _keyword_score(items)

        # Extract JSON array from response
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


def _keyword_score(items: list[ScrapedItem]) -> list[ScrapedItem]:
    """Simple keyword-based relevance scoring as fallback."""
    high_signal = ["prediction market", "kalshi", "polymarket", "quant", "algorithmic trading",
                   "backtesting", "alpha", "edge detection", "kelly criterion", "sharpe"]
    medium_signal = ["AI trading", "machine learning finance", "LLM", "neural network",
                     "stock prediction", "sentiment analysis", "NLP finance"]

    for item in items:
        text = f"{item.title} {item.snippet}".lower()
        score = 0.3  # base
        for kw in high_signal:
            if kw.lower() in text:
                score += 0.15
        for kw in medium_signal:
            if kw.lower() in text:
                score += 0.08
        item.relevance_score = min(score, 1.0)

    return items


def run_scanner() -> Path:
    """Execute the full Twitter scanning pipeline. Returns path to output file."""
    config = load_config()
    twitter_config = config.get("trading", config.get("twitter", {}))
    seen_hashes = load_seen_hashes()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    all_items: list[ScrapedItem] = []

    # Phase 1: Gemini grounded search for automated trading methods
    trading_config = config.get("trading", config.get("twitter", {}))
    gemini_queries = trading_config.get("search_queries", [
        "automated trading new methods strategies 2026",
        "algorithmic trading AI breakthrough research",
        "prediction market automated strategy",
        "quantitative trading machine learning new",
        "AI trading system architecture design",
        "sports betting algorithm quantitative methods",
        "market making AI automated strategies",
        "reinforcement learning trading 2026",
    ])
    for query in gemini_queries:
        gemini_items = search_gemini(query, max_results=8)
        all_items.extend(gemini_items)
        time.sleep(1)

    # Phase 2: Nitter fallback (if any instances are alive)
    nitter_instances = trading_config.get("nitter_instances", twitter_config.get("nitter_instances", []))
    nitter_max = trading_config.get("max_results_per_query", twitter_config.get("max_results_per_query", 20))
    nitter_queries = trading_config.get("search_queries", twitter_config.get("search_queries", []))[:3]
    for query in nitter_queries:
        nitter_items = scrape_nitter(
            query,
            nitter_instances,
            nitter_max,
        )
        all_items.extend(nitter_items)
        time.sleep(2)

    # Deduplicate
    unique_items: list[ScrapedItem] = []
    current_hashes: set[str] = set()
    for item in all_items:
        if item.content_hash not in seen_hashes and item.content_hash not in current_hashes:
            unique_items.append(item)
            current_hashes.add(item.content_hash)

    logger.info(f"Collected {len(all_items)} total, {len(unique_items)} after dedup")

    # Score relevance
    unique_items = score_relevance(unique_items)

    # Filter by minimum score
    min_score = twitter_config["min_relevance_score"]
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
    print(f"Twitter scan complete: {output}")
