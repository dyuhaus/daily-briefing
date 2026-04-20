"""
Market News Scanner — Gemini grounded search + Nitter fallback.

Covers both algorithmic/quantitative trading methods AND macro market news
(Fed policy, economic indicators, earnings, market moves).

Config key: "market_news" (falls back to "trading" for backward compatibility)
Output dir:  output/market_news/
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

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "Tools" / "GeminiSearch" / "python"))
from config.credentials import get_credential
from config.quota import gemini_quota
from gemini_search import GeminiSearchClient, SearchResult

logging.basicConfig(level=logging.INFO, format="%(asctime)s [MarketNewsScanner] %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output" / "market_news"
CONFIG_PATH = PROJECT_ROOT / "config" / "settings.json"
SEEN_HASHES_FILE = OUTPUT_DIR / ".seen_hashes.json"


@dataclass
class MarketItem:
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
    trimmed = sorted(hashes)[-5000:]
    SEEN_HASHES_FILE.write_text(json.dumps(trimmed, indent=2), encoding="utf-8")


_gemini_client: Optional[GeminiSearchClient] = None


def _get_gemini_client() -> GeminiSearchClient:
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = GeminiSearchClient()
    return _gemini_client


def search_gemini(query: str, max_results: int = 10) -> list[MarketItem]:
    """Search the web using Gemini grounded search."""
    items: list[MarketItem] = []
    try:
        from gemini_search import DEFAULT_MODEL

        client = _get_gemini_client()

        if not gemini_quota.can_call(DEFAULT_MODEL):
            logger.info(f"Quota limit reached — waiting for slot before: '{query}'")
            if not gemini_quota.wait_for_quota(DEFAULT_MODEL):
                logger.warning(f"Quota wait timed out — skipping query: '{query}'")
                return items
        gemini_quota.record_call(DEFAULT_MODEL)

        logger.info(f"Gemini grounded search: '{query}'")
        result = client.search(query, focus="general")

        for source in result.sources[:max_results]:
            item = MarketItem(
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
            item = MarketItem(
                title=query[:200],
                url="",
                source="gemini_grounded",
                author="gemini",
                snippet=result.text[:500],
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            item.compute_hash()
            items.append(item)

        logger.info(f"Gemini search returned {len(items)} results (model: {result.model})")

    except Exception as e:
        logger.warning(f"Gemini search failed: {e}")

    return items


def scrape_nitter(query: str, instances: list[str], max_results: int = 20) -> list[MarketItem]:
    """Scrape Nitter instances for Twitter search results."""
    items: list[MarketItem] = []
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

                    item = MarketItem(
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


def score_relevance(items: list[MarketItem]) -> list[MarketItem]:
    """Score items by relevance using LLM — prioritizes both macro news and algo trading."""
    if not items:
        return items

    try:
        from config.llm import llm_call

        batch_text = "\n\n".join(
            f"[{i}] {item.title}\n{item.snippet[:200]}"
            for i, item in enumerate(items)
        )

        prompt = f"""Rate each item's relevance to "market and financial news — covering BOTH macro economics AND algorithmic/quantitative trading" on a 0.0-1.0 scale.

High relevance (0.7-1.0):
  MACRO: Fed rate decisions, inflation data (CPI/PCE), GDP reports, earnings surprises, major market moves, yield curve changes, recession signals, treasury actions
  ALGO: Novel trading strategies with live/reported performance, new algorithmic approaches, prediction market techniques, quantitative research with actual results

Medium relevance (0.4-0.7): General financial market analysis, AI trading discussion with substance, market microstructure

Low relevance (0.1-0.2): Articles whose PRIMARY focus is backtesting methodology, trading system architecture, or algo trading tutorials — even if they mention quantitative topics. Score 0.6+ only when the article reports actual market-moving events, live trading results, novel strategy performance, or macro/policy news.

Very low relevance (0.0-0.1): Generic financial content, old news, unrelated content, pure opinion without data

Return ONLY a JSON array of numbers, one per item. Example: [0.8, 0.3, 0.9]

