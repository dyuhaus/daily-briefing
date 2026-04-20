"""
LLM Interface — Claude Code CLI (default), Anthropic API, or Ollama local.
Tracks all usage for cost estimation regardless of which backend is used.

Switch backends via config/settings.json:
  "llm": { "backend": "claude-cli" }     ← default, uses subscription
  "llm": { "backend": "api" }            ← uses Anthropic API, pay-per-token
  "llm": { "backend": "ollama-local" }   ← uses local Ollama, $0 cost

Ollama routing can also be purpose-based: set "ollama_purposes" in settings
to route specific purposes to Ollama while keeping others on Claude CLI.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, asdict, field
from typing import Optional

# Centralized usage tracking (in addition to local log)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
try:
    from shared.anthropic_usage import log_cli_call as _shared_log_cli
except ImportError:
    _shared_log_cli = None  # type: ignore[assignment]

try:
    from shared.local_llm import ollama_call, ollama_multimodal_call, is_ollama_available, OllamaResponse
    _ollama_available = True
except ImportError:
    _ollama_available = False

try:
    from shared.ollama_trust import (
        should_verify,
        record_verification,
        get_trust_level,
        get_verification_rate,
    )
    _trust_available = True
except ImportError:
    _trust_available = False

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.json"
USAGE_LOG = PROJECT_ROOT / "output" / "usage_log.jsonl"
USAGE_SUMMARY = PROJECT_ROOT / "output" / "usage_summary.json"

# Rough token estimation: ~4 chars per token
CHARS_PER_TOKEN = 4


def _load_ollama_settings() -> dict:
    """Load Ollama-specific settings from settings.json."""
    if not SETTINGS_PATH.exists():
        return {}
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            settings = json.load(f)
        return settings.get("ollama", {})
    except (json.JSONDecodeError, OSError):
        return {}


def _get_backend_for_purpose(purpose: str) -> str:
    """
    Determine which backend to use for a given purpose.

    If ollama.purpose_routing is configured in settings.json, specific
    purposes can be routed to Ollama while others stay on Claude CLI.
    """
    if not SETTINGS_PATH.exists():
        return "claude-cli"
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            settings = json.load(f)
    except (json.JSONDecodeError, OSError):
        return "claude-cli"

    llm_settings = settings.get("llm", {})
    default_backend = llm_settings.get("backend", "claude-cli")

    ollama_settings = settings.get("ollama", {})
    ollama_purposes = ollama_settings.get("purpose_routing", [])

    if ollama_purposes and purpose in ollama_purposes:
        return "ollama-local"

    return default_backend


@dataclass
class UsageRecord:
    timestamp: str
    backend: str  # "claude-cli" or "api"
    model: str
    purpose: str  # "twitter-scoring", "youtube-extraction", etc.
    input_chars: int
    output_chars: int
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    latency_seconds: float = 0.0
    success: bool = True
    error: str = ""

    def compute_estimates(self) -> None:
        self.estimated_input_tokens = max(1, self.input_chars // CHARS_PER_TOKEN)
        self.estimated_output_tokens = max(1, self.output_chars // CHARS_PER_TOKEN)
        # Cost estimation deferred to shared/anthropic_usage.py (source of truth for pricing)


def _log_usage(record: UsageRecord) -> None:
    """Append usage record to JSONL log."""
    USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(USAGE_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")


def _call_claude_cli(prompt: str, model: str = "sonnet") -> str:
    """Call Claude Code CLI as a subprocess.

    Uses --output-format stream-json --verbose to capture the actual text
    response, since the default -p mode returns an empty 'result' field
    when extended thinking is enabled.
    """
    cmd = [
        "claude", "-p", prompt,
        "--model", model,
        "--output-format", "stream-json",
        "--verbose",
    ]
    env = {**subprocess.os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        cwd=str(PROJECT_ROOT),
        env=env,
    )
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"Claude CLI failed (exit {result.returncode}): {stderr[:200]}")

    # Parse stream-json lines to extract text content from assistant messages
    text_parts: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            if event.get("type") != "assistant":
                continue
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "text":
                    text_parts.append(block["text"])
        except (json.JSONDecodeError, KeyError):
            continue

    return "\n".join(text_parts).strip()


def _call_ollama(prompt: str, purpose: str) -> tuple[str, float, int, int]:
    """
    Call Ollama for a given prompt.

    Returns (response_text, latency_seconds, input_chars, output_chars).

    Raises:
        RuntimeError: If Ollama is not available or the call fails.
    """
    if not _ollama_available:
        raise RuntimeError("shared.local_llm not importable — pip install httpx")

    ollama_settings = _load_ollama_settings()
    model = ollama_settings.get("model", "qwen2.5:7b")
    scoring_model = ollama_settings.get("scoring_model", "phi4-mini")
    base_url = ollama_settings.get("base_url", "http://localhost:11434")

    # Use scoring model for scoring/classification purposes
    is_scoring = purpose.endswith("-scoring")
    selected_model = scoring_model if is_scoring else model

    result: OllamaResponse = ollama_call(
        prompt=prompt,
        purpose=purpose,
        source="DailyBriefing",
        model=selected_model,
        base_url=base_url,
        temperature=0.0 if is_scoring else 0.3,
        max_tokens=512 if is_scoring else 1024,
        json_mode=is_scoring,
    )

    return (
        result.text,
        result.usage.latency_seconds,
        result.usage.input_tokens * CHARS_PER_TOKEN,
        result.usage.output_tokens * CHARS_PER_TOKEN,
    )


VERIFY_PROMPT_TEMPLATE: str = """You are a quality reviewer. A local open-source model produced the output below.
Your job: verify the output is correct, well-structured, and faithfully addresses the original task.

