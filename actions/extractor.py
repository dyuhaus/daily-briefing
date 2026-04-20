"""
Action Item Extractor — identifies actionable recommendations from briefing output.

Reads the daily digest JSON (output/digests/YYYY-MM-DD.json) and _Inbox.md
to extract items that can be implemented as code changes or system improvements.
"""
import json
import os
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

# Action verbs that indicate an actionable recommendation
ACTION_VERBS = frozenset({
    "implement", "add", "create", "build", "fix", "improve", "update",
    "refactor", "integrate", "migrate", "replace", "remove", "optimize",
    "configure", "set up", "install", "deploy", "automate", "investigate",
    "research", "evaluate", "test", "validate", "monitor", "schedule",
})

# Project name detection keywords (same as session_manager.py)
PROJECT_KEYWORDS: dict[str, list[str]] = {
    "IHTC": ["ihtc", "consulting", "voice receptionist", "fiverr", "chatbot"],
    "FORGE": ["forge", "strategy discovery", "launch queue"],
    "MarketSwarm": ["marketswarm", "algoswarm", "regime filter", "backtest"],
    "SportsBettingSwarm": ["sports betting", "kalshi bet", "wager"],
    "QuantMarketData": ["kalshi", "paper trader", "prediction market"],
    "DailyBriefing": ["daily briefing", "newsletter", "scanner"],
    "MomentumWatch": ["momentum", "stock", "portfolio", "sell monitor"],
    "BookSummarizer": ["book summarizer", "knowledge base"],
    "GameCreationPlatform": ["game creation", "google ai studio"],
}

@dataclass(frozen=True)
class ActionItem:
    id: str
    title: str
    description: str
    priority: int  # 1=highest
    source: str  # "briefing:section_name" or "inbox:todo"
    target_project: Optional[str] = None

def _detect_project(text: str) -> Optional[str]:
    text_lower = text.lower()
    scores: dict[str, int] = {}
    for project, keywords in PROJECT_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[project] = score
    return max(scores, key=scores.get) if scores else None

def _is_actionable(text: str) -> bool:
    text_lower = text.lower()
    return any(verb in text_lower for verb in ACTION_VERBS)

def _extract_from_digest(digest_path: str) -> list[dict]:
    """Extract actionable insights from digest JSON sections."""
    if not os.path.exists(digest_path):
        return []
    with open(digest_path, "r", encoding="utf-8") as f:
        digest = json.load(f)

    items = []
    for section_name in ["anthropic_news", "openai_news", "google_ai_news",
                          "ai_industry", "project_applicability", "market_news",
                          "claude_workflows"]:
        section = digest.get(section_name)
        if not section:
            continue
        insights = section.get("key_insights", [])
        for insight in insights:
            if _is_actionable(insight):
                items.append({
                    "title": insight[:100],
                    "description": insight,
                    "source": f"briefing:{section_name}",
                    "project": _detect_project(insight),
                })
        # Also check cliff_notes for actionable content
        cliff = section.get("cliff_notes", "")
        sentences = [s.strip() for s in cliff.split(".") if s.strip()]
        for sentence in sentences:
            if _is_actionable(sentence) and len(sentence) > 30:
                items.append({
                    "title": sentence[:100],
                    "description": sentence,
                    "source": f"briefing:{section_name}",
                    "project": _detect_project(sentence),
                })
    return items

def _extract_from_inbox(inbox_path: str) -> list[dict]:
    """Extract TODO items from _Inbox.md."""
    if not os.path.exists(inbox_path):
        return []
    content = Path(inbox_path).read_text(encoding="utf-8")
    items = []
    # Match ## TODO sections and ## Idea sections
    sections = re.split(r'\n(?=## )', content)
    for section in sections:
        header = section.split("\n")[0].strip()
        if "TODO" in header or "Idea" in header:
            body = "\n".join(section.split("\n")[1:]).strip()
            if body and len(body) > 20:
                items.append({
                    "title": header[:100],
                    "description": body[:500],
                    "source": "inbox:todo",
                    "project": _detect_project(body),
                })
    return items

def _load_completed(completed_path: str) -> set[str]:
    """Load titles of recently completed actions to avoid duplicates."""
    if not os.path.exists(completed_path):
        return set()
    try:
        with open(completed_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {item.get("title", "").lower() for item in data}
    except (json.JSONDecodeError, OSError):
        return set()

def _generate_id(counter_path: str) -> str:
    """Generate sequential action ID like act-001."""
    counter = 1
    if os.path.exists(counter_path):
        try:
            with open(counter_path, "r") as f:
                counter = json.load(f).get("next", 1)
        except (json.JSONDecodeError, OSError):
            pass
    os.makedirs(os.path.dirname(counter_path), exist_ok=True)
    with open(counter_path, "w") as f:
        json.dump({"next": counter + 1}, f)
    return f"act-{counter:03d}"

def extract_actions(
    digest_path: str,
    inbox_path: str,
    actions_dir: str = "",
    max_items: int = 10,
) -> list[ActionItem]:
    """Extract and priority-rank actionable items from today's briefing."""
    if not actions_dir:
        actions_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)))

    completed = _load_completed(os.path.join(actions_dir, "completed.json"))
    counter_path = os.path.join(actions_dir, "_counter.json")

    raw_items = _extract_from_digest(digest_path) + _extract_from_inbox(inbox_path)

    # Deduplicate against completed
    unique = [item for item in raw_items if item["title"].lower() not in completed]

    # Deduplicate by title similarity (exact match)
    seen_titles: set[str] = set()
    deduped = []
    for item in unique:
        key = item["title"].lower()[:80]
        if key not in seen_titles:
            seen_titles.add(key)
            deduped.append(item)

    # Priority scoring: project-specific > general, briefing > inbox
    def _score(item: dict) -> int:
        score = 0
        if item["project"]:
            score += 10  # project-specific is higher priority
        if "project_applicability" in item["source"]:
            score += 5  # explicitly flagged as applicable
        if "inbox" in item["source"]:
            score += 3  # user-submitted ideas
        return score

    deduped.sort(key=_score, reverse=True)

    # Convert to ActionItem with generated IDs
    actions = []
    for i, item in enumerate(deduped[:max_items]):
        actions.append(ActionItem(
            id=_generate_id(counter_path),
            title=item["title"],
            description=item["description"],
            priority=i + 1,
            source=item["source"],
            target_project=item["project"],
        ))

    return actions