Items:
{batch_text}"""

        text, usage = llm_call(prompt, purpose="market-news-scoring")

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


def _keyword_score(items: list[MarketItem]) -> list[MarketItem]:
    """Keyword-based relevance scoring as fallback."""
    # Algo trading signals
    algo_high = [
        "prediction market", "kalshi", "polymarket", "quant", "algorithmic trading",
        "alpha", "edge detection", "kelly criterion", "sharpe",
        "market making", "reinforcement learning trading",
    ]
    # Deprioritized: backtesting methodology / tutorial / architecture articles
    algo_low = [
        "backtesting", "how to backtest", "backtesting framework",
        "trading system architecture", "algo trading tutorial",
        "strategy development workflow", "build a trading bot",
    ]
    # Macro market signals
    macro_high = [
        "fed", "federal reserve", "inflation", "cpi", "pce", "gdp", "earnings",
        "yield", "treasury", "recession", "bull market", "bear market", "rate hike",
        "rate cut", "fomc", "jerome powell", "interest rate", "nonfarm payroll",
    ]
    medium_signal = [
        "ai trading", "machine learning finance", "llm", "neural network",
        "stock prediction", "sentiment analysis", "nlp finance", "markets",
        "economy", "equities", "bonds", "commodities", "forex",
    ]

    for item in items:
        text = f"{item.title} {item.snippet}".lower()
        score = 0.3
        for kw in algo_high:
            if kw.lower() in text:
                score += 0.13
        for kw in macro_high:
            if kw.lower() in text:
                score += 0.13
        for kw in medium_signal:
            if kw.lower() in text:
                score += 0.06
        for kw in algo_low:
            if kw.lower() in text:
                score -= 0.15
        item.relevance_score = min(max(score, 0.0), 1.0)

    return items


def run_scanner() -> Path:
    """Execute the full market news scanning pipeline.

    Reads from 'market_news' config key, falls back to 'trading' for
    backward compatibility. Outputs to output/market_news/.
    """
    config = load_config()
    # Backward-compatible config lookup
    market_config: dict = config.get("market_news", config.get("trading", {}))
    seen_hashes = load_seen_hashes()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    all_items: list[MarketItem] = []

    # Phase 1: Gemini grounded search
    search_queries: list[str] = market_config.get("search_queries", [
        "automated trading new methods strategies 2026",
        "algorithmic trading AI breakthrough research",
        "prediction market automated strategy",
        "Federal Reserve interest rate decision economy",
        "inflation CPI GDP economic data markets",
        "market making AI automated strategies",
        "reinforcement learning trading 2026",
    ])
    for query in search_queries:
        gemini_items = search_gemini(query, max_results=8)
        all_items.extend(gemini_items)
        time.sleep(1)

    # Phase 2: Nitter fallback (algo trading / finance accounts)
    nitter_instances: list[str] = market_config.get("nitter_instances", [])
    nitter_max: int = market_config.get("max_results_per_query", 20)
    # Use only first 3 queries for Nitter to stay within rate limits
    nitter_queries = search_queries[:3]
    for query in nitter_queries:
        nitter_items = scrape_nitter(query, nitter_instances, nitter_max)
        all_items.extend(nitter_items)
        time.sleep(2)

    # Deduplicate
    unique_items: list[MarketItem] = []
    current_hashes: set[str] = set()
    for item in all_items:
        if item.content_hash not in seen_hashes and item.content_hash not in current_hashes:
            unique_items.append(item)
            current_hashes.add(item.content_hash)

    logger.info(f"Collected {len(all_items)} total, {len(unique_items)} after dedup")

    unique_items = score_relevance(unique_items)

    min_score: float = market_config.get("min_relevance_score", 0.3)
    filtered = [item for item in unique_items if item.relevance_score >= min_score]
    filtered.sort(key=lambda x: x.relevance_score, reverse=True)

    logger.info(f"Filtered to {len(filtered)} items above {min_score} relevance threshold")

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

    seen_hashes.update(current_hashes)
    save_seen_hashes(seen_hashes)

    logger.info(f"Output saved to {output_file}")
    return output_file


if __name__ == "__main__":
    output = run_scanner()
    print(f"Market news scan complete: {output}")
