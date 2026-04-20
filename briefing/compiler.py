"""
Daily Briefing Compiler — synthesizes scanner outputs + project status into newsletter.
Uses LLM to produce cliff notes instead of raw link lists.
"""
from __future__ import annotations

import json
import logging
import smtplib
import sys
from dataclasses import asdict
from datetime import date, datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import re

from jinja2 import Template

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.credentials import get_credential
from briefing.status_reader import read_all_statuses
from briefing.synthesizer import (
    run_synthesis,
    SynthesizedDigest,
    _load_editorial_items,
)

# KalshiTrader v2 paper trading data paths
KALSHI_V2_REPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "KalshiTrader" / "logs" / "paper_v2" / "reports"
KALSHI_V2_FILL_LEDGER = Path(__file__).resolve().parent.parent.parent / "KalshiTrader" / "logs" / "paper_v2" / "fill_ledger.jsonl"
KALSHI_V2_SETTLEMENT_LEDGER = Path(__file__).resolve().parent.parent.parent / "KalshiTrader" / "logs" / "paper_v2" / "settlement_ledger.jsonl"

_KALSHI_CHANGE_NOTES_PATH = Path(__file__).resolve().parent.parent.parent / "KalshiTrader" / "logs" / "paper_v2" / "change_notes.json"


