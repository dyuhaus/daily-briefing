"""
Company News Scanner — Per-company AI news via Gemini grounded search.

Covers three dedicated company feeds:
  - Anthropic: Claude models, safety research, API features, partnerships
  - OpenAI:    GPT models, ChatGPT features, API updates, DALL-E, Sora
  - Google AI: Gemini models, DeepMind research, Firebase, Genkit, AI Studio, Imagen, Veo

Config keys: anthropic_news, openai_news, google_ai_news
Output dirs:  output/anthropic/, output/openai/, output/google_ai/
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [CompanyNewsScanner] %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "settings.json"

# Company configuration: maps company slug → (config_key, output_subdir, log_tag)
COMPANY_CONFIG: dict[str, tuple[str, str, str]] = {
    "anthropic": ("anthropic_news", "anthropic", "Anthropic"),
    "openai":    ("openai_news",    "openai",    "OpenAI"),
    "google_ai": ("google_ai_news", "google_ai", "Google AI"),
}

# Per-company LLM scoring descriptions
COMPANY_SCORING_CONTEXT: dict[str, str] = {
    "anthropic": (
        "Anthropic and Claude — Claude model releases, API changes, safety research, "
        "Claude Code features, partnership announcements, pricing updates, Anthropic blog posts"
    ),
    "openai": (
        "OpenAI and its products — GPT model releases, ChatGPT feature updates, API changes, "
        "DALL-E/Sora announcements, OpenAI research papers, pricing, partnerships, safety announcements"
    ),
    "google_ai": (
        "Google AI ecosystem — Gemini model releases, DeepMind research, Firebase/Genkit updates, "
        "Google AI Studio features, Imagen/Veo announcements, Google Cloud AI, Vertex AI updates"
    ),
}

# Per-company keyword signals for fallback scoring
COMPANY_KEYWORD_SIGNALS: dict[str, tuple[list[str], list[str]]] = {
    "anthropic": (
        ["claude", "anthropic", "claude code", "model release", "api update", "constitutional ai",
         "claude sonnet", "claude haiku", "claude opus", "mcp", "safety research"],
        ["llm", "ai assistant", "chatbot", "language model", "fine-tuning", "alignment"],
    ),
    "openai": (
        ["openai", "chatgpt", "gpt-4", "gpt-5", "dall-e", "sora", "whisper", "api update",
         "model release", "o1", "o3", "realtime api", "assistants api"],
        ["llm", "ai assistant", "chatbot", "language model", "multimodal", "reasoning"],
    ),
    "google_ai": (
        ["gemini", "google ai", "deepmind", "firebase", "genkit", "vertex ai", "google cloud ai",
         "imagen", "veo", "ai studio", "google antigravity", "gemini api", "model release"],
        ["llm", "ai assistant", "language model", "multimodal", "search grounding", "fine-tuning"],
    ),
}


@dataclass
class NewsItem:
    title: str
    url: str
    source: str
    author: str
    snippet: str
    timestamp: str
    company: str = ""
    category: str = ""  # model_release, api_update, research, partnership, product, blog, general
    relevance_score: float = 0.0
    content_hash: str = ""

    def compute_hash(self) -> str:
        raw = f"{self.url}{self.title}".lower().strip()
        self.content_hash = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return self.content_hash


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _seen_hashes_path(company: str) -> Path:
    output_dir = PROJECT_ROOT / "output" / COMPANY_CONFIG[company][1]
    return output_dir / ".seen_hashes.json"


def load_seen_hashes(company: str) -> set[str]:
    path = _seen_hashes_path(company)
    if path.exists():
        return set(json.loads(path.read_text(encoding="utf-8")))
    return set()


def save_seen_hashes(company: str, hashes: set[str]) -> None:
    path = _seen_hashes_path(company)
    trimmed = sorted(hashes)[-5000:]
    path.write_text(json.dumps(trimmed, indent=2), encoding="utf-8")


_gemini_client: Optional[GeminiSearchClient] = None


def _get_gemini_client() -> GeminiSearchClient:
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = GeminiSearchClient()
    return _gemini_client


def search_gemini(query: str, max_results: int = 10) -> list[NewsItem]:
    """Search using Gemini grounded search, return raw NewsItems."""
    items: list[NewsItem] = []
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

        logger.info(f"Gemini search returned {len(items)} results (model: {result.model})")

    except Exception as e:
        logger.warning(f"Gemini search failed: {e}")

    return items


def classify_category(items: list[NewsItem]) -> list[NewsItem]:
    """Classify each item into a specific news category."""
    model_signals = ["model release", "new model", "launch", "release candidate", "api v", "preview"]
    api_signals = ["api update", "changelog", "api v", "sdk release", "deprecat", "rate limit", "pricing"]
    research_signals = ["paper", "arxiv", "research", "benchmark", "evaluation", "study", "technical report"]
    partnership_signals = ["partnership", "collaboration", "deal", "agreement", "integrat", "acquisition"]
    product_signals = ["feature", "update", "new in", "introducing", "now available", "generally available"]
    blog_signals = ["blog", "post", "announcement", "newsletter", "thoughts on"]

    for item in items:
        combined = f"{item.url} {item.title} {item.snippet}".lower()

        if any(kw in combined for kw in model_signals):
            item.category = "model_release"
        elif any(kw in combined for kw in api_signals):
            item.category = "api_update"
        elif any(kw in combined for kw in research_signals):
            item.category = "research"
        elif any(kw in combined for kw in partnership_signals):
            item.category = "partnership"
        elif any(kw in combined for kw in product_signals):
            item.category = "product"
        elif any(kw in combined for kw in blog_signals):
            item.category = "blog"
        else:
            item.category = "general"

    return items


def score_relevance(items: list[NewsItem], company: str) -> list[NewsItem]:
    """Score items by relevance to the specific company using LLM."""
    if not items:
        return items

    context = COMPANY_SCORING_CONTEXT[company]

    try:
        from config.llm import llm_call

        batch_text = "\n\n".join(
            f"[{i}] {item.title}\n{item.snippet[:200]}"
            for i, item in enumerate(items)
        )

        prompt = f"""Rate each item's relevance to "{context}" on a 0.0-1.0 scale.

