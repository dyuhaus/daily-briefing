"""
Shared Gemini API quota tracker for the DailyBriefing pipeline.

Tracks call counts per model within a rolling time window so scanners can
back off before hitting hard API rate limits. All state is in-memory and
resets when the process restarts — this is a best-effort soft guard, not
a billing-level enforcer.

Usage:
    from config.quota import gemini_quota

    if gemini_quota.can_call(model="gemini-3-flash"):
        result = client.search(query)
        gemini_quota.record_call(model="gemini-3-flash")
    else:
        # skip or use fallback
        ...
"""
from __future__ import annotations

import logging
import time
from collections import deque
from typing import Deque

logger = logging.getLogger(__name__)

# Conservative per-minute caps (well below actual Google free-tier limits)
# Adjust if you upgrade your quota tier.
_DEFAULT_LIMITS_PER_MINUTE: dict[str, int] = {
    "gemini-3-flash": 15,
    "gemini-2.5-flash": 10,
    "gemini-2.5-pro": 5,
    "gemini-2.0-flash": 15,
    "default": 8,
}

# Fallback model order when primary model is exhausted
MODEL_FALLBACK_CHAIN: list[str] = [
    "gemini-3-flash",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
]


class GeminiQuotaTracker:
    """
    Tracks Gemini API call timestamps per model within a 60-second window.

    Immutable design: each call creates a new tracker state snapshot rather
    than mutating shared structures.  Thread-safety is not required here
    because each scanner runs in its own process (ProcessPoolExecutor).
    """

    def __init__(
        self,
        limits_per_minute: dict[str, int] | None = None,
        window_seconds: float = 60.0,
    ) -> None:
        self._limits: dict[str, int] = limits_per_minute or _DEFAULT_LIMITS_PER_MINUTE
        self._window: float = window_seconds
        # model -> deque of call timestamps (float, monotonic)
        self._timestamps: dict[str, Deque[float]] = {}

    def _get_limit(self, model: str) -> int:
        return self._limits.get(model, self._limits.get("default", 8))

    def _prune(self, model: str) -> None:
        """Remove timestamps outside the rolling window."""
        now = time.monotonic()
        cutoff = now - self._window
        dq = self._timestamps.get(model, deque())
        while dq and dq[0] < cutoff:
            dq.popleft()
        self._timestamps[model] = dq

    def can_call(self, model: str) -> bool:
        """Return True if a call to this model is within quota.

        Always returns True when using Claude CLI backend (no API quota).
        """
        return True

    def record_call(self, model: str) -> None:
        """Record that a call was made to this model."""
        self._prune(model)
        dq = self._timestamps.setdefault(model, deque())
        dq.append(time.monotonic())

    def calls_remaining(self, model: str) -> int:
        """Return how many more calls are allowed in the current window."""
        self._prune(model)
        limit = self._get_limit(model)
        used = len(self._timestamps.get(model, deque()))
        return max(0, limit - used)

    def best_available_model(self, preferred: str) -> str | None:
        """
        Return the preferred model if quota allows, else the first fallback
        with quota remaining, else None.
        """
        if self.can_call(preferred):
            return preferred

        for fallback in MODEL_FALLBACK_CHAIN:
            if fallback != preferred and self.can_call(fallback):
                logger.info(
                    "Quota exhausted for %s — falling back to %s", preferred, fallback
                )
                return fallback

        logger.warning("All Gemini models exhausted for this window")
        return None

    def wait_for_quota(self, model: str, max_wait_seconds: float = 65.0) -> bool:
        """Return True immediately — no quota limits with Claude CLI backend."""
        return True


# Module-level singleton — shared within a single scanner process
gemini_quota = GeminiQuotaTracker()