def _load_kalshi_change_notes() -> list[str]:
    """Load KalshiTrader change notes from file, falling back to empty list."""
    if _KALSHI_CHANGE_NOTES_PATH.exists():
        try:
            return json.loads(_KALSHI_CHANGE_NOTES_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return []

_LEAGUE_PREFIX_MAP: dict[str, str] = {
    "KXNBASPREAD": "NBA",
    "KXNCAAMBSPREAD": "NCAAM",
    "KXNFLSPREAD": "NFL",
    "KXMLBSPREAD": "MLB",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [Compiler] %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STALE_DETECTOR_DIR = PROJECT_ROOT.parent / "Tools" / "stale-work-detector"


_INACTIVE_TITLE_MARKER = "No activity"


def _read_action_items() -> tuple[dict | None, list[dict]]:
    """
    Run the stale work detector and return structured findings split into:
    - action_items: actual pending work (open items, TODOs, missing files, etc.)
    - inactive_projects: findings where title contains "No activity" (5+ days no git)

    Returns:
        Tuple of (action_items dict | None, inactive_projects list).
    """
    try:
        sys.path.insert(0, str(STALE_DETECTOR_DIR))
        from detector import run_full_scan
        from rules import Severity

        findings = run_full_scan(today=date.today())
        if not findings:
            return None, []

        # Separate inactive-project findings from actual work items
        work_findings = [f for f in findings if _INACTIVE_TITLE_MARKER not in f.title]
        inactive_findings = [f for f in findings if _INACTIVE_TITLE_MARKER in f.title]

        inactive_projects = [
            {"project": f.project, "title": f.title, "details": f.details}
            for f in inactive_findings
        ]

        if not work_findings:
            return None, inactive_projects

        critical = [
            {"project": f.project, "title": f.title, "details": f.details}
            for f in work_findings if f.severity == Severity.CRITICAL
        ]
        attention = [
            {"project": f.project, "title": f.title, "details": f.details}
            for f in work_findings if f.severity == Severity.ATTENTION
        ]
        info = [
            {"project": f.project, "title": f.title, "details": f.details}
            for f in work_findings if f.severity == Severity.INFO
        ]

        action_items = {
            "critical": critical,
            "attention": attention,
            "info": info,
            "total": len(work_findings),
            "critical_count": len(critical),
            "attention_count": len(attention),
            "info_count": len(info),
        }
        return action_items, inactive_projects

    except Exception as e:
        logger.warning(f"Stale work detector failed: {e}")
        return None, []
    finally:
        if str(STALE_DETECTOR_DIR) in sys.path:
            sys.path.remove(str(STALE_DETECTOR_DIR))
CONFIG_PATH = PROJECT_ROOT / "config" / "settings.json"
TEMPLATE_PATH = PROJECT_ROOT / "templates" / "newsletter.html"
OUTPUT_DIR = PROJECT_ROOT / "output" / "newsletters"


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def compile_briefing() -> tuple[str, str]:
    """Compile the full daily briefing. Returns (html_content, plain_text_summary)."""
    config = load_config()

    # Run synthesis (cliff notes + Brain indexing)
    logger.info("Running synthesis pipeline...")
    digest = run_synthesis()

    # Load project statuses
    project_statuses = read_all_statuses(config)

    # Load KalshiTrader v2 paper trading data
    kalshi_paper_data = None
    try:
        # Find most recent v2 report
        report_files = sorted(KALSHI_V2_REPORTS_DIR.glob("paper_v2_report_*.json"), reverse=True) if KALSHI_V2_REPORTS_DIR.exists() else []
        if report_files:
            report = json.loads(report_files[0].read_text(encoding="utf-8"))

            # Build by-league breakdown from settlement ledger
            by_league: dict[str, dict] = {}
            if KALSHI_V2_SETTLEMENT_LEDGER.exists():
                for line in KALSHI_V2_SETTLEMENT_LEDGER.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    s = json.loads(line)
                    ticker = s.get("ticker", "")
                    league = next(
                        (label for prefix, label in _LEAGUE_PREFIX_MAP.items() if ticker.startswith(prefix)),
                        "Other",
                    )
                    if league not in by_league:
                        by_league[league] = {"wins": 0, "losses": 0, "pnl": 0.0}
                    pnl = s.get("pnl", 0.0)
                    if pnl > 0:
                        by_league[league]["wins"] += 1
                    else:
                        by_league[league]["losses"] += 1
                    by_league[league]["pnl"] = round(by_league[league]["pnl"] + pnl, 2)

            # Build today's fills from fill ledger
            today_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
            today_fills: list[dict] = []
            settled_tickers: set[str] = set()
            if KALSHI_V2_SETTLEMENT_LEDGER.exists():
                for line in KALSHI_V2_SETTLEMENT_LEDGER.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line:
                        settled_tickers.add(json.loads(line).get("ticker", ""))

            if KALSHI_V2_FILL_LEDGER.exists():
                seen_fill_tickers: set[str] = set()
                for line in KALSHI_V2_FILL_LEDGER.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    fill = json.loads(line)
                    ticker = fill.get("ticker", "")
                    fill_time = fill.get("fill_time", "")
                    # Deduplicate by ticker (multiple fills per order)
                    if ticker in seen_fill_tickers:
                        continue
                    seen_fill_tickers.add(ticker)
                    if not fill_time.startswith(today_str):
                        continue
                    league = next(
                        (label for prefix, label in _LEAGUE_PREFIX_MAP.items() if ticker.startswith(prefix)),
                        "Other",
                    )
                    is_settled = ticker in settled_tickers
                    today_fills.append({
                        "league": league,
                        "ticker": ticker,
                        "entry_price": fill.get("price_cents", 0) / 100.0,
                        "settled": is_settled,
                        "pnl": None,  # not available per-fill; settlement_ledger has it
                    })

            total_fills = report.get("total_fills", 0)
            unsettled_count = total_fills - report.get("total_settlements", 0)

            kalshi_paper_data = {
                "total_trades": total_fills,
                "settled": report.get("total_settlements", 0),
                "unsettled": max(0, unsettled_count),
                "wins": report.get("wins", 0),
                "losses": report.get("losses", 0),
                "win_rate": report.get("win_rate", 0.0),
                "total_pnl": round(report.get("total_pnl", 0.0), 2),
                "total_wagered": round(report.get("total_invested", 0.0), 2),
                "roi": round(report.get("roi", 0.0), 4),
                "bankroll": round(report.get("current_balance", 100.0), 2),
                "avg_timing_score": 0.0,  # not tracked in v2 report; placeholder
                "by_league": by_league,
                "today_trades": today_fills,
                "change_notes": _load_kalshi_change_notes(),
            }
            logger.info(
                f"KalshiTrader v2 paper data: {total_fills} fills, "
                f"{report.get('wins', 0)}W/{report.get('losses', 0)}L, "
                f"P&L=${report.get('total_pnl', 0.0):+.2f}"
            )
    except Exception as e:
        logger.warning(f"Could not load KalshiTrader v2 paper data: {e}")

    def _classify_density(source_count: int, cliff_notes: str) -> str:
        """Generate a subtitle like 'Light day' or 'Heavy day' based on source density."""
        if source_count <= 10:
            return "Light day"
        elif source_count <= 20:
            return ""
        elif source_count <= 35:
            return "Busy day"
        else:
            return "Heavy day"

    def _clean_text(text: str) -> str:
        """Convert markdown bold to HTML <strong> and fix encoding artifacts."""
        # Fix mojibake: UTF-8 bytes misread as Windows-1252
        # These are the raw Unicode codepoints that result from double-encoding
        text = text.replace("\u00e2\u20ac\u201d", "\u2014")  # em dash
        text = text.replace("\u00e2\u20ac\u201c", "\u2014")  # em dash variant
        text = text.replace("\u00e2\u20ac\u2122", "\u2019")  # right single quote
        text = text.replace("\u00e2\u20ac\u0153", "\u201c")  # left double quote
        text = text.replace("\u00e2\u20ac\u009d", "\u201d")  # right double quote
        # Also handle the 3-byte mojibake sequences as raw bytes decoded to str
        _mojibake_map = {
            "\xc3\xa2\xc2\x80\xc2\x94": "\u2014",  # em dash
            "\xc3\xa2\xc2\x80\xc2\x99": "\u2019",  # right single quote
            "\xc3\xa2\xc2\x80\xc2\x9c": "\u201c",  # left double quote
            "\xc3\xa2\xc2\x80\xc2\x9d": "\u201d",  # right double quote
        }
        for bad, good in _mojibake_map.items():
            text = text.replace(bad, good)
        # Convert markdown bold **text** to HTML <strong>
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        # Convert markdown italic *text* to HTML <em>
        text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
        return text

    def _clean_list(items: list[str]) -> list[str]:
        return [_clean_text(item) for item in items]

    # Load editorial items for direct rendering in the template
    editorial_items = _load_editorial_items()

    # Run stale-work detector — split into actual work items vs inactive projects
    action_items, inactive_projects = _read_action_items()

    # Build template context
    today = datetime.now().strftime("%A, %B %d, %Y")
    context = {
        "date": today,
        "generation_time": datetime.now().strftime("%I:%M %p ET"),
        "anthropic_news": {
            "cliff_notes": _clean_text(digest.anthropic_news.cliff_notes),
            "key_insights": _clean_list(digest.anthropic_news.key_insights),
            "source_count": digest.anthropic_news.source_count,
        } if digest.anthropic_news else None,
        "openai_news": {
            "cliff_notes": _clean_text(digest.openai_news.cliff_notes),
            "key_insights": _clean_list(digest.openai_news.key_insights),
            "source_count": digest.openai_news.source_count,
        } if digest.openai_news else None,
        "google_ai_news": {
            "cliff_notes": _clean_text(digest.google_ai_news.cliff_notes),
            "key_insights": _clean_list(digest.google_ai_news.key_insights),
            "source_count": digest.google_ai_news.source_count,
        } if digest.google_ai_news else None,
        "ai_industry": {
            "cliff_notes": _clean_text(digest.ai_industry.cliff_notes),
            "key_insights": _clean_list(digest.ai_industry.key_insights),
            "source_count": digest.ai_industry.source_count,
            "density": _classify_density(digest.ai_industry.source_count, digest.ai_industry.cliff_notes),
        } if digest.ai_industry else None,
        "project_applicability": {
            "cliff_notes": _clean_text(digest.project_applicability.cliff_notes),
            "key_insights": _clean_list(digest.project_applicability.key_insights),
        } if digest.project_applicability else None,
        "market_news": {
            "cliff_notes": _clean_text(digest.market_news.cliff_notes),
            "key_insights": _clean_list(digest.market_news.key_insights),
            "source_count": digest.market_news.source_count,
        } if digest.market_news else None,
        "claude_workflows": {
            "cliff_notes": _clean_text(digest.claude_workflows.cliff_notes),
            "key_insights": _clean_list(digest.claude_workflows.key_insights),
            "source_count": digest.claude_workflows.source_count,
            "section_tag": "Internal update" if digest.claude_workflows.source_count == 0 else "",
        } if digest.claude_workflows else None,
        "applicable_workflows": [
            {
                "name": wf.name,
                "description": _clean_text(wf.description),
                "use_cases": _clean_list(wf.use_cases[:2]),
                "applicable_projects": wf.applicable_projects,
            }
            for wf in digest.applicable_workflows
        ],
        "editorial_items": editorial_items,
        "action_items": action_items,
        "inactive_projects": inactive_projects,
        "projects": [
            {
                "name": s.name,
                "status": s.status,
                "summary": s.summary,
                "last_activity": s.last_activity[:10] if s.last_activity else "Unknown",
                "metrics": s.metrics,
            }
            for s in project_statuses
        ],
        "kalshi_paper": kalshi_paper_data,
    }

    # Render HTML
    template = Template(TEMPLATE_PATH.read_text(encoding="utf-8"))
    html = template.render(**context)

    # Plain text summary
    lines = [f"Daily Briefing — {today}", "=" * 55, ""]

    if kalshi_paper_data and kalshi_paper_data.get("total_trades", 0) > 0:
        lines.append("KALSHI PAPER TRADER — Timing Strategy")
        lines.append("-" * 40)
        kp = kalshi_paper_data
        lines.append(f"  Record: {kp['wins']}W / {kp['losses']}L | "
                      f"P&L: ${kp['total_pnl']:+.2f} | "
                      f"Win Rate: {kp['win_rate']:.0%} | "
                      f"ROI: {kp['roi']:.1%}")
        lines.append(f"  Bankroll: ${kp['bankroll']:.2f} | "
                      f"Trades: {kp['total_trades']} ({kp['unsettled']} pending) | "
                      f"Avg timing score: {kp['avg_timing_score']:.3f}")
        if kp.get("by_league"):
            for league, stats in kp["by_league"].items():
                lines.append(f"    {league}: {stats['wins']}W/{stats['losses']}L "
                              f"${stats['pnl']:+.2f}")
        if kp.get("today_trades"):
            lines.append(f"  Today: {len(kp['today_trades'])} fills")
            for t in kp["today_trades"][:5]:
                status = "settled" if t["settled"] else "pending"
                lines.append(f"    {t['league']} {t['ticker']} "
                              f"@ {round((t['entry_price'] or 0) * 100)}c {status}")
        lines.extend(["", ""])

    if digest.anthropic_news and digest.anthropic_news.cliff_notes:
        lines.append(f"ANTHROPIC NEWS ({digest.anthropic_news.source_count} sources)")
        lines.append("-" * 40)
        lines.append(digest.anthropic_news.cliff_notes)
        lines.append("")
        if digest.anthropic_news.key_insights:
            for insight in digest.anthropic_news.key_insights:
                lines.append(f"  > {insight}")
            lines.append("")
    else:
        lines.append("ANTHROPIC NEWS: No new developments today.")
        lines.append("")

    if digest.openai_news and digest.openai_news.cliff_notes:
        lines.append(f"OPENAI NEWS ({digest.openai_news.source_count} sources)")
        lines.append("-" * 40)
        lines.append(digest.openai_news.cliff_notes)
        lines.append("")
        if digest.openai_news.key_insights:
            for insight in digest.openai_news.key_insights:
                lines.append(f"  > {insight}")
            lines.append("")
    else:
        lines.append("OPENAI NEWS: No new developments today.")
        lines.append("")

    if digest.google_ai_news and digest.google_ai_news.cliff_notes:
        lines.append(f"GOOGLE NEWS ({digest.google_ai_news.source_count} sources)")
        lines.append("-" * 40)
        lines.append(digest.google_ai_news.cliff_notes)
        lines.append("")
        if digest.google_ai_news.key_insights:
            for insight in digest.google_ai_news.key_insights:
                lines.append(f"  > {insight}")
            lines.append("")
    else:
        lines.append("GOOGLE NEWS: No new developments today.")
        lines.append("")

    if digest.ai_industry and digest.ai_industry.cliff_notes:
        lines.append(f"GENERAL AI INDUSTRY ({digest.ai_industry.source_count} sources)")
        lines.append("-" * 40)
        lines.append(digest.ai_industry.cliff_notes)
        lines.append("")
        if digest.ai_industry.key_insights:
            for insight in digest.ai_industry.key_insights:
                lines.append(f"  > {insight}")
            lines.append("")
    else:
        lines.append("GENERAL AI INDUSTRY: No intelligence gathered today.")
        lines.append("")

    if digest.project_applicability and digest.project_applicability.cliff_notes:
        lines.append("PROJECT APPLICABILITY ANALYSIS")
        lines.append("-" * 40)
        lines.append(digest.project_applicability.cliff_notes)
        lines.append("")
        if digest.project_applicability.key_insights:
            for insight in digest.project_applicability.key_insights:
                lines.append(f"  > {insight}")
            lines.append("")

    if digest.market_news and digest.market_news.cliff_notes:
        lines.append(f"MARKET NEWS ({digest.market_news.source_count} sources)")
        lines.append("-" * 40)
        lines.append(digest.market_news.cliff_notes)
        lines.append("")
        if digest.market_news.key_insights:
            for insight in digest.market_news.key_insights:
                lines.append(f"  > {insight}")
            lines.append("")
    else:
        lines.append("MARKET NEWS: No new developments today.")
        lines.append("")

    if digest.claude_workflows and digest.claude_workflows.cliff_notes:
        lines.append(f"CLAUDE CODE INTEL ({digest.claude_workflows.source_count} videos)")
        lines.append("-" * 40)
        lines.append(digest.claude_workflows.cliff_notes)
        lines.append("")
        if digest.claude_workflows.key_insights:
            for insight in digest.claude_workflows.key_insights:
                lines.append(f"  > {insight}")
            lines.append("")
        if digest.applicable_workflows:
            lines.append("  APPLICABLE TO YOUR PROJECTS:")
            for wf in digest.applicable_workflows:
                projects = ", ".join(wf.applicable_projects)
                lines.append(f"  [{wf.name}] {wf.description}")
                lines.append(f"    Projects: {projects}")
            lines.append("")
    else:
        lines.append("CLAUDE CODE INTEL: No new workflows discovered today.")
        lines.append("")

    # Action Items — only actual pending work (no inactive-project entries)
    if action_items and action_items["total"] > 0:
        lines.append(f"ACTION ITEMS ({action_items['total']} findings)")
        lines.append("-" * 40)
        if action_items["critical"]:
            lines.append("  CRITICAL:")
            for item in action_items["critical"]:
                lines.append(f"    [!!!] {item['project']}: {item['title']}")
        if action_items["attention"]:
            lines.append("  ATTENTION:")
            for item in action_items["attention"]:
                lines.append(f"    [!!] {item['project']}: {item['title']}")
        if action_items["info"]:
            lines.append(f"  INFO: {action_items['info_count']} lower-priority item(s)")
        lines.append("")

    # Inactive Projects — separate section
    if inactive_projects:
        lines.append(f"INACTIVE PROJECTS ({len(inactive_projects)} projects)")
        lines.append("-" * 40)
        for item in inactive_projects:
            lines.append(f"  [-] {item['project']}: {item['title']}")
        lines.append("")

    lines.append("PROJECT STATUS")
    lines.append("-" * 40)
    for proj in context["projects"]:
        icon = {"active": "+", "idle": "~", "error": "!"}
        lines.append(f"  [{icon.get(proj['status'], '?')}] {proj['name']}: {proj['summary'][:70]}")
        if proj["metrics"]:
            metrics_str = " | ".join(f"{k}: {v}" for k, v in proj["metrics"].items())
            lines.append(f"      {metrics_str}")
    lines.append("")

    plain = "\n".join(lines)
    return html, plain


def deliver_email(html: str, config: dict) -> bool:
    """Send the newsletter via Gmail SMTP."""
    email_config = config["briefing"]["email"]
    smtp_password = get_credential("SMTP_PASSWORD")
    if not smtp_password:
        smtp_password = get_credential("SMTP_APP_PASSWORD")
    if not smtp_password:
        logger.warning("No SMTP credentials found, skipping email delivery")
        return False

    try:
        msg = MIMEMultipart("alternative")
        today = datetime.now().strftime("%Y-%m-%d")
        msg["Subject"] = f"{email_config['subject_prefix']} {today}"
        msg["From"] = email_config["from"]
        msg["To"] = email_config["to"]
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(email_config["from"], smtp_password)
            server.send_message(msg)

        logger.info(f"Email sent to {email_config['to']}")
        return True

    except Exception as e:
        logger.error(f"Email delivery failed: {e}")
        return False


def save_html_file(html: str) -> Path:
    """Save newsletter as local HTML file."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    output_path = OUTPUT_DIR / f"{today}.html"
    output_path.write_text(html, encoding="utf-8")
    logger.info(f"HTML saved to {output_path}")
    return output_path


def run_briefing() -> None:
    """Execute the full briefing pipeline."""
    config = load_config()

    logger.info("Compiling daily briefing...")
    html, plain = compile_briefing()

    html_path = save_html_file(html)

    if config["briefing"]["delivery"].get("email"):
        deliver_email(html, config)

    print(plain)
    print(f"\nHTML newsletter: {html_path}")

    # Post-compile hook: run action pipeline to extract and stage actionable items
    if config.get("action_pipeline", {}).get("enabled", False):
        try:
            from datetime import datetime as _dt
            digest_path = str(
                Path(__file__).resolve().parent.parent
                / "output" / "digests" / f"{_dt.now().strftime('%Y-%m-%d')}.json"
            )
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from actions.pipeline import run_action_pipeline
            count = run_action_pipeline(digest_path, config)
            if count:
                logger.info(f"Action pipeline: {count} items sent for approval")
        except Exception as e:
            logger.warning(f"Action pipeline failed (non-blocking): {e}")


if __name__ == "__main__":
    run_briefing()
