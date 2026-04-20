"""
briefing/synthesizer_sections.py — Per-section LLM synthesis functions.

Each function takes raw scanner data and returns a DigestSection.
Sections 1-7 correspond to the newsletter structure:
  1. Anthropic News
  2. OpenAI News
  3. Google AI News
  4. AI Industry News
  5. Project Applicability
  6. Market News
  7. Claude Code Intel
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.llm import llm_call
from briefing.synthesizer_utils import (
    DigestSection,
    WorkflowEntry,
    _load_recent_digest_history,
    _build_dedup_context,
    _parse_json_response,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Section 1: Anthropic News
# ---------------------------------------------------------------------------

def synthesize_anthropic_news(data: dict | None) -> DigestSection | None:
    """Synthesize Anthropic-specific news into cliff notes."""
    if not data or not data.get("items"):
        return None

    items = data["items"]
    source_count = len(items)

    articles_text = "\n\n".join(
        f"- [{item.get('category', 'general')}] {item['title']}\n  Source: {item.get('author', 'unknown')}\n  Snippet: {item.get('snippet', '')[:300]}"
        for item in items[:15]
    )

    recent_history = _load_recent_digest_history(days_back=7)
    dedup_context = _build_dedup_context(recent_history, "anthropic_notes")

    today_str = datetime.now().strftime("%A, %B %d, %Y")
    prompt = f"""You are writing the "Anthropic News" section of a daily intelligence briefing for {today_str}.
The reader builds autonomous AI agent systems using Claude and the Anthropic API.

{dedup_context}
Below are {source_count} articles about Anthropic developments.

FORMATTING RULES:
- Write in plain text. Do NOT use markdown formatting (no **bold**, no *italic*, no bullet markers).
- Use em dashes for parenthetical asides, not hyphens.
- Write naturally like a written memo, not a dashboard widget.

COVERAGE PRIORITIES (in order):
1. New Claude model releases or updates (capabilities, pricing, context windows)
2. Anthropic safety research publications
3. API changes, new features, or deprecations
4. Partnerships, integrations, or enterprise announcements
5. Company news (funding, leadership, policy positions)

YOUR TASK:
1. Write 1-2 paragraph cliff notes synthesizing what is new from Anthropic TODAY. Focus ONLY on what is genuinely new.
2. Extract 2-4 key developments as short insights. Each should name what changed and why it matters.
3. If all articles cover previously reported topics, say "No significant new developments today."

ARTICLES:
{articles_text}

Return JSON:
{{"cliff_notes": "Your synthesis...", "key_insights": ["insight 1", "insight 2"]}}"""

    response, usage = llm_call(prompt, purpose="anthropic-news-synthesis")
    if not response:
        return DigestSection(
            title="Anthropic News",
            cliff_notes="Synthesis unavailable — scanner collected data but LLM synthesis failed.",
            key_insights=[f"Raw data: {source_count} articles found. Check output/anthropic/ for details."],
            source_count=source_count,
        )

    try:
        parsed = _parse_json_response(response)
        return DigestSection(
            title="Anthropic News",
            cliff_notes=parsed.get("cliff_notes", ""),
            key_insights=parsed.get("key_insights", []),
            source_count=source_count,
        )
    except Exception as e:
        logger.warning(f"Failed to parse Anthropic news synthesis: {e}")
        return DigestSection(
            title="Anthropic News",
            cliff_notes=response[:500],
            key_insights=[],
            source_count=source_count,
        )


# ---------------------------------------------------------------------------
# Section 2: OpenAI News
# ---------------------------------------------------------------------------

def synthesize_openai_news(data: dict | None) -> DigestSection | None:
    """Synthesize OpenAI-specific news into cliff notes."""
    if not data or not data.get("items"):
        return None

    items = data["items"]
    source_count = len(items)

    articles_text = "\n\n".join(
        f"- [{item.get('category', 'general')}] {item['title']}\n  Source: {item.get('author', 'unknown')}\n  Snippet: {item.get('snippet', '')[:300]}"
        for item in items[:15]
    )

    recent_history = _load_recent_digest_history(days_back=7)
    dedup_context = _build_dedup_context(recent_history, "openai_notes")

    today_str = datetime.now().strftime("%A, %B %d, %Y")
    prompt = f"""You are writing the "OpenAI News" section of a daily intelligence briefing for {today_str}.
