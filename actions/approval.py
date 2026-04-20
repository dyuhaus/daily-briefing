"""
Approval Manager — sends action plans to Telegram for approval and tracks state.
"""
import json
import os
import time
from dataclasses import asdict
from typing import Any, Callable, Optional

from .planner import ActionPlan

ACTIONS_DIR = os.path.dirname(os.path.abspath(__file__))
PENDING_FILE = os.path.join(ACTIONS_DIR, "pending.json")
COMPLETED_FILE = os.path.join(ACTIONS_DIR, "completed.json")
FAILED_FILE = os.path.join(ACTIONS_DIR, "failed.json")

def load_pending() -> dict[str, dict]:
    """Load pending approvals from disk."""
    if not os.path.exists(PENDING_FILE):
        return {}
    try:
        with open(PENDING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

def save_pending(pending: dict[str, dict]) -> None:
    """Persist pending approvals to disk."""
    os.makedirs(os.path.dirname(PENDING_FILE), exist_ok=True)
    with open(PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump(pending, f, indent=2, default=str)

def mark_completed(approval_id: str) -> None:
    """Move an approval from pending to completed."""
    pending = load_pending()
    item = pending.pop(approval_id, None)
    save_pending(pending)

    if item:
        completed = []
        if os.path.exists(COMPLETED_FILE):
            try:
                with open(COMPLETED_FILE, "r", encoding="utf-8") as f:
                    completed = json.load(f)
            except (json.JSONDecodeError, OSError):
                completed = []
        item["status"] = "completed"
        item["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        completed.append(item)
        # Keep last 100 completed items
        completed = completed[-100:]
        with open(COMPLETED_FILE, "w", encoding="utf-8") as f:
            json.dump(completed, f, indent=2, default=str)

def load_failed() -> dict[str, dict]:
    """Load failed research items from disk."""
    if not os.path.exists(FAILED_FILE):
        return {}
    try:
        with open(FAILED_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_failed_item(item_id: str, item_data: dict, error: str) -> None:
    """Record a failed research item for later retry."""
    failed = load_failed()
    failed[item_id] = {
        **item_data,
        "status": "failed",
        "error": error,
        "failed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with open(FAILED_FILE, "w", encoding="utf-8") as f:
        json.dump(failed, f, indent=2, default=str)


def remove_failed_item(item_id: str) -> Optional[dict]:
    """Remove a failed item (after successful retry). Returns the item or None."""
    failed = load_failed()
    item = failed.pop(item_id, None)
    with open(FAILED_FILE, "w", encoding="utf-8") as f:
        json.dump(failed, f, indent=2, default=str)
    return item


def mark_skipped(approval_id: str) -> None:
    """Remove an approval without executing."""
    pending = load_pending()
    pending.pop(approval_id, None)
    save_pending(pending)

def format_approval_message(plan: ActionPlan) -> str:
    """Format an action plan as a Telegram approval message."""
    files_str = ""
    if plan.files_to_modify:
        files_list = "\n".join(f"  - {f}" for f in plan.files_to_modify[:5])
        files_str = f"\n*Files:*\n{files_list}"

    plan_preview = plan.plan[:800]
    if len(plan.plan) > 800:
        plan_preview += "\n..."

    return (
        f"*Action Item: {plan.item.title[:80]}*\n"
        f"ID: `{plan.item.id}` | Priority: {plan.item.priority} | "
        f"Effort: {plan.estimated_effort} | Complexity: {plan.complexity}\n\n"
        f"*What:* {plan.item.description[:200]}\n"
        f"*Project:* {plan.item.target_project or 'General'}\n"
        f"*Source:* {plan.item.source}"
        f"{files_str}\n\n"
        f"*Plan:*\n{plan_preview}\n\n"
        f"Reply: `/approve {plan.item.id}` or `/revise {plan.item.id} <feedback>` or `/skip {plan.item.id}`"
    )

def send_approval_request(
    plan: ActionPlan,
    send_fn: Callable[[str], Any],
) -> str:
    """Format, send, and persist an approval request. Returns the approval ID."""
    msg = format_approval_message(plan)
    send_fn(msg, parse_mode="Markdown")

    # Persist to pending
    pending = load_pending()
    pending[plan.item.id] = {
        **plan.to_dict(),
        "sent_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "status": "pending",
    }
    save_pending(pending)

    return plan.item.id