High relevance (0.7-1.0): Direct announcements, new releases, feature updates, official blog posts, significant research papers
Medium relevance (0.4-0.7): Industry analysis referencing this company, third-party coverage with substance
Low relevance (0.0-0.3): Tangential mentions, old news, generic AI coverage, unrelated content

Return ONLY a JSON array of numbers, one per item. Example: [0.8, 0.3, 0.9]

Items:
{batch_text}"""

        text, usage = llm_call(prompt, purpose=f"{company}-scoring")

        if not text:
            logger.warning("LLM returned empty response, falling back to keyword scoring")
            return _keyword_score(items, company)

        start = text.index("[")
        end = text.rindex("]") + 1
        scores = json.loads(text[start:end])

        for i, score in enumerate(scores):
            if i < len(items):
                items[i].relevance_score = float(score)

        logger.info(f"LLM scored {len(items)} items for {company} (backend: {usage.backend})")

    except Exception as e:
        logger.warning(f"LLM scoring failed ({e}), falling back to keyword scoring")
        items = _keyword_score(items, company)

    return items


def _keyword_score(items: list[NewsItem], company: str) -> list[NewsItem]:
    """Keyword-based relevance scoring as fallback."""
    high_signal, medium_signal = COMPANY_KEYWORD_SIGNALS[company]

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


def run_scanner(company: str) -> Path:
    """Execute the full news scanning pipeline for a single company.

    Args:
        company: One of 'anthropic', 'openai', 'google_ai'

    Returns:
        Path to the output JSON file written.
    """
    if company not in COMPANY_CONFIG:
        raise ValueError(f"Unknown company '{company}'. Must be one of: {list(COMPANY_CONFIG.keys())}")

    config_key, output_subdir, log_tag = COMPANY_CONFIG[company]
    output_dir = PROJECT_ROOT / "output" / output_subdir
    output_dir.mkdir(parents=True, exist_ok=True)

    config = load_config()
    company_config = config.get(config_key, {})
    seen_hashes = load_seen_hashes(company)

    logger.info(f"[{log_tag}] Starting scan using config key '{config_key}'")

    all_items: list[NewsItem] = []

    search_queries: list[str] = company_config.get("search_queries", [])
    for query in search_queries:
        gemini_items = search_gemini(query, max_results=8)
        for item in gemini_items:
            item.company = company
        all_items.extend(gemini_items)
        time.sleep(1)

    # Deduplicate against seen and within this run
    unique_items: list[NewsItem] = []
    current_hashes: set[str] = set()
    for item in all_items:
        if item.content_hash not in seen_hashes and item.content_hash not in current_hashes:
            unique_items.append(item)
            current_hashes.add(item.content_hash)

    logger.info(f"[{log_tag}] Collected {len(all_items)} total, {len(unique_items)} after dedup")

    unique_items = classify_category(unique_items)
    unique_items = score_relevance(unique_items, company)

    min_score: float = company_config.get("min_relevance_score", 0.3)
    filtered = [item for item in unique_items if item.relevance_score >= min_score]
    filtered.sort(key=lambda x: x.relevance_score, reverse=True)

    logger.info(f"[{log_tag}] Filtered to {len(filtered)} items above {min_score} relevance threshold")

    today = datetime.now().strftime("%Y-%m-%d")
    output_file = output_dir / f"{today}.json"
    output_data = {
        "date": today,
        "company": company,
        "scan_time": datetime.now(timezone.utc).isoformat(),
        "total_scraped": len(all_items),
        "after_dedup": len(unique_items),
        "after_filter": len(filtered),
        "items": [asdict(item) for item in filtered],
    }
    output_file.write_text(json.dumps(output_data, indent=2, ensure_ascii=False), encoding="utf-8")

    seen_hashes.update(current_hashes)
    save_seen_hashes(company, seen_hashes)

    logger.info(f"[{log_tag}] Output saved to {output_file}")
    return output_file


def run_all_companies() -> dict[str, Path]:
    """Run all three company scanners sequentially.

    Returns:
        Mapping of company slug → output file path.
    """
    results: dict[str, Path] = {}
    for company in COMPANY_CONFIG:
        try:
            output_path = run_scanner(company)
            results[company] = output_path
        except Exception as e:
            logger.error(f"Scanner failed for company '{company}': {e}")
    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Company news scanner")
    parser.add_argument(
        "company",
        nargs="?",
        default="all",
        choices=["all", "anthropic", "openai", "google_ai"],
        help="Company to scan (default: all)",
    )
    args = parser.parse_args()

    if args.company == "all":
        outputs = run_all_companies()
        for company, path in outputs.items():
            print(f"{company}: {path}")
    else:
        output = run_scanner(args.company)
        print(f"Company scan complete: {output}")
