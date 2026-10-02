"""A clock the sync engine can be driven by, so tests need no real waiting."""

from __future__ import annotations

import asyncio
import time
from typing import Protocol


class Clock(Protocol):
    """Wall-clock time and sleeping."""

    def time(self) -> float:
        """Return seconds since the epoch."""

    async def sleep(self, seconds: float) -> None:
        """Wait for the given number of seconds."""


class SystemClock:
    """The real clock."""

    def time(self) -> float:
        """Return seconds since the epoch."""
        return time.time()

    async def sleep(self, seconds: float) -> None:
        """Wait for the given number of seconds."""
        await asyncio.sleep(seconds)