The reader builds autonomous AI agent systems and monitors the competitive AI landscape.

{dedup_context}
Below are {source_count} articles about OpenAI developments.

FORMATTING RULES:
- Write in plain text. Do NOT use markdown formatting (no **bold**, no *italic*, no bullet markers).
- Use em dashes for parenthetical asides, not hyphens.
- Write naturally like a written memo, not a dashboard widget.

COVERAGE PRIORITIES (in order):
1. New GPT model releases or ChatGPT feature updates
2. API changes, new features, or deprecations
3. Research publications (o-series reasoning, DALL-E, Sora, etc.)
4. Enterprise or partnership announcements
5. Company news (leadership, funding, policy, safety)

YOUR TASK:
1. Write 1-2 paragraph cliff notes synthesizing what is new from OpenAI TODAY. Focus ONLY on what is genuinely new.
2. Extract 2-4 key developments as short insights. Each should name what changed and why it matters.
3. If all articles cover previously reported topics, say "No significant new developments today."

ARTICLES:
{articles_text}

Return JSON:
{{"cliff_notes": "Your synthesis...", "key_insights": ["insight 1", "insight 2"]}}"""

    response, usage = llm_call(prompt, purpose="openai-news-synthesis")
    if not response:
        return DigestSection(
            title="OpenAI News",
            cliff_notes="Synthesis unavailable — scanner collected data but LLM synthesis failed.",
            key_insights=[f"Raw data: {source_count} articles found. Check output/openai/ for details."],
            source_count=source_count,
        )

    try:
        parsed = _parse_json_response(response)
        return DigestSection(
            title="OpenAI News",
            cliff_notes=parsed.get("cliff_notes", ""),
            key_insights=parsed.get("key_insights", []),
            source_count=source_count,
        )
    except Exception as e:
        logger.warning(f"Failed to parse OpenAI news synthesis: {e}")
        return DigestSection(
            title="OpenAI News",
            cliff_notes=response[:500],
            key_insights=[],
            source_count=source_count,
        )


# ---------------------------------------------------------------------------
# Section 3: Google AI News
# ---------------------------------------------------------------------------

def synthesize_google_ai_news(data: dict | None) -> DigestSection | None:
    """Synthesize all Google AI news — Gemini, DeepMind, Firebase Studio, Genkit, AI Studio."""
    if not data or not data.get("items"):
        return None

    items = data["items"]
    source_count = len(items)

    articles_text = "\n\n".join(
        f"- [{item.get('category', 'general')}] {item['title']}\n  Source: {item.get('author', 'unknown')}\n  Snippet: {item.get('snippet', '')[:300]}"
        for item in items[:20]
    )

    recent_history = _load_recent_digest_history(days_back=7)
    dedup_context = _build_dedup_context(recent_history, "google_ai_notes")

    today_str = datetime.now().strftime("%A, %B %d, %Y")
    prompt = f"""You are writing the "Google News" section of a daily intelligence briefing for {today_str}.
The reader builds autonomous AI agent systems and is actively using the Google AI ecosystem (Gemini API, Firebase Studio, Genkit, AI Studio, Imagen, Veo).

{dedup_context}
Below are {source_count} articles about Google AI developments.

FORMATTING RULES:
- Write in plain text. Do NOT use markdown formatting (no **bold**, no *italic*, no bullet markers).
- Use em dashes for parenthetical asides, not hyphens.
- Write naturally like a written memo, not a dashboard widget.

