"""
Daily Briefing Pipeline Runner.
Can run individual stages, or the full pipeline in parallel (default) or sequentially.

Usage:
  python run_pipeline.py ai_industry       # Run AI industry news scanner only
  python run_pipeline.py company_news      # Run company news scanner (Anthropic/OpenAI/Google)
  python run_pipeline.py market_news       # Run market news scanner only
  python run_pipeline.py trading           # Alias for market_news
  python run_pipeline.py twitter           # Alias for market_news
  python run_pipeline.py youtube           # Run YouTube scanner only
  python run_pipeline.py briefing          # Run briefing compiler only
  python run_pipeline.py audio             # Generate audio briefing from latest newsletter
  python run_pipeline.py all               # Run full pipeline (parallel scanners + briefing)
  python run_pipeline.py parallel          # Alias for 'all'
  python run_pipeline.py all --sequential  # Run full pipeline sequentially (fallback)
  python run_pipeline.py test              # Dry run — compile briefing with whatever data exists
"""
from __future__ import annotations

import json
import sys
import logging
import time
from concurrent.futures import ProcessPoolExecutor, Future, TimeoutError as FuturesTimeoutError, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

SCANNER_TIMEOUT_SECONDS: int = 600
MIN_SCANNERS_FOR_BRIEFING: int = 2
_PROJECT_DIR: Path = Path(__file__).resolve().parent
_OUTPUT_DIR: Path = _PROJECT_DIR / "output"

_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            _OUTPUT_DIR / f"pipeline_{datetime.now().strftime('%Y-%m-%d')}.log",
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScannerResult:
    """Immutable result from a single scanner execution."""

    name: str
    success: bool
    duration_seconds: float
    output_path: Optional[str]
    error: Optional[str]


def _run_scanner_process(scanner_name: str) -> ScannerResult:
    """
    Execute a single scanner in an isolated process.

    This function is the target for ProcessPoolExecutor. It imports and runs
    the appropriate scanner, returning a structured result.
    """
    start: float = time.monotonic()
    try:
        if scanner_name == "ai_industry":
            from scanners.ai_industry_scanner import run_scanner
            output_path: Path = run_scanner()
        elif scanner_name == "company_news":
            from scanners.company_news_scanner import run_all_companies
            results: dict[str, Path] = run_all_companies()
            if not results:
                raise RuntimeError("company_news_scanner returned no outputs")
            output_path = next(iter(results.values()))
        elif scanner_name == "market_news":
            from scanners.market_news_scanner import run_scanner
            output_path = run_scanner()
        elif scanner_name == "youtube":
            from scanners.youtube_scanner import run_scanner
            output_path = run_scanner()
        else:
            raise ValueError(f"Unknown scanner: {scanner_name}")
        elapsed: float = time.monotonic() - start
        return ScannerResult(
            name=scanner_name,
            success=True,
            duration_seconds=elapsed,
            output_path=str(output_path),
            error=None,
        )
    except Exception as e:
        elapsed = time.monotonic() - start
        return ScannerResult(
            name=scanner_name,
            success=False,
            duration_seconds=elapsed,
            output_path=None,
            error=str(e),
        )


def run_scanners_parallel() -> list[ScannerResult]:
    """
    Run all 4 scanners in parallel via ProcessPoolExecutor.

    Each scanner runs in its own process with a 10-minute timeout.
    If a scanner fails or times out, the others continue unaffected.

    Returns:
        List of ScannerResult for each scanner.
    """
    scanner_names: list[str] = ["ai_industry", "company_news", "market_news", "youtube"]
    results: list[ScannerResult] = []

    logger.info(f"[PARALLEL] Starting {len(scanner_names)} scanners...")

    with ProcessPoolExecutor(max_workers=len(scanner_names)) as executor:
        futures: dict[Future[ScannerResult], str] = {
            executor.submit(_run_scanner_process, name): name
            for name in scanner_names
        }

        for future in as_completed(futures, timeout=SCANNER_TIMEOUT_SECONDS):
            name: str = futures[future]
            try:
                result: ScannerResult = future.result()
                results.append(result)
                if result.success:
                    logger.info(
                        f"[DONE] {result.name} ({result.duration_seconds:.0f}s) -> {result.output_path}"
                    )
                else:
                    logger.error(
                        f"[FAIL] {result.name} ({result.duration_seconds:.0f}s): {result.error}"
                    )
            except FuturesTimeoutError:
                results.append(
                    ScannerResult(
                        name=name,
                        success=False,
                        duration_seconds=SCANNER_TIMEOUT_SECONDS,
                        output_path=None,
                        error=f"Timeout after {SCANNER_TIMEOUT_SECONDS}s",
                    )
                )
                logger.error(f"[FAIL] {name} (timeout after {SCANNER_TIMEOUT_SECONDS}s)")
            except Exception as e:
                results.append(
                    ScannerResult(
                        name=name,
                        success=False,
                        duration_seconds=0.0,
                        output_path=None,
                        error=str(e),
                    )
                )
                logger.error(f"[FAIL] {name}: {e}")

    return results


def run_scanners_sequential() -> list[ScannerResult]:
    """
    Run all 4 scanners sequentially (fallback mode).

    Returns:
        List of ScannerResult for each scanner.
    """
    scanner_names: list[str] = ["ai_industry", "company_news", "market_news", "youtube"]
    results: list[ScannerResult] = []

    logger.info(f"[SEQUENTIAL] Running {len(scanner_names)} scanners one by one...")

    for name in scanner_names:
        logger.info(f"[START] {name}")
        result: ScannerResult = _run_scanner_process(name)
        results.append(result)
        if result.success:
            logger.info(f"[DONE] {result.name} ({result.duration_seconds:.0f}s)")
        else:
            logger.error(f"[FAIL] {result.name} ({result.duration_seconds:.0f}s): {result.error}")

    return results


