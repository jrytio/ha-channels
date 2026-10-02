"""Start a recording on a Channels app and wait until it is really playing.

Two things the app does, measured on tvOS on 2026-10-02, make "it reports the
recording" not enough. Sitting paused at 0 after a recording played to its
end, the app drops the first play command and stops. And for about 1.5 s
after a play it reports the recording and a frozen position, and discards
any seek sent then.
"""

from __future__ import annotations

from typing import Protocol

from .clock import Clock
from .models import AppStatus

RESEND_AFTER = 3.0  # a dropped play stays dropped; a resend works within 1 s
POLL = 0.5  # playback is advancing once two reads this far apart differ by half


class Playable(Protocol):
    """The parts of AppClient this module uses."""

    async def status(self) -> AppStatus: ...
    async def play_recording(self, recording_id: str) -> AppStatus: ...


async def start_playback(
    player: Playable, recording_id: str, max_wait: float, clock: Clock
) -> AppStatus | None:
    """Play a recording and return the first status read once it is advancing.

    The play command is sent again whenever the app has not been on the
    recording for `RESEND_AFTER` seconds since the last one. Returns None if
    playback is not advancing within `max_wait` seconds.
    """
    deadline = clock.time() + max_wait
    await player.play_recording(recording_id)
    sent_at = clock.time()
    previous: AppStatus | None = None
    while True:
        status = await player.status()
        if status.recording_id == recording_id and status.position is not None:
            if previous is not None and status.position - previous.position >= POLL / 2:
                return status
            previous = status
        else:
            previous = None
            if clock.time() - sent_at >= RESEND_AFTER and clock.time() < deadline:
                await player.play_recording(recording_id)
                sent_at = clock.time()
        if clock.time() >= deadline:
            return None
        await clock.sleep(POLL)