COVERAGE PRIORITIES (in order):
1. New Gemini model releases or API capability updates
2. Firebase Studio, Genkit, or AI Studio new features or guides
3. DeepMind research with practical near-term applications
4. Imagen, Veo, or other Google AI product updates
5. Google Cloud AI platform changes
6. Developer tooling improvements (SDKs, documentation, samples)

YOUR TASK:
1. Write 1-2 paragraph cliff notes synthesizing what is new from Google AI TODAY. Cover the full breadth — models, tools, docs, research. Focus ONLY on what is genuinely new.
2. Extract 2-4 key developments as short insights. Each should name the product/tool and what changed.
3. If all articles cover previously reported topics, say "No significant new developments today."

ARTICLES:
{articles_text}

Return JSON:
{{"cliff_notes": "Your synthesis...", "key_insights": ["insight 1", "insight 2"]}}"""

    response, usage = llm_call(prompt, purpose="google-ai-news-synthesis")
    if not response:
        return DigestSection(
            title="Google News",
            cliff_notes="Synthesis unavailable — scanner collected data but LLM synthesis failed.",
            key_insights=[f"Raw data: {source_count} articles found. Check output/google_ai/ for details."],
            source_count=source_count,
        )

    try:
        parsed = _parse_json_response(response)
        return DigestSection(
            title="Google News",
            cliff_notes=parsed.get("cliff_notes", ""),
            key_insights=parsed.get("key_insights", []),
            source_count=source_count,
        )
    except Exception as e:
        logger.warning(f"Failed to parse Google AI news synthesis: {e}")
        return DigestSection(
            title="Google News",
            cliff_notes=response[:500],
            key_insights=[],
            source_count=source_count,
        )


# ---------------------------------------------------------------------------
# Section 4: AI Industry News (General — excludes Anthropic/OpenAI/Google)
# ---------------------------------------------------------------------------

def synthesize_ai_industry(ai_industry_data: dict | None) -> DigestSection | None:
    """Synthesize broad AI industry news — excludes Anthropic, OpenAI, Google (those have dedicated sections)."""
    if not ai_industry_data or not ai_industry_data.get("items"):
        return None

    items = ai_industry_data["items"]
    source_count = len(items)

    articles_text = "\n\n".join(
        f"- [{item.get('category', 'general')}] {item['title']}\n  Source: {item.get('author', 'unknown')}\n  Snippet: {item.get('snippet', '')[:300]}"
        for item in items[:20]
    )

    recent_history = _load_recent_digest_history(days_back=7)
    dedup_context = _build_dedup_context(recent_history, "ai_industry_notes")
    if not dedup_context:
        dedup_context = _build_dedup_context(recent_history, "ai_markets_notes")

    today_str = datetime.now().strftime("%A, %B %d, %Y")
    prompt = f"""You are writing the "General AI Industry" section of a daily intelligence briefing for {today_str}.
The reader builds autonomous AI agent systems and wants to stay current on the broader AI landscape.

IMPORTANT: Anthropic, OpenAI, and Google have their own dedicated sections in this briefing. Do NOT cover news from those three companies here. Focus exclusively on the rest of the AI ecosystem.

{dedup_context}
Below are {source_count} articles about AI industry developments.

FORMATTING RULES:
- Write in plain text. Do NOT use markdown formatting (no **bold**, no *italic*, no bullet markers).
- Use em dashes for parenthetical asides, not hyphens.
- Write naturally like a written memo, not a dashboard widget.

COVERAGE PRIORITIES (in order):
1. AI startups: notable launches, funding rounds, product releases
2. Open-source model releases (Meta/LLaMA, Mistral, HuggingFace, community models)
3. Microsoft AI (non-OpenAI), Meta AI, xAI, Cohere, Stability AI
4. Rising GitHub repositories for AI agents, frameworks, or tools
5. AI coding assistants (Cursor, Codeium, Sourcegraph, etc.)
6. AI policy, regulation, and industry trends
7. Notable arxiv papers with practical agent/LLM applications

