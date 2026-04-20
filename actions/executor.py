"""
Execution Dispatcher — runs approved action plans via Claude CLI.

Simple items: single claude -p session.
Complex items: claude -p with cowork instruction for multi-agent execution.

Includes path validation to prevent executing plans with stale references.
"""
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Callable

from .approval import mark_completed

logger = logging.getLogger("action_executor")

WORKSPACE_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", ".."
))

# Paths that no longer exist — if an execution prompt references these,
# the plan is stale and must be re-researched
DEAD_PATHS = [
    "F:/_stockBot",
    "F:\\_stockBot",
    "Archive/_stockBot",
    "Archive\\_stockBot",
    "localhost:3100",
    "bus-client.js",
    "Orchestrator/subagents",
]

WORKSPACE_CONTEXT = (
    "\n\n---\n"
    "IMPORTANT CONTEXT: The workspace is at <workspace>/. "
    "Read the CLAUDE.md at the workspace root for full architecture. "
    "Key projects: IHTC, FORGE, MarketSwarm, SportsBettingSwarm, "
    "QuantMarketData, KalshiTrader, KalshiTestLab, DailyBriefing, "
    "MomentumWatch (at workspace root, NOT in Archive). "
    "Tools: TelegramDispatcher (Gateway Router), MaverickMCP (at Tools/MaverickMCP), "
    "swarm-utils, GeminiSearch. "
    "The MCP bus at localhost:3100 has been removed. "
    "Archive/_stockBot no longer exists — it was moved to MomentumWatch/."
)


def _validate_prompt(execution_prompt: str) -> tuple[bool, str]:
    """Check if the execution prompt references stale/dead paths.

    Returns (is_valid, reason). If invalid, the plan needs re-research.
    """
    for dead_path in DEAD_PATHS:
        if dead_path in execution_prompt:
            return False, f"References dead path: {dead_path}"
    return True, ""


def _validate_file_paths(files_to_modify: list[str]) -> list[str]:
    """Check which planned file paths actually exist. Returns list of missing paths."""
    missing = []
    for path in files_to_modify:
        # Normalize to absolute
        if not os.path.isabs(path):
            path = os.path.join(WORKSPACE_ROOT, path)
        # Files to create are OK (parent dir should exist)
        parent = os.path.dirname(path)
        if not os.path.exists(path) and not os.path.isdir(parent):
            missing.append(path)
    return missing


def execute_action(
    approval_id: str,
    plan_data: dict,
    send_fn: Callable[..., Any],
    model: str = "sonnet",
) -> None:
    """Execute an approved action plan in a background thread.

    Validates the execution prompt against dead paths before running.
    Injects workspace context to prevent stale references.
    """
    complexity = plan_data.get("complexity", "complex")
    execution_prompt = plan_data.get("execution_prompt", "")
    item = plan_data.get("item", {})
    title = item.get("title", "Unknown action")
    target_project = item.get("target_project")
    files_to_modify = plan_data.get("files_to_modify", [])

    if not execution_prompt:
        send_fn(f"No execution prompt for `{approval_id}`. Skipping.")
        return

    # Guard: validate prompt doesn't reference dead paths
    is_valid, reason = _validate_prompt(execution_prompt)
    if not is_valid:
        send_fn(
            f"BLOCKED `{approval_id}`: plan references stale infrastructure.\n"
            f"Reason: {reason}\n"
            f"Use `/revise {approval_id} <updated context>` to re-plan.",
            parse_mode="",
        )
        return

    # Guard: check if target files exist (warning, not blocking)
    missing = _validate_file_paths(files_to_modify)
    if missing:
        logger.warning(f"{approval_id}: {len(missing)} planned paths don't exist: {missing[:3]}")

    # Choose execution parameters based on complexity
    if complexity == "simple":
        max_turns = 15
        prompt_prefix = ""
    else:
        max_turns = 30
        prompt_prefix = (
            "This is a complex implementation task. Use subagents for parallel work. "
            "Read all relevant project files before making changes. "
            "Write tests for your changes. "
        )

    # Inject workspace context to prevent stale references
    full_prompt = f"{prompt_prefix}{execution_prompt}{WORKSPACE_CONTEXT}"

    # Determine working directory
    cwd = WORKSPACE_ROOT
    if target_project:
        project_dir = os.path.join(WORKSPACE_ROOT, target_project)
        if os.path.isdir(project_dir):
            cwd = project_dir

    claude_cmd = shutil.which("claude") or shutil.which("claude.cmd")
    if not claude_cmd:
        send_fn(f"ERROR: claude CLI not found. Cannot execute `{approval_id}`.")
        return

    env = os.environ.copy()
    env["TG_SESSION_ID"] = f"action-{approval_id}"
    env["REPLY_CHANNEL"] = "telegram"
    env.pop("ANTHROPIC_API_KEY", None)

    send_fn(
        f"Executing `{approval_id}`: _{title[:80]}_\n"
        f"Complexity: {complexity} | Model: {model} | Max turns: {max_turns}",
        parse_mode="Markdown",
    )

    def _run() -> None:
        start = time.time()
        try:
            result = subprocess.run(
                [claude_cmd, "--dangerously-skip-permissions",
                 "-p", full_prompt, "--model", model,
                 "--max-turns", str(max_turns)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=1800, cwd=cwd, env=env,
            )
            elapsed = time.time() - start
            status = "Done" if result.returncode == 0 else "Failed"
            preview = (result.stdout or "")[-1500:]

            send_fn(
                f"{status}: `{approval_id}` ({elapsed:.0f}s)\n"
                f"_{title[:60]}_\n\n{preview}",
                parse_mode="",
            )

            if result.returncode == 0:
                mark_completed(approval_id)

        except subprocess.TimeoutExpired:
            send_fn(f"TIMEOUT: `{approval_id}` exceeded 30 minutes.")
        except Exception as e:
            send_fn(f"ERROR executing `{approval_id}`: {e}")

    thread = threading.Thread(target=_run, daemon=True, name=f"exec-{approval_id}")
    thread.start()