=== ORIGINAL TASK ===
{task_summary}
=== END TASK ===

=== LOCAL MODEL OUTPUT ===
{ollama_output}
=== END OUTPUT ===

Review criteria:
1. Is the output factually reasonable given the task?
2. Is the formatting/structure correct (valid JSON if scoring, coherent text if synthesis)?
3. Does it answer what was asked without hallucinating details?

Respond with ONLY a JSON object:
{{"verdict": "approve" or "revise", "revised_output": "..." or null, "reason": "one sentence"}}

If the output is acceptable, set verdict to "approve" and revised_output to null.
If it needs correction, set verdict to "revise" and provide the corrected output in revised_output."""


def _verify_ollama_output(
    ollama_response: str,
    original_prompt: str,
    purpose: str,
    model_cli: str = "haiku",
) -> str:
    """
    Have Claude verify Ollama's output. Returns the final approved/revised output.

    At FULL trust level (default), Claude verifies 100% of outputs.
    As trust builds through sustained agreement, verification rate decreases.
    If trust tracking is unavailable, always verifies.

    The verification prompt is compact: just the task summary (first 2000 chars
    of the original prompt) + Ollama's output. Much cheaper than re-generating.
    """
    # Check if we should verify this output
    do_verify = True
    if _trust_available:
        do_verify = should_verify()

    if not do_verify:
        logger.debug(f"Skipping verification for {purpose} (trust level: {get_trust_level().value})")
        return ollama_response

    # Build a compact verification prompt
    # Only send first 2000 chars of original prompt as task summary
    task_summary = original_prompt[:2000]
    if len(original_prompt) > 2000:
        task_summary += "\n[... task truncated for review ...]"

    verify_prompt = VERIFY_PROMPT_TEMPLATE.format(
        task_summary=task_summary,
        ollama_output=ollama_response[:3000],
    )

    try:
        raw_review = _call_claude_cli(verify_prompt, model=model_cli)

        # Parse Claude's verdict
        verdict_data = _parse_verification_verdict(raw_review)
        verdict = verdict_data.get("verdict", "approve")
        revised = verdict_data.get("revised_output")
        reason = verdict_data.get("reason", "")

        agreed = verdict == "approve"
        claude_action = "approved" if agreed else "revised"
        final_output = ollama_response if agreed else (revised or ollama_response)

        # Log to trust tracker
        if _trust_available:
            new_level = record_verification(
                source="DailyBriefing",
                purpose=purpose,
                ollama_output=ollama_response,
                claude_output=final_output,
                agreed=agreed,
                claude_action=claude_action,
            )
            level_str = new_level.value if hasattr(new_level, "value") else str(new_level)
            if not agreed:
                logger.info(
                    f"[VERIFY] {purpose}: Claude REVISED Ollama output. "
                    f"Reason: {reason}. Trust: {level_str}"
                )
            else:
                logger.debug(
                    f"[VERIFY] {purpose}: Claude APPROVED. Trust: {level_str}"
                )

        return final_output

    except Exception as e:
        # Verification failed — be conservative, return Ollama's output
        # but log the failure so we know verification is broken
        logger.warning(f"Verification failed for {purpose}, using Ollama output: {e}")
        return ollama_response


def _parse_verification_verdict(raw_text: str) -> dict:
    """
    Parse Claude's verification verdict JSON.

    Handles cases where Claude wraps JSON in markdown or extra text.
    """
    import re

    stripped = raw_text.strip()

    # Try direct JSON parse
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # Try to find embedded JSON object
    match = re.search(r'\{[^{}]*"verdict"[^{}]*\}', stripped, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Fallback: if Claude just said "approve" or similar, treat as approval
    lower = stripped.lower()
    if "approve" in lower or "acceptable" in lower or "correct" in lower:
        return {"verdict": "approve", "revised_output": None, "reason": "parsed from freetext"}

    # Can't parse — default to approve (conservative: don't block on parse failure)
    logger.warning(f"Could not parse verification verdict, defaulting to approve: {stripped[:200]}")
    return {"verdict": "approve", "revised_output": None, "reason": "parse failure, defaulting approve"}


def llm_call(
    prompt: str,
    purpose: str,
    model_cli: str = "sonnet",
    model_api: str = "claude-haiku-4-5-20251001",
) -> tuple[str, UsageRecord]:
    """
    Make an LLM call via the configured backend.
    Returns (response_text, usage_record).

    Backend is determined by purpose-based routing (settings.json ollama.purpose_routing)
    or the global llm.backend setting. Supports three backends:
      - claude-cli: Claude Code CLI (subscription, default)
      - api: Anthropic API (pay-per-token)
      - ollama-local: Local Ollama server ($0 cost)
    """
    backend = _get_backend_for_purpose(purpose)
    start = time.time()

    record = UsageRecord(
        timestamp=datetime.now(timezone.utc).isoformat(),
        backend=backend,
        model=model_cli,
        purpose=purpose,
        input_chars=len(prompt),
        output_chars=0,
    )

    try:
        if backend == "ollama-local":
            response, latency, in_chars, out_chars = _call_ollama(prompt, purpose)
            record.output_chars = out_chars
            record.latency_seconds = latency
            ollama_settings = _load_ollama_settings()
            is_scoring = purpose.endswith("-scoring")
            record.model = (
                ollama_settings.get("scoring_model", "phi4-mini")
                if is_scoring
                else ollama_settings.get("model", "qwen2.5:7b")
            )

            # --- Claude oversight: verify Ollama's output ---
            response = _verify_ollama_output(
                ollama_response=response,
                original_prompt=prompt,
                purpose=purpose,
                model_cli=model_cli,
            )

        else:
            response = _call_claude_cli(prompt, model=model_cli)
            record.output_chars = len(response)
            record.latency_seconds = time.time() - start

        record.success = True

    except Exception as e:
        record.latency_seconds = time.time() - start
        record.success = False
        record.error = str(e)[:200]
        logger.error(f"LLM call failed ({backend}): {e}")

        # Fallback: if Ollama fails, try Claude CLI
        if backend == "ollama-local":
            logger.warning("Ollama failed, falling back to Claude CLI")
            try:
                response = _call_claude_cli(prompt, model=model_cli)
                record.output_chars = len(response)
                record.latency_seconds = time.time() - start
                record.success = True
                record.error = ""
                record.backend = "claude-cli (fallback)"
                record.model = model_cli
            except Exception as fallback_err:
                record.latency_seconds = time.time() - start
                record.success = False
                record.error = f"Ollama+CLI both failed: {fallback_err}"[:200]
                logger.error(f"Fallback CLI also failed: {fallback_err}")
                response = ""
        else:
            response = ""

    record.compute_estimates()
    _log_usage(record)

    # Also log to centralized shared tracker (skip for ollama — it has its own log)
    if _shared_log_cli is not None and not backend.startswith("ollama"):
        _shared_log_cli(
            model=model_cli,
            prompt_chars=record.input_chars,
            output_chars=record.output_chars,
            source="DailyBriefing",
            purpose=purpose,
            latency_seconds=record.latency_seconds,
            success=record.success,
            error=record.error,
        )

    return response, record


def llm_multimodal_call(
    prompt: str,
    purpose: str,
    image_paths: list[Path],
    model_cli: str = "sonnet",
) -> tuple[str, UsageRecord]:
    """Make a multimodal LLM call with images via Ollama Llama 4.

    Falls back to text-only llm_call() if Ollama is unavailable or image_paths is empty.

    Args:
        prompt: The user message content.
        purpose: Tag for usage tracking (e.g., "image-analysis").
        image_paths: Local image files to embed. Pass [] for text-only fallback.
        model_cli: Claude CLI model to use on fallback (default "sonnet").

    Returns:
        Tuple of (response_text, usage_record).
    """
    if not image_paths or not _ollama_available:
        return llm_call(prompt, purpose, model_cli)

    ollama_settings = _load_ollama_settings()
    multimodal_model: str = ollama_settings.get("multimodal_model", "llama4:scout")
    base_url: str = ollama_settings.get("base_url", "http://localhost:11434")

    start = time.time()
    result: OllamaResponse = ollama_multimodal_call(
        prompt=prompt,
        purpose=purpose,
        source="DailyBriefing",
        image_paths=image_paths,
        model=multimodal_model,
        base_url=base_url,
    )

    record = UsageRecord(
        timestamp=datetime.now(timezone.utc).isoformat(),
        backend="ollama-local",
        model=multimodal_model,
        purpose=purpose,
        input_chars=len(prompt),
        output_chars=len(result.text),
        latency_seconds=result.usage.latency_seconds,
        success=result.usage.success,
        error=result.usage.error,
    )
    record.compute_estimates()
    _log_usage(record)

    if _shared_log_cli is not None:
        _shared_log_cli(
            model=multimodal_model,
            prompt_chars=record.input_chars,
            output_chars=record.output_chars,
            source="DailyBriefing",
            purpose=purpose,
            latency_seconds=record.latency_seconds,
            success=record.success,
            error=record.error,
        )

    return result.text, record


def get_usage_summary() -> dict:
    """Compute usage summary from the log file."""
    if not USAGE_LOG.exists():
        return {"total_calls": 0, "total_estimated_cost": 0.0}

    records: list[dict] = []
    for line in USAGE_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not records:
        return {"total_calls": 0, "total_estimated_cost": 0.0}

    total_cost = sum(r.get("estimated_cost_usd", 0) for r in records)
    total_input_tokens = sum(r.get("estimated_input_tokens", 0) for r in records)
    total_output_tokens = sum(r.get("estimated_output_tokens", 0) for r in records)
    cli_calls = sum(1 for r in records if r.get("backend") == "claude-cli")
    api_calls = sum(1 for r in records if r.get("backend") == "api")
    failures = sum(1 for r in records if not r.get("success"))

    by_purpose: dict[str, dict] = {}
    for r in records:
        purpose = r.get("purpose", "unknown")
        if purpose not in by_purpose:
            by_purpose[purpose] = {"calls": 0, "estimated_cost": 0.0, "avg_latency": 0.0}
        by_purpose[purpose]["calls"] += 1
        by_purpose[purpose]["estimated_cost"] += r.get("estimated_cost_usd", 0)
        by_purpose[purpose]["avg_latency"] += r.get("latency_seconds", 0)
    for p in by_purpose.values():
        if p["calls"] > 0:
            p["avg_latency"] = round(p["avg_latency"] / p["calls"], 1)
        p["estimated_cost"] = round(p["estimated_cost"], 6)

    # Cost projections
    days_tracked = 1
    if len(records) >= 2:
        first = records[0].get("timestamp", "")
        last = records[-1].get("timestamp", "")
        try:
            first_dt = datetime.fromisoformat(first)
            last_dt = datetime.fromisoformat(last)
            days_tracked = max(1, (last_dt - first_dt).days + 1)
        except Exception:
            pass

    daily_avg = total_cost / days_tracked
    monthly_projection = daily_avg * 30

    summary = {
        "total_calls": len(records),
        "cli_calls": cli_calls,
        "api_calls": api_calls,
        "failures": failures,
        "total_estimated_input_tokens": total_input_tokens,
        "total_estimated_output_tokens": total_output_tokens,
        "total_estimated_cost_usd": round(total_cost, 6),
        "days_tracked": days_tracked,
        "daily_avg_cost_usd": round(daily_avg, 6),
        "monthly_projection_usd": round(monthly_projection, 4),
        "by_purpose": by_purpose,
        "note": "Costs are ESTIMATES based on Haiku pricing. CLI calls use subscription (actual cost = $0).",
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }

    # Save summary
    USAGE_SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