YOUR TASK:
1. Write 2-3 paragraph cliff notes synthesizing the key developments. Group by theme (startups, open-source, tools, policy). Focus ONLY on what is genuinely new.
2. Extract 3-5 key developments as short insights. Each should name the org/product and what happened.
3. If all articles cover previously reported topics, say "No significant new developments today."

ARTICLES:
{articles_text}

Return JSON:
{{"cliff_notes": "Your synthesis...", "key_insights": ["insight 1", "insight 2", "insight 3"]}}"""

    response, usage = llm_call(prompt, purpose="ai-industry-synthesis")
    if not response:
        return DigestSection(
            title="General AI Industry",
            cliff_notes="Synthesis unavailable — scanner collected data but LLM synthesis failed.",
            key_insights=[f"Raw data: {source_count} articles found. Check output/ai_industry/ for details."],
            source_count=source_count,
        )

    try:
        parsed = _parse_json_response(response)
        return DigestSection(
            title="General AI Industry",
            cliff_notes=parsed.get("cliff_notes", ""),
            key_insights=parsed.get("key_insights", []),
            source_count=source_count,
        )
    except Exception as e:
        logger.warning(f"Failed to parse AI industry synthesis: {e}")
        return DigestSection(
            title="General AI Industry",
            cliff_notes=response[:500],
            key_insights=[],
            source_count=source_count,
        )


# ---------------------------------------------------------------------------
# Section 5: Project Applicability Analysis
# ---------------------------------------------------------------------------

def synthesize_project_applicability(
    ai_industry_section: DigestSection | None,
    project_context: dict,
) -> DigestSection | None:
    """Analyze how today's AI industry news applies to current projects."""
    if not ai_industry_section or not ai_industry_section.cliff_notes:
        return None

    stacks = json.dumps(project_context.get("active_stacks", {}), indent=2)
    priorities = json.dumps(project_context.get("current_priorities", []), indent=2)

    prompt = f"""You are an analyst evaluating how today's AI industry developments apply to a specific portfolio of projects.

TODAY'S AI NEWS:
{ai_industry_section.cliff_notes}

KEY DEVELOPMENTS:
{chr(10).join(f"- {i}" for i in ai_industry_section.key_insights)}

CURRENT PROJECT STACKS:
{stacks}

CURRENT PRIORITIES:
{priorities}

FORMATTING RULES:
- Write in plain text. Do NOT use markdown formatting (no **bold**, no *italic*, no bullet markers).
- Use em dashes for parenthetical asides, not hyphens.
- Be specific: name the project, the development, and the concrete action.

YOUR TASK:
1. Write 1-2 paragraphs analyzing which of today's developments are directly relevant to the reader's projects and why. Be concrete — name specific projects and what they could gain.
2. Extract 2-4 actionable recommendations. Each should say: what to do, which project benefits, and why it matters now. Skip generic advice.
3. If nothing today is directly applicable, say so honestly rather than stretching.

Return JSON:
{{"cliff_notes": "Your analysis...", "key_insights": ["recommendation 1", "recommendation 2"]}}"""

    response, usage = llm_call(prompt, purpose="project-applicability-synthesis")
    if not response:
        return None

    try:
        parsed = _parse_json_response(response)
        return DigestSection(
            title="Project Applicability",
            cliff_notes=parsed.get("cliff_notes", ""),
            key_insights=parsed.get("key_insights", []),
            source_count=0,
        )
    except Exception as e:
        logger.warning(f"Failed to parse project applicability synthesis: {e}")
        return DigestSection(
            title="Project Applicability",
            cliff_notes=response[:500],
            key_insights=[],
            source_count=0,
        )


# ---------------------------------------------------------------------------
# Section 6: Market News
# ---------------------------------------------------------------------------

