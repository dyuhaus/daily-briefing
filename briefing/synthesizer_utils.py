"""
briefing/synthesizer_utils.py — Shared data types and utility functions for the synthesizer.

Contains: DigestSection, WorkflowEntry, SynthesizedDigest dataclasses,
and helper functions for loading history, building dedup context, parsing JSON.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class DigestSection:
    title: str
    cliff_notes: str
    key_insights: list[str]
    source_count: int = 0


@dataclass
class WorkflowEntry:
    name: str
    description: str
    use_cases: list[str]
    applicable_projects: list[str]
    source_url: str
    source_title: str


@dataclass
class SynthesizedDigest:
    date: str
    anthropic_news: DigestSection | None = None
    openai_news: DigestSection | None = None
    google_ai_news: DigestSection | None = None
    ai_industry: DigestSection | None = None
    project_applicability: DigestSection | None = None
    market_news: DigestSection | None = None
    claude_workflows: DigestSection | None = None
    applicable_workflows: list[WorkflowEntry] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _load_project_context() -> dict:
    """Load current project stacks and priorities for relevance filtering."""
    project_context_path = PROJECT_ROOT / "config" / "project_context.json"
    if project_context_path.exists():
        return json.loads(project_context_path.read_text(encoding="utf-8"))
    return {}


def _load_scanner_data(scanner_dir: Path) -> dict | None:
    """Load today's scanner output."""
    today = datetime.now().strftime("%Y-%m-%d")
    today_file = scanner_dir / f"{today}.json"
    if today_file.exists():
        return json.loads(today_file.read_text(encoding="utf-8"))
    return None


def _load_recent_digest_history(days_back: int = 7) -> list[dict]:
    """Load cliff notes from recent newsletters to prevent repeat content."""
    history: list[dict] = []
    today = datetime.now()
    digest_dir = PROJECT_ROOT / "output" / "digests"
    newsletter_dir = PROJECT_ROOT / "output" / "newsletters"

    for days_ago in range(1, days_back + 1):
        date = (today - __import__("datetime").timedelta(days=days_ago)).strftime("%Y-%m-%d")

        digest_file = digest_dir / f"{date}.json"
        if digest_file.exists():
            try:
                data = json.loads(digest_file.read_text(encoding="utf-8"))
                history.append({
                    "date": date,
                    "anthropic_notes": data.get("anthropic_news", {}).get("cliff_notes", ""),
                    "openai_notes": data.get("openai_news", {}).get("cliff_notes", ""),
                    "google_ai_notes": data.get("google_ai_news", {}).get("cliff_notes", ""),
                    "ai_industry_notes": data.get("ai_industry", {}).get("cliff_notes", ""),
                    "market_notes": data.get("market_news", {}).get("cliff_notes", ""),
                    "claude_notes": data.get("claude_workflows", {}).get("cliff_notes", ""),
                    # Legacy fields for backward compat with old digest format
                    "trading_notes": data.get("trading_methods", {}).get("cliff_notes", ""),
                    "gemini_notes": data.get("gemini_platform", {}).get("cliff_notes", ""),
                    "ai_markets_notes": data.get("ai_markets", {}).get("cliff_notes", ""),
                })
                continue
            except (json.JSONDecodeError, OSError):
                pass

        html_file = newsletter_dir / f"{date}.html"
        if html_file.exists():
            try:
                html = html_file.read_text(encoding="utf-8")
                notes_match = re.search(
                    r'<div class="cliff-notes">(.*?)</div>',
                    html, re.DOTALL
                )
                if notes_match:
                    raw = notes_match.group(1)
                    plain = re.sub(r'<[^>]+>', ' ', raw).strip()
                    plain = re.sub(r'\s+', ' ', plain)[:1000]
                    history.append({
                        "date": date,
                        "anthropic_notes": "",
                        "openai_notes": "",
                        "google_ai_notes": "",
                        "ai_industry_notes": plain,
                        "market_notes": "",
                        "claude_notes": "",
                        "trading_notes": "",
                        "gemini_notes": "",
                    })
            except OSError:
                pass

    return history


def _load_editorial_items() -> list[dict]:
    """
    Load editorial JSON files from output/editorial/.

    Editorial items are manually curated and are NEVER overwritten by scanner runs.
    """
    editorial_dir = PROJECT_ROOT / "output" / "editorial"
    if not editorial_dir.exists():
        return []

    items: list[dict] = []
    for json_file in sorted(editorial_dir.glob("*.json")):
        try:
            item = json.loads(json_file.read_text(encoding="utf-8"))
            item.setdefault("source", "editorial")
            item.setdefault("relevance_score", 0.9)
            item.setdefault("snippet", "")
            item.setdefault("url", "")
            item.setdefault("category", "editorial")
            items.append(item)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load editorial file {json_file.name}: {e}")

    items.sort(key=lambda x: x.get("relevance_score", 0.0), reverse=True)
    logger.info(f"Loaded {len(items)} editorial item(s) from {editorial_dir}")
    return items


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_dedup_context(recent_history: list[dict], field_name: str, max_days: int = 5) -> str:
    """Build deduplication context for a given section field."""
    dedup_lines: list[str] = []
    for h in recent_history[:max_days]:
        notes = h.get(field_name, "")[:500]
        if notes:
            dedup_lines.append(f"[{h['date']}] {notes}")
    if not dedup_lines:
        return ""
    joined = "\n".join(dedup_lines)
    return f"""
PREVIOUSLY COVERED (DO NOT REPEAT):
The following themes were already covered recently. Do NOT repeat these. Find genuinely NEW angles or skip.

{joined}

END OF PREVIOUS COVERAGE.
"""


def _save_digest_json(digest: SynthesizedDigest) -> None:
    """Save today's digest as JSON for future deduplication lookups."""
    digest_dir = PROJECT_ROOT / "output" / "digests"
    digest_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")

    def _section_dict(section: DigestSection | None) -> dict:
        if not section:
            return {"cliff_notes": "", "key_insights": [], "source_count": 0}
        return {
            "cliff_notes": section.cliff_notes,
            "key_insights": section.key_insights,
            "source_count": section.source_count,
        }

    data = {
        "date": today,
        "anthropic_news": _section_dict(digest.anthropic_news),
        "openai_news": _section_dict(digest.openai_news),
        "google_ai_news": _section_dict(digest.google_ai_news),
        "ai_industry": _section_dict(digest.ai_industry),
        "project_applicability": _section_dict(digest.project_applicability),
        "market_news": _section_dict(digest.market_news),
        "claude_workflows": _section_dict(digest.claude_workflows),
    }

    filepath = digest_dir / f"{today}.json"
    filepath.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"Saved digest JSON for deduplication: {filepath}")


def _parse_json_response(response: str) -> dict:
    """Extract and parse JSON from an LLM response, handling markdown wrapping."""
    start = response.index("{")
    depth = 0
    end = start
    for i in range(start, len(response)):
        if response[i] == "{":
            depth += 1
        elif response[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end <= start:
        raise ValueError("No matching closing brace found")
    return json.loads(response[start:end])
