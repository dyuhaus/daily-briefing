"""
Project status reader — pulls live metrics from background projects.
"""
from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [StatusReader] %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class ProjectStatus:
    name: str
    status: str  # "active", "idle", "error"
    summary: str
    last_activity: str
    metrics: dict


def _git_last_commit(repo_path: Path) -> tuple[str, str]:
    """Get last commit message and date from a git repo."""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%s|%ai"],
            cwd=str(repo_path),
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split("|", 1)
            return parts[0], parts[1] if len(parts) > 1 else ""
    except Exception:
        pass
    return "", ""


def _count_lines_matching(file_path: Path, pattern: str) -> int:
    """Count lines in a file matching a simple substring."""
    if not file_path.exists():
        return 0
    count = 0
    for line in file_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if pattern.lower() in line.lower():
            count += 1
    return count


def read_sports_betting_swarm(path: str) -> ProjectStatus:
    repo = Path(path)
    msg, date = _git_last_commit(repo)
    old_tests = repo / "docs" / "old-tests.md"
    total_runs = _count_lines_matching(old_tests, "score:")

    # Try to read dashboard state
    metrics: dict = {"total_runs": total_runs}
    dashboard = repo / "dashboard_state.json"
    if dashboard.exists():
        try:
            data = json.loads(dashboard.read_text(encoding="utf-8"))
            metrics.update({
                "current_score": data.get("current_score"),
                "improvements": data.get("improvements"),
            })
        except Exception:
            pass

    return ProjectStatus(
        name="Sports Betting Swarm",
        status="active" if date else "idle",
        summary=msg or "No recent activity",
        last_activity=date,
        metrics=metrics,
    )


def read_march_madness_swarm(path: str) -> ProjectStatus:
    repo = Path(path)
    msg, date = _git_last_commit(repo)
    old_tests = repo / "docs" / "old_tests.md"
    total_experiments = _count_lines_matching(old_tests, "experiment")

    return ProjectStatus(
        name="March Madness Swarm",
        status="active" if date else "idle",
        summary=msg or "No recent activity",
        last_activity=date,
        metrics={"total_experiments": total_experiments},
    )



def read_forge(path: str) -> ProjectStatus:
    repo = Path(path)
    msg, date = _git_last_commit(repo)

    return ProjectStatus(
        name="FORGE",
        status="active" if date else "idle",
        summary=msg or "No recent activity",
        last_activity=date,
        metrics={},
    )


def read_market_swarm(path: str) -> ProjectStatus:
    repo = Path(path)
    msg, date = _git_last_commit(repo)

    metrics: dict = {}
    swarm_status = repo / "results" / "swarm_status.json"
    if swarm_status.exists():
        try:
            data = json.loads(swarm_status.read_text(encoding="utf-8"))
            metrics.update(data)
        except Exception:
            pass

    portfolio_file = repo / "portfolio_manager" / "portfolio.json"
    if portfolio_file.exists():
        try:
            pdata = json.loads(portfolio_file.read_text(encoding="utf-8"))
            metrics["portfolio_value"] = pdata.get("total_value")
            metrics["positions"] = len(pdata.get("holdings", {}))
        except Exception:
            pass

    return ProjectStatus(
        name="MarketSwarm (AlgoSwarm)",
        status="active" if date else "idle",
        summary=msg or "No recent activity",
        last_activity=date,
        metrics=metrics,
    )


def read_quant_market_data(path: str) -> ProjectStatus:
    repo = Path(path)
    msg, date = _git_last_commit(repo)

    return ProjectStatus(
        name="QuantMarketData (Kalshi)",
        status="active" if date else "idle",
        summary=msg or "No recent activity",
        last_activity=date,
        metrics={},
    )


def read_kalshi_trader(path: str) -> ProjectStatus:
    """Read KalshiTrader paper trading v2 results."""
    repo = Path(path)
    msg, date = _git_last_commit(repo)

    metrics: dict = {}
    reports_dir = repo / "logs" / "paper_v2" / "reports"

    if reports_dir.exists():
        # Find the most recent report
        report_files = sorted(reports_dir.glob("paper_v2_report_*.json"), reverse=True)
        if report_files:
            try:
                data = json.loads(report_files[0].read_text(encoding="utf-8"))
                metrics["balance"] = data.get("current_balance")
                metrics["total_pnl"] = data.get("total_pnl")
                metrics["roi"] = data.get("roi")
                metrics["total_fills"] = data.get("total_fills")
                metrics["settlements"] = data.get("total_settlements")
                metrics["win_rate"] = data.get("win_rate")
                metrics["open_positions"] = data.get("open_positions")
                metrics["resting_orders"] = data.get("resting_orders")

                # Build summary from data
                wins = data.get("wins", 0)
                losses = data.get("losses", 0)
                pnl = data.get("total_pnl", 0)
                roi_val = data.get("roi", 0)
                msg = (
                    f"Paper V2: {wins}W-{losses}L | "
                    f"PnL ${pnl:+.2f} ({roi_val:.1%} ROI)"
                )
            except Exception:
                pass

    # Also check fill ledger for activity timestamp
    fill_ledger = repo / "logs" / "paper_v2" / "fill_ledger.jsonl"
    if fill_ledger.exists():
        try:
            lines = fill_ledger.read_text(encoding="utf-8").strip().splitlines()
            if lines:
                last_fill = json.loads(lines[-1])
                date = last_fill.get("fill_time", date)
        except Exception:
            pass

    return ProjectStatus(
        name="KalshiTrader (Paper V2)",
        status="active" if date else "idle",
        summary=msg or "Paper trader running — no fills yet",
        last_activity=date,
        metrics=metrics,
    )