def synthesize_market_news(market_data: dict | None) -> DigestSection | None:
    """Synthesize macro market news and algo trading methods into cliff notes."""
    if not market_data or not market_data.get("items"):
        return None

    items = market_data["items"]
    source_count = len(items)

    articles_text = "\n\n".join(
        f"- {item['title']}\n  Source: {item.get('author', 'unknown')}\n  Snippet: {item.get('snippet', '')[:300]}"
        for item in items[:15]
    )

    recent_history = _load_recent_digest_history(days_back=7)
    dedup_context = _build_dedup_context(recent_history, "market_notes")
    if not dedup_context:
        dedup_context = _build_dedup_context(recent_history, "trading_notes")

    today_str = datetime.now().strftime("%A, %B %d, %Y")
    prompt = f"""You are writing the "Market News" section of a daily intelligence briefing for {today_str}.
The reader runs autonomous trading systems on Kalshi prediction markets, stock markets, and sports betting.

{dedup_context}
Below are {source_count} articles about markets and trading.

FORMATTING RULES:
- Write in plain text. Do NOT use markdown formatting (no **bold**, no *italic*, no bullet markers).
- Use em dashes for parenthetical asides, not hyphens.
- Write naturally like a written memo, not a dashboard widget.

COVERAGE PRIORITIES (in order):
1. Macro market news: Fed decisions, economic data releases, earnings surprises, market structure shifts
2. Geopolitical or policy events with immediate market impact
3. Novel algo trading strategies or quantitative methods with reported live/actual performance (only if genuinely new — skip if nothing new)
4. Prediction market or sports betting strategy developments
5. Trading infrastructure or regulatory changes

EXCLUSION RULES:
- Do NOT include or summarize articles whose primary angle is (a) how to perform backtesting, (b) trading system architecture/design, or (c) generic algo trading tutorials.
- If an article discusses backtesting only as supporting evidence for a novel strategy's actual performance, you may reference it briefly under the algo section.

YOUR TASK:
1. Write 1-2 paragraph cliff notes. Lead with macro news if available — this is the primary focus. Include algo trading methods only if genuinely new with real results; if nothing new, stick to macro.
2. Extract 2-4 actionable insights: market developments to watch, strategy adjustments warranted, or new methods to evaluate.
3. If all articles cover previously reported topics, say "No significant new developments today."

ARTICLES:
{articles_text}

Return JSON:
{{"cliff_notes": "Your synthesis...", "key_insights": ["insight 1", "insight 2"]}}"""

    response, usage = llm_call(prompt, purpose="market-news-synthesis")
    if not response:
        return DigestSection(
            title="Market News",
            cliff_notes="Synthesis unavailable — scanner collected data but LLM synthesis failed.",
            key_insights=[f"Raw data: {source_count} articles found. Check output/market_news/ for details."],
            source_count=source_count,
        )

    try:
        parsed = _parse_json_response(response)
        return DigestSection(
            title="Market News",
            cliff_notes=parsed.get("cliff_notes", ""),
            key_insights=parsed.get("key_insights", []),
            source_count=source_count,
        )
    except Exception as e:
        logger.warning(f"Failed to parse market news synthesis: {e}")
        return DigestSection(
            title="Market News",
            cliff_notes=response[:500],
            key_insights=[],
            source_count=source_count,
        )


# ---------------------------------------------------------------------------
# Section 7: Claude Code Intel
# ---------------------------------------------------------------------------

