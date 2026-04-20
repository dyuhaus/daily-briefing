"""
Synthesizer — Converts raw scanner data into cliff notes and Brain entries.

Split into:
  synthesizer_utils.py    — shared data types and utility functions
  synthesizer_sections.py — per-section LLM synthesis functions (sections 1-7)
  synthesizer.py          — main pipeline (run_synthesis, index_workflows_to_brain)

Re-exports all public names for backward compatibility with compiler.py.

Original sections:
1. Anthropic News — Claude model releases, safety research, API updates, partnerships
2. OpenAI News — GPT models, ChatGPT, API updates, research, DALL-E/Sora
3. Google AI News — Gemini models, DeepMind, Firebase Studio, Genkit, AI Studio
4. AI Industry News — broad AI (startups, open source, GitHub repos, policy, Meta, Microsoft, HuggingFace)
5. Project Applicability — how today's AI news applies to current projects
6. Market News — macro news (Fed, economy, earnings) + algo trading methods
7. Claude Code Intel — workflows and techniques
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Re-export all public names so existing callers (compiler.py) still work
from briefing.synthesizer_utils import (
    DigestSection,
    WorkflowEntry,
    SynthesizedDigest,
    _load_project_context,
    _load_scanner_data,
    _load_recent_digest_history,
    _load_editorial_items,
    _build_dedup_context,
    _save_digest_json,
    _parse_json_response,
)
from briefing.synthesizer_sections import (
    synthesize_anthropic_news,
    synthesize_openai_news,
    synthesize_google_ai_news,
    synthesize_ai_industry,
    synthesize_project_applicability,
    synthesize_market_news,
    synthesize_claude_workflows,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [Synthesizer] %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BRAIN_KNOWLEDGE = Path("<workspace>/AI_Brain/Knowledge")


# ---------------------------------------------------------------------------
# Brain indexing
# ---------------------------------------------------------------------------

def index_workflows_to_brain(workflows: list[WorkflowEntry]) -> int:
    """Save new Claude Code workflow discoveries to the Brain vault."""
    if not workflows:
        return 0

    indexed = 0
    today = datetime.now().strftime("%Y-%m-%d")
    discoveries_dir = BRAIN_KNOWLEDGE / "Tools" / "discoveries"
    discoveries_dir.mkdir(parents=True, exist_ok=True)

    for wf in workflows:
        safe_name = wf.name.lower().replace(" ", "-").replace("/", "-")[:50]
        file_path = discoveries_dir / f"{today}_{safe_name}.md"

        content = f"""---
tags: [discovery, claude-code, {today}]
---
# {wf.name}

**Discovered**: {today}
**Source**: [{wf.source_title}]({wf.source_url})

## Description
{wf.description}

## Use Cases
"""
        for uc in wf.use_cases:
            content += f"- {uc}\n"

        content += f"""
## Applicable Projects
"""
        for proj in wf.applicable_projects:
            content += f"- [[Projects/{proj}/overview|{proj}]]\n"

        content += f"""
## Synapses
- [[Knowledge/Tools/global-skills|Global Skills Directory]]
"""

        file_path.write_text(content, encoding="utf-8")
        indexed += 1
        logger.info(f"Indexed workflow to Brain: {wf.name}")

    return indexed


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_synthesis() -> SynthesizedDigest:
    """Run the full synthesis pipeline."""
    project_context = _load_project_context()

    anthropic_data = _load_scanner_data(PROJECT_ROOT / "output" / "anthropic")
    openai_data = _load_scanner_data(PROJECT_ROOT / "output" / "openai")
    google_ai_data = _load_scanner_data(PROJECT_ROOT / "output" / "google_ai")
    ai_industry_data = _load_scanner_data(PROJECT_ROOT / "output" / "ai_industry")
    market_data = _load_scanner_data(PROJECT_ROOT / "output" / "market_news")
    youtube_data = _load_scanner_data(PROJECT_ROOT / "output" / "youtube")

    # Merge editorial items into AI industry data so they appear in the newsletter.
    editorial_items = _load_editorial_items()
    if editorial_items:
        if ai_industry_data is None:
            ai_industry_data = {"items": []}
        existing_urls = {item.get("url", "") for item in ai_industry_data.get("items", [])}
        new_editorial = [
            item for item in editorial_items
            if item.get("url", "") not in existing_urls or item.get("url", "") == ""
        ]
        ai_industry_data["items"] = new_editorial + ai_industry_data.get("items", [])
        logger.info(f"Injected {len(new_editorial)} editorial item(s) into AI industry feed")

    digest = SynthesizedDigest(date=datetime.now().strftime("%Y-%m-%d"))

    # 1. Anthropic News
    logger.info("Synthesizing Anthropic News...")
    digest.anthropic_news = synthesize_anthropic_news(anthropic_data)

    # 2. OpenAI News
    logger.info("Synthesizing OpenAI News...")
    digest.openai_news = synthesize_openai_news(openai_data)

    # 3. Google AI News
    logger.info("Synthesizing Google AI News...")
    digest.google_ai_news = synthesize_google_ai_news(google_ai_data)

    # 4. General AI Industry News
    logger.info("Synthesizing General AI Industry News...")
    digest.ai_industry = synthesize_ai_industry(ai_industry_data)

    # 5. Project Applicability (depends on AI Industry section)
    logger.info("Synthesizing Project Applicability...")
    digest.project_applicability = synthesize_project_applicability(
        digest.ai_industry, project_context
    )

    # 6. Market News
    logger.info("Synthesizing Market News...")
    digest.market_news = synthesize_market_news(market_data)

    # 7. Claude Code Intel
    logger.info("Synthesizing Claude Code workflows...")
    claude_section, workflows = synthesize_claude_workflows(youtube_data, project_context)
    digest.claude_workflows = claude_section
    digest.applicable_workflows = workflows

    # Index workflows to Brain
    if workflows:
        count = index_workflows_to_brain(workflows)
        logger.info(f"Indexed {count} new workflows to Brain")

    # Save digest JSON
    _save_digest_json(digest)

    return digest


# ---------------------------------------------------------------------------
# Stub — old section bodies removed, now live in synthesizer_sections.py
# ---------------------------------------------------------------------------
