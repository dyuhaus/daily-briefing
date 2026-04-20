"""
Action Planner — researches and plans implementation for each action item.

Spawns a claude -p session per item to analyze the codebase,
identify files to modify, and produce a structured implementation plan.
Uses process health checks instead of hard timeouts.
"""
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from .extractor import ActionItem

logger = logging.getLogger("action_planner")

WORKSPACE_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", ".."
))

# Health check: if process produces no output for this long, it's stuck
STALL_TIMEOUT_SECONDS = 180  # 3 minutes with no new output = stuck
HEALTH_CHECK_INTERVAL = 15   # check every 15 seconds


@dataclass
class ActionPlan:
    item: ActionItem
    plan: str = ""
    files_to_modify: list[str] = field(default_factory=list)
    complexity: str = "simple"  # "simple" | "complex"
    estimated_effort: str = "unknown"
    execution_prompt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _build_research_prompt(item: ActionItem) -> str:
    project_context = ""
    if item.target_project:
        project_dir = os.path.join(WORKSPACE_ROOT, item.target_project)
        claude_md = os.path.join(project_dir, "CLAUDE.md")
        if os.path.exists(claude_md):
            project_context = f"\nThe target project has a CLAUDE.md at {claude_md}. Read it for context."

    return f"""You are a planning agent. Research how to implement the following action item.

## Workspace Context
The workspace is at <workspace>/. Read the CLAUDE.md at the workspace root for full system architecture.
Key active projects: IHTC, FORGE, MarketSwarm, SportsBettingSwarm, QuantMarketData, KalshiTrader, KalshiTestLab, DailyBriefing, MomentumWatch (formerly _stockBot — moved from Archive).
Tools: TelegramDispatcher (Gateway Router), MaverickMCP, swarm-utils, GeminiSearch, BookSummarizer.
Archive/_stockBot no longer exists. The MCP bus (localhost:3100) has been removed.

## Action Item
**Title:** {item.title}
**Description:** {item.description}
**Target Project:** {item.target_project or 'General workspace'}
**Source:** {item.source}
{project_context}

## Instructions
1. Read the relevant project files to understand the current state
2. Identify exactly which files need to be modified or created
3. Write a step-by-step implementation plan
4. Estimate complexity and effort

## Response Format (JSON)
Respond with ONLY a JSON object:
{{
    "plan": "Step-by-step implementation plan in markdown",
    "files_to_modify": ["path/to/file1.py", "path/to/file2.py"],
    "complexity": "simple or complex (simple if <200 lines changed)",
    "estimated_effort": "30 min / 2-3 hours / 1-2 days",
    "execution_prompt": "The exact prompt to give to a builder agent to implement this. Be thorough and specific — include file paths, function names, and the full scope of changes. The builder should be able to implement without additional research."
}}

Keep your response concise. The execution_prompt should be self-contained — include enough context for an agent to implement without additional research."""


def _run_with_health_check(cmd: list[str], cwd: str, env: dict) -> subprocess.CompletedProcess:
    """Run a subprocess with stall detection instead of hard timeout.

    Monitors stdout size. If no new output is produced for STALL_TIMEOUT_SECONDS,
    the process is considered stuck and killed. Otherwise it runs to completion.
    """
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    stdout_chunks: list[str] = []
    last_output_time = time.time()
    last_output_size = 0

    def _reader() -> None:
        """Background thread to read stdout without blocking."""
        nonlocal last_output_time, last_output_size
        while True:
            chunk = process.stdout.read(4096)
            if not chunk:
                break
            stdout_chunks.append(chunk)
            current_size = sum(len(c) for c in stdout_chunks)
            if current_size > last_output_size:
                last_output_time = time.time()
                last_output_size = current_size

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    # Health check loop
    while process.poll() is None:
        time.sleep(HEALTH_CHECK_INTERVAL)
        stall_duration = time.time() - last_output_time

        if stall_duration > STALL_TIMEOUT_SECONDS:
            logger.warning(
                f"Process stalled for {stall_duration:.0f}s with no output. Killing."
            )
            process.kill()
            process.wait()
            break

    reader_thread.join(timeout=5)
    stderr = process.stderr.read() if process.stderr else ""

    return subprocess.CompletedProcess(
        args=cmd,
        returncode=process.returncode if process.returncode is not None else -1,
        stdout="".join(stdout_chunks),
        stderr=stderr,
    )


def research_action(
    item: ActionItem,
    model: str = "sonnet",
) -> ActionPlan:
    """Spawn a claude -p research session to plan the implementation.

    Uses health-check based stall detection instead of a hard timeout.
    The process runs as long as it needs, but is killed if it produces
    no output for 3 minutes (stuck).
    """
    claude_cmd = shutil.which("claude") or shutil.which("claude.cmd")
    if not claude_cmd:
        return ActionPlan(
            item=item,
            plan="ERROR: claude CLI not found on PATH",
            complexity="complex",
            estimated_effort="unknown",
        )

    prompt = _build_research_prompt(item)
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)

    logger.info(f"Starting research for {item.id}: {item.title[:50]}")
    start = time.time()

    try:
        result = _run_with_health_check(
            [claude_cmd, "--dangerously-skip-permissions",
             "-p", prompt, "--model", model,
             "--max-turns", "8", "--output-format", "text"],
            cwd=WORKSPACE_ROOT,
            env=env,
        )
    except Exception as e:
        return ActionPlan(item=item, plan=f"ERROR: {e}", complexity="complex")

    elapsed = time.time() - start
    logger.info(f"Research for {item.id} completed in {elapsed:.0f}s")

    if result.returncode != 0:
        return ActionPlan(
            item=item,
            plan=f"ERROR: claude exited {result.returncode}",
            complexity="complex",
        )

    raw = result.stdout.strip()

    # Parse JSON from response
    parsed = _parse_json(raw)
    if parsed:
        return ActionPlan(
            item=item,
            plan=parsed.get("plan", raw),
            files_to_modify=parsed.get("files_to_modify", []),
            complexity=parsed.get("complexity", "complex"),
            estimated_effort=parsed.get("estimated_effort", "unknown"),
            execution_prompt=parsed.get("execution_prompt", ""),
        )

    # Fallback: use raw response as plan
    return ActionPlan(item=item, plan=raw, complexity="complex", estimated_effort="unknown")


def _parse_json(text: str) -> Optional[dict]:
    """Extract JSON from response (handles markdown fences)."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None
