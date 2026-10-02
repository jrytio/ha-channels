"""Bring one Channels app onto another's recording and position.

Small seeks land unpredictably and large ones lose up to a second, but a
timed pause shifts playback by the pause length plus a small fixed overhead.
So the engine seeks the follower to slightly ahead of the leader, then holds
it still for exactly its lead.

The constants were measured on tvOS on 2026-10-01.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from statistics import median
from typing import Protocol

from .clock import Clock
from .models import STATE_PAUSED, STATE_PLAYING, AppStatus, ChannelsError
from .playback import start_playback

LEAD = 1.0  # a coarse seek aims the follower this far ahead of the leader
MAX_PAUSE = 3.0  # the largest lead fixed by pausing rather than seeking
PAUSE_OVERHEAD = 0.045  # extra shift a pause/resume pair adds
SETTLE = 1.0  # wait after a correction before measuring again
SAMPLES = 5
SAMPLE_GAP = 0.2
MAX_ROUNDS = 5
START_TIMEOUT = 15.0
POLL = 0.5

SYNCED = "synced"
OUT_OF_TOLERANCE = "out_of_tolerance"
SKIPPED = "skipped"


class LeaderError(ChannelsError):
    """The leader cannot be synced to."""


class Player(Protocol):
    """The parts of AppClient the engine uses."""

    async def status(self) -> AppStatus: ...
    async def play_recording(self, recording_id: str) -> AppStatus: ...
    async def seek(self, seconds: float) -> AppStatus: ...
    async def pause(self) -> AppStatus: ...
    async def resume(self) -> AppStatus: ...


@dataclass(frozen=True, slots=True)
class SyncResult:
    """How one follower ended up."""

    status: str
    offset_ms: float | None = None
    rounds: int = 0
    reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-friendly form, leaving out what does not apply."""
        data: dict[str, object] = {"status": self.status}
        if self.offset_ms is not None:
            data["offset_ms"] = round(self.offset_ms, 1)
            data["rounds"] = self.rounds
        if self.reason is not None:
            data["reason"] = self.reason
        return data


async def wait_until_reachable(player: Player, max_wait: float, clock: Clock) -> bool:
    """Poll until the app answers, or `max_wait` seconds pass."""
    deadline = clock.time() + max_wait
    while True:
        try:
            await player.status()
        except ChannelsError:
            if clock.time() >= deadline:
                return False
            await clock.sleep(POLL)
        else:
            return True


async def leader_status(leader: Player) -> AppStatus:
    """Return the leader's status, or raise if it cannot be synced to."""
    status = await leader.status()
    if not status.is_active:
        raise LeaderError("The leader is not playing anything")
    if status.recording_id is None or status.position is None:
        raise LeaderError("The leader is on live TV; only a recording can be synced to")
    return status


async def measure_offset(
    leader: Player, follower: Player, clock: Clock
) -> float | None:
    """Return how many seconds the follower is ahead of the leader.

    Negative means behind. None means the follower reports no position.
    """
    samples: list[float] = []
    for _ in range(SAMPLES):
        lead, follow = await asyncio.gather(leader_status(leader), follower.status())
        if follow.position is not None:
            offset = follow.position - lead.position
            if lead.state == STATE_PLAYING:
                # The two positions were read at slightly different instants.
                offset -= follow.sampled_at - lead.sampled_at
            samples.append(offset)
        await clock.sleep(SAMPLE_GAP)
    return median(samples) if samples else None


async def _hold(player: Player, seconds: float, clock: Clock) -> None:
    """Pause for a measured time, counting the pause call's own latency."""
    started = clock.time()
    await player.pause()
    await clock.sleep(max(0.0, seconds - (clock.time() - started)))
    await player.resume()


async def _match_play_state(
    leader: Player, follower: Player, recording_id: str
) -> AppStatus:
    """Put the follower in the leader's play state and return the leader's status.

    A paused side has a frozen clock, so this runs whenever the leader may have
    changed. Raises LeaderError if the leader left the recording being synced.
    """
    lead = await leader_status(leader)
    if lead.recording_id != recording_id:
        raise LeaderError("The leader changed to a different recording during the sync")
    follow = await follower.status()
    if lead.state == STATE_PLAYING and follow.state == STATE_PAUSED:
        await follower.resume()
    elif lead.state == STATE_PAUSED and follow.state == STATE_PLAYING:
        await follower.pause()
    return lead


async def sync_follower(
    leader: Player,
    follower: Player,
    *,
    clock: Clock,
    tolerance: float = 0.05,
    target: float = 0.0,
    start_timeout: float = START_TIMEOUT,
) -> SyncResult:
    """Put the follower on the leader's recording at the leader's position.

    `target` is the offset to aim for, in seconds: 0 lines the two up, and
    -0.08 leaves the follower 80 ms behind the leader.

    Raises LeaderError when the leader is stopped or on live TV.
    """
    lead = await leader_status(leader)
    follow = await follower.status()

    # A recording that played to its end leaves the app paused at exactly 0 on
    # that recording, and a resume from there reports playing at a frozen 0.
    # A play command is what gets it going; one from an ordinary pause works too.
    at_end = (
        follow.recording_id == lead.recording_id
        and follow.state == STATE_PAUSED
        and follow.position == 0
        and lead.state == STATE_PLAYING
    )
    if follow.recording_id != lead.recording_id or at_end:
        # Measure only once playback is really running: a seek sent while
        # the app is still loading is discarded.
        if (
            await start_playback(follower, lead.recording_id, start_timeout, clock)
            is None
        ):
            return SyncResult(SKIPPED, reason="It did not start the recording")
        lead = await leader_status(leader)
        follow = await follower.status()

    recording_id = lead.recording_id

    offset: float | None = None
    corrections = 0
    for attempt in range(1, MAX_ROUNDS + 2):
        before = await _match_play_state(leader, follower, recording_id)
        measured = await measure_offset(leader, follower, clock)
        if measured is None:
            return SyncResult(SKIPPED, reason="It stopped reporting a position")
        after = await _match_play_state(leader, follower, recording_id)
        if before.state != after.state:
            # The leader paused or resumed mid-measurement, so the samples
            # mix two play states. Measure again from the new one.
            offset = None
            continue
        offset = measured
        error = offset - target
        if abs(error) <= tolerance or attempt > MAX_ROUNDS:
            break

        if after.state == STATE_PAUSED:
            # Nothing is moving, so a seek is the only tool.
            await follower.seek(-error)
        elif PAUSE_OVERHEAD < error <= MAX_PAUSE:
            await _hold(follower, error - PAUSE_OVERHEAD, clock)
        else:
            await follower.seek(LEAD - error)
        corrections += 1
        await clock.sleep(SETTLE)

    if offset is None:
        return SyncResult(
            SKIPPED, reason="The leader kept pausing and resuming during the sync"
        )
    status = SYNCED if abs(offset - target) <= tolerance else OUT_OF_TOLERANCE
    return SyncResult(status, offset_ms=offset * 1000, rounds=corrections)
