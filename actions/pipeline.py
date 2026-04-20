"""
Action Pipeline Orchestrator — chains extract → research → approval.

Called as a post-compile hook from the DailyBriefing compiler.
Sends each item to Telegram as soon as research completes (not batched).
"""
import json
import logging
import os
import sys
import time
from pathlib import Path

logger = logging.getLogger("action_pipeline")

WORKSPACE_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", ".."
))
INBOX_PATH = os.path.join(WORKSPACE_ROOT, "AI_Brain", "_Inbox.md")


def run_action_pipeline(digest_path: str, config: dict | None = None) -> int:
    """Main entry point — extract actions, research plans, send for approval.

    Items are sent to Telegram individually as soon as research completes,
    not batched at the end. This means the user starts receiving items
    while later items are still being researched.

    Returns the number of action items sent for approval.
    """
    from .extractor import extract_actions
    from .planner import research_action
    from .approval import send_approval_request, save_failed_item

    # Load config
    if config is None:
        config_path = os.path.join(os.path.dirname(__file__), "..", "config", "settings.json")
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except (json.JSONDecodeError, OSError):
            config = {}

    pipeline_config = config.get("action_pipeline", {})
    if not pipeline_config.get("enabled", True):
        logger.info("Action pipeline disabled in config")
        return 0

    max_items = pipeline_config.get("max_items_per_day", 10)
    research_model = pipeline_config.get("research_model", "sonnet")

    # Load telegram send function
    tg_dir = os.path.join(WORKSPACE_ROOT, "Tools", "TelegramDispatcher")
    sys.path.insert(0, tg_dir)
    try:
        from telegram_client import load_credentials, send_message
        load_credentials()
        send_fn = send_message
    except Exception as e:
        logger.error(f"Cannot load Telegram client: {e}")
        return 0

    # Step 1: Extract actionable items
    logger.info(f"Extracting actions from {digest_path}")
    actions_dir = os.path.dirname(os.path.abspath(__file__))
    items = extract_actions(digest_path, INBOX_PATH, actions_dir, max_items)

    if not items:
        logger.info("No actionable items found in today's briefing")
        return 0

    logger.info(f"Found {len(items)} actionable items")

    send_fn(
        f"*Daily Action Pipeline*\n"
        f"Found {len(items)} actionable items. Researching each now — "
        f"items will arrive as they're ready.",
        parse_mode="Markdown",
    )
    time.sleep(1)

    # Step 2+3: Research each item AND send immediately when ready
    sent_count = 0
    failed_count = 0

    for item in items:
        logger.info(f"Researching: {item.title[:60]}")
        plan = research_action(item, model=research_model)

        if "ERROR" not in plan.plan.upper():
            send_approval_request(plan, send_fn)
            sent_count += 1
            time.sleep(1)  # rate limit between messages
            logger.info(f"Sent {item.id} for approval")
        else:
            failed_count += 1
            logger.warning(f"Research failed for {item.id}: {plan.plan[:100]}")
            save_failed_item(
                item.id,
                {"item": item.__dict__ if hasattr(item, "__dict__") else {"id": item.id, "title": item.title, "description": item.description, "priority": item.priority, "source": item.source, "target_project": item.target_project}},
                plan.plan,
            )
            send_fn(
                f"Research failed for `{item.id}`: _{item.title[:60]}_\n"
                f"Reason: {plan.plan[:200]}\n"
                f"Use `/retry {item.id}` to re-attempt.",
                parse_mode="Markdown",
            )
            time.sleep(1)

    # Summary
    if sent_count > 0 or failed_count > 0:
        send_fn(
            f"*Action Pipeline Complete*\n"
            f"Sent: {sent_count} | Failed: {failed_count} | Total: {len(items)}",
            parse_mode="Markdown",
        )

    logger.info(f"Pipeline complete: {sent_count} sent, {failed_count} failed")
    return sent_count


if __name__ == "__main__":
    """CLI: python -m actions.pipeline <digest_path>"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    if len(sys.argv) < 2:
        from datetime import date
        digest = os.path.join(WORKSPACE_ROOT, "DailyBriefing", "output", "digests", f"{date.today()}.json")
    else:
        digest = sys.argv[1]

    count = run_action_pipeline(digest)
    print(f"Sent {count} action items for approval")