def read_context_file(project_path: str) -> Optional[str]:
    """Read a project's CONTEXT.md for enhanced status information."""
    context_path = Path(project_path) / "CONTEXT.md"
    if not context_path.exists():
        return None
    try:
        content = context_path.read_text(encoding="utf-8", errors="ignore")
        # Extract just the Status Snapshot section (first ~20 lines of content)
        lines = content.splitlines()
        snapshot_lines: list[str] = []
        in_snapshot = False
        for line in lines:
            if "## Status Snapshot" in line:
                in_snapshot = True
                continue
            elif line.startswith("## ") and in_snapshot:
                break
            elif in_snapshot:
                snapshot_lines.append(line.strip())
        return "\n".join(l for l in snapshot_lines if l)
    except Exception:
        return None


def read_latest_report(project_path: str, report_glob: str = "*.md") -> Optional[str]:
    """Read the most recent report file from a project's reports directory."""
    reports_dir = Path(project_path) / "reports"
    if not reports_dir.exists():
        # Try results directory (MarketSwarm uses results/)
        reports_dir = Path(project_path) / "results"
    if not reports_dir.exists():
        return None

    report_files = sorted(reports_dir.glob(report_glob), reverse=True)
    # Filter to only report files (not status JSONs)
    report_files = [f for f in report_files if f.suffix == ".md" and "report" in f.name.lower()]

    if not report_files:
        return None

    try:
        content = report_files[0].read_text(encoding="utf-8", errors="ignore")
        # Return first 30 lines (summary, not full report)
        lines = content.splitlines()[:30]
        return "\n".join(lines)
    except Exception:
        return None


def _sanitize_summary(summary: str) -> str:
    """Clean up raw git commit messages and CONTEXT.md lines for newsletter display."""
    if not summary:
        return "No recent activity"
    # Strip leading markdown bullets/dashes
    summary = summary.lstrip("- *#").strip()
    # Truncate to first sentence or 120 chars
    for sep in [". ", "\n", " — "]:
        idx = summary.find(sep)
        if 0 < idx < 120:
            summary = summary[:idx]
            break
    return summary[:120]


def _clean_metrics(metrics: dict) -> dict:
    """Remove noisy or empty metrics that clutter the newsletter display."""
    # Keys to always exclude from newsletter rendering
    exclude_keys = {"latest_report_preview", "workers", "improvements"}

    cleaned: dict = {}
    for key, value in metrics.items():
        if key in exclude_keys:
            continue
        # Skip None, empty lists, empty strings
        if value is None or value == [] or value == "":
            continue
        # Format numeric values for readability
        if isinstance(value, float):
            cleaned[key] = round(value, 2)
        else:
            cleaned[key] = value

    return cleaned


# Human-friendly display names for metric keys
_METRIC_LABELS: dict[str, str] = {
    "total_runs": "Runs",
    "total_experiments": "Experiments",
    "current_score": "Score",
    "last_val_bpb": "Val BPB",
    "current_round": "Round",
    "baseline_score": "Baseline",
    "status": "Status",
    "positions": "Positions",
    "portfolio_value": "Portfolio",
    "balance": "Balance",
    "total_pnl": "P&L",
    "roi": "ROI",
    "total_fills": "Fills",
    "settlements": "Settled",
    "win_rate": "Win Rate",
    "open_positions": "Open Pos",
    "resting_orders": "Resting",
}


def _format_metric_label(key: str) -> str:
    """Convert metric key to human-readable label."""
    return _METRIC_LABELS.get(key, key.replace("_", " ").title())


def read_all_statuses(config: dict) -> list[ProjectStatus]:
    """Read status from all configured projects."""
    sources = config.get("project_status_sources", {})
    statuses: list[ProjectStatus] = []

    readers = {
        "SportsBettingSwarm": read_sports_betting_swarm,
        "MarchMadnessSwarm": read_march_madness_swarm,
        "FORGE": read_forge,
        "MarketSwarm": read_market_swarm,
        "QuantMarketData": read_quant_market_data,
        "KalshiTrader": read_kalshi_trader,
    }

    for name, source_config in sources.items():
        reader = readers.get(name)
        if reader:
            try:
                status = reader(source_config["path"])
                # Enhance with CONTEXT.md snapshot
                context_snapshot = read_context_file(source_config["path"])
                if context_snapshot:
                    status.summary = context_snapshot.split('\n')[0] if context_snapshot else status.summary

                # Clean up summary and metrics for newsletter display
                status.summary = _sanitize_summary(status.summary)
                status.metrics = _clean_metrics(status.metrics)

                # Rename metric keys to human-readable labels
                status.metrics = {
                    _format_metric_label(k): v
                    for k, v in status.metrics.items()
                }

                statuses.append(status)
            except Exception as e:
                logger.warning(f"Failed to read {name}: {e}")
                statuses.append(ProjectStatus(
                    name=name, status="error",
                    summary=f"Failed to read: {e}",
                    last_activity="", metrics={},
                ))

    return statuses