def synthesize_claude_workflows(
    youtube_data: dict | None,
    project_context: dict,
) -> tuple[DigestSection | None, list[WorkflowEntry]]:
    """Synthesize Claude Code findings into cliff notes and workflow entries."""
    if not youtube_data or not youtube_data.get("items"):
        return None, []

    items = youtube_data["items"]
    source_count = len(items)

    videos_text = "\n\n".join(
        f"- {item['title']} (by {item.get('channel', 'unknown')})\n  Patterns found: {json.dumps(item.get('extracted_patterns', []))}"
        for item in items[:10]
    )

    stacks = json.dumps(project_context.get("active_stacks", {}), indent=2)
    priorities = json.dumps(project_context.get("current_priorities", []), indent=2)

    has_content = any(
        item.get("extracted_patterns") or item.get("transcript_length", 0) > 0
        for item in items
    )

    if not has_content:
        title_list = ", ".join(f'"{item["title"]}"' for item in items[:10])
        content_note = f"""NOTE: No video transcripts or extracted patterns are available (YouTube IP-blocked). You only have video titles and channel names below. Synthesize what you can infer from the titles about current Claude Code ecosystem trends. Do NOT ask for more information — just work with what you have.

VIDEO TITLES: {title_list}"""
    else:
        content_note = f"""VIDEOS FOUND (with extracted patterns):
{videos_text}"""

    prompt = f"""You are writing the "Claude Code Intel" section of a daily newsletter. The user runs autonomous AI agent systems.
You MUST return valid JSON and nothing else — no questions, no commentary.

FORMATTING RULES:
- Write in plain text. Do NOT use markdown formatting (no **bold**, no *italic*, no bullet markers).
- Use em dashes for parenthetical asides, not hyphens.
- Write naturally like a written memo, not a dashboard widget.

CURRENT STACKS:
{stacks}

CURRENT PRIORITIES:
{priorities}

{content_note}

YOUR TASK:
1. Write 1-2 concise paragraphs summarizing what topics are trending in the Claude Code ecosystem based on available information. Be direct and practical.
2. If specific techniques are identifiable, catalog them as workflows. If only titles are available, infer likely topics but keep workflows empty.
3. ONLY include workflows that are actually applicable to the current stacks and priorities. Skip generic/beginner content.

Return ONLY this JSON (no other text):
{{
  "cliff_notes": "synthesis paragraph(s)",
  "key_insights": ["insight 1", "insight 2"],
  "workflows": [
    {{
      "name": "Workflow Name",
      "description": "2-3 sentence description of what this technique does and why it matters",
      "use_cases": ["specific use case 1 for the reader's projects", "specific use case 2"],
      "applicable_projects": ["ProjectName1", "ProjectName2"]
    }}
  ]
}}"""

    response, usage = llm_call(prompt, purpose="claude-workflow-synthesis")
    if not response:
        return None, []

    try:
        parsed = _parse_json_response(response)

        section = DigestSection(
            title="Claude Code Workflows",
            cliff_notes=parsed.get("cliff_notes", ""),
            key_insights=parsed.get("key_insights", []),
            source_count=source_count,
        )

        workflows: list[WorkflowEntry] = []
        for w in parsed.get("workflows", []):
            source_url = ""
            source_title = ""
            for item in items:
                patterns = item.get("extracted_patterns", [])
                if any(w.get("name", "").lower() in p.lower() for p in patterns):
                    source_url = item.get("url", "")
                    source_title = item.get("title", "")
                    break
            if not source_url and items:
                source_url = items[0].get("url", "")
                source_title = items[0].get("title", "")

            workflows.append(WorkflowEntry(
                name=w.get("name", "Unknown"),
                description=w.get("description", ""),
                use_cases=w.get("use_cases", []),
                applicable_projects=w.get("applicable_projects", []),
                source_url=source_url,
                source_title=source_title,
            ))

        return section, workflows

    except Exception as e:
        logger.warning(f"Failed to parse Claude workflow synthesis: {e}")
        if items:
            titles = [item.get("title", "") for item in items[:8]]
            fallback_notes = (
                f"YouTube scanner found {len(items)} Claude Code videos today, "
                f"but transcript extraction was unavailable (IP-blocked). "
                f"Topics covered include: {', '.join(titles[:5])}."
            )
            return DigestSection(
                title="Claude Code Workflows",
                cliff_notes=fallback_notes,
                key_insights=[],
                source_count=source_count,
            ), []
        return None, []