def run_briefing() -> None:
    """Run the briefing compiler stage."""
    logger.info("=" * 50)
    logger.info("STAGE: Daily Briefing Compiler")
    logger.info("=" * 50)
    from briefing.compiler import run_briefing as compile_briefing

    compile_briefing()


def run_audio_briefing() -> None:
    """Generate audio briefing from the latest newsletter and send to Telegram."""
    logger.info("=" * 50)
    logger.info("STAGE: Audio Briefing (NotebookLM + Telegram)")
    logger.info("=" * 50)
    from briefing.audio_briefing import generate_audio_briefing

    # Read today's digest to build plain text
    today_str: str = datetime.now().strftime("%Y-%m-%d")
    digest_path: Path = _OUTPUT_DIR / "digests" / f"{today_str}.json"

    if not digest_path.exists():
        logger.error(f"No digest found for today ({digest_path}). Run 'briefing' stage first.")
        return

    digest: dict = json.loads(digest_path.read_text(encoding="utf-8"))
    sections: list[str] = []
    for key in ["anthropic_news", "openai_news", "google_ai_news", "ai_industry",
                "market_news", "claude_workflows", "project_applicability"]:
        section = digest.get(key)
        if section and section.get("cliff_notes"):
            label: str = key.upper().replace("_", " ")
            count: int = section.get("source_count", 0)
            header: str = f"{label} ({count} sources)" if count else label
            sections.append(f"{header}\n{section['cliff_notes']}")
            insights: list[str] = section.get("key_insights", [])
            if insights:
                for insight in insights:
                    sections.append(f"  > {insight}")

    plain_text: str = f"Daily Briefing — {today_str}\n\n" + "\n\n".join(sections)

    result = generate_audio_briefing(plain_text)
    if result.success:
        logger.info(f"Audio: {result.audio_path}")
        logger.info(f"Telegram: {'sent' if result.telegram_sent else 'not sent'}")
        logger.info(f"Total duration: {result.duration_seconds:.0f}s")
    else:
        logger.error(f"Audio briefing failed: {result.error}")


def run_full_pipeline(sequential: bool = True) -> None:
    """
    Run the full pipeline: all scanners then the briefing compiler.

    Args:
        sequential: If True, run scanners one by one instead of in parallel.
    """
    if sequential:
        results: list[ScannerResult] = run_scanners_sequential()
    else:
        results = run_scanners_parallel()

    succeeded: int = sum(1 for r in results if r.success)
    failed: int = len(results) - succeeded

    logger.info("=" * 50)
    logger.info(f"Scanner Summary: {succeeded} succeeded, {failed} failed")
    for r in results:
        status: str = "OK" if r.success else "FAIL"
        logger.info(f"  [{status}] {r.name}: {r.duration_seconds:.0f}s")
    logger.info("=" * 50)

    if succeeded >= MIN_SCANNERS_FOR_BRIEFING:
        logger.info(
            f"At least {MIN_SCANNERS_FOR_BRIEFING} scanners succeeded — running briefing compiler"
        )
        run_briefing()
    else:
        logger.error(
            f"Only {succeeded} scanner(s) succeeded (need {MIN_SCANNERS_FOR_BRIEFING}). "
            "Skipping briefing compiler."
        )


def run_single_scanner(scanner_name: str) -> None:
    """Run a single named scanner with logging."""
    display_names: dict[str, str] = {
        "ai_industry": "AI Industry News Scanner",
        "company_news": "Company News Scanner (Anthropic/OpenAI/Google)",
        "market_news": "Market News Scanner",
        "youtube": "YouTube Claude Code Scanner",
    }
    logger.info("=" * 50)
    logger.info(f"STAGE: {display_names.get(scanner_name, scanner_name)}")
    logger.info("=" * 50)

    result: ScannerResult = _run_scanner_process(scanner_name)
    if result.success:
        logger.info(f"Scanner output: {result.output_path}")
    else:
        raise RuntimeError(f"Scanner {scanner_name} failed: {result.error}")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    stage: str = sys.argv[1].lower()
    sequential: bool = "--sequential" in sys.argv
    start: datetime = datetime.now()
    logger.info(f"Pipeline started at {start.strftime('%I:%M %p')}")

    try:
        if stage == "usage":
            from config.llm import get_usage_summary

            summary: dict[str, object] = get_usage_summary()
            print(json.dumps(summary, indent=2))
            return
        elif stage == "ai_industry":
            run_single_scanner("ai_industry")
        elif stage == "company_news":
            run_single_scanner("company_news")
        elif stage in ("market_news", "trading", "twitter"):
            run_single_scanner("market_news")
        elif stage == "youtube":
            run_single_scanner("youtube")
        elif stage == "gemini":
            logger.warning("'gemini' stage is deprecated — use 'company_news' for company-specific AI news")
        elif stage == "audio":
            run_audio_briefing()
        elif stage in ("briefing", "test"):
            run_briefing()
        elif stage in ("all", "parallel"):
            run_full_pipeline(sequential=sequential)
        else:
            print(f"Unknown stage: {stage}")
            print(__doc__)
            sys.exit(1)

        elapsed: float = (datetime.now() - start).total_seconds()
        logger.info(f"Pipeline completed in {elapsed:.0f}s")

    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
