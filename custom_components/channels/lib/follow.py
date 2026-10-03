"""Keep followers on a leader's recording and position for as long as it runs.

A session reads the leader once a second. Each follower is then read and, if
it is out of line, corrected. Nothing here tries to notice that the leader
"just skipped": a follower is only ever compared with where it should be, so
a skip on the leader, a rewind with a follower's own remote and plain drift
are one case.

The constants were measured on tvOS on 2026-10-01 and 2026-10-02.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import logging
from typing import Protocol

from .clock import Clock
from .models import STATE_PAUSED, STATE_PLAYING, AppStatus, ChannelsError
from .playback import start_playback
from .recording import ChannelChanged, RecordingDvr, switch_to_recording
from .sync import SYNCED, LeaderError, Player, sync_follower

_LOGGER = logging.getLogger(__name__)

TICK = 1.0  # one small request per TV per second
JUMP = 1.0  # beyond this a plain seek is quicker; plain seeks land within 0.1-1 s
CONFIRM = 2  # single paired readings were seen up to 130 ms out with nothing wrong
PAUSED_SLACK = 1.5  # keyframes are 0.7-1.0 s apart; a pause arrives a second late
FINE_TOLERANCE = 0.05  # what a fine-tune aims for
START_TIMEOUT = 15.0
LEADER_JUMP = 0.5  # the leader moved this far from where playing would put it
REST_AFTER = 5  # failed corrections in a row before a follower is rested
REST_FOR = 30.0

IN_SYNC = "in_sync"
CORRECTING = "correcting"
RESTING = "resting"
ABSENT = "absent"

NO_DVR = (
    "The leader is on live TV and no Channels DVR server is set up, "
    "so the other TVs cannot follow it"
)
UNEXPECTED = "An unexpected error occurred; see the log"
WILL_NOT_SETTLE = "It could not be kept in step with the leader; trying again shortly"


class LeaderApp(Player, Protocol):
    """The parts of AppClient the session uses on the leader."""

    async def toggle_record(self) -> AppStatus: ...


class _GuardedLeader:
    """The leader as a fine-tune sees it: a reading is only good if followable.

    A leader that reaches the end of its recording mid-tune reports position 0;
    without this the tune would drag the follower to the start.
    """

    def __init__(self, leader: Player) -> None:
        self._leader = leader
        self.play_recording = leader.play_recording
        self.seek = leader.seek
        self.pause = leader.pause
        self.resume = leader.resume

    async def status(self) -> AppStatus:
        reading = await self._leader.status()
        if not _followable(reading):
            raise LeaderError("The leader is no longer playing a recording")
        return reading


@dataclass(frozen=True, slots=True)
class Follower:
    """A follower's app and the offset to hold it at, in seconds.

    A target of -0.08 keeps it 80 ms behind the leader.
    """

    player: Player
    target: float = 0.0


@dataclass(slots=True)
class _Live:
    """The live channel the leader is on, and how that is going."""

    channel: str
    since: float
    gave_up: bool = False


@dataclass(slots=True)
class _Memory:
    """What one follower's task remembers between passes."""

    out_of_line: int = 0  # passes in a row between the tolerance and JUMP
    tighten: bool = False  # a coarse correction was made; fine-tune next
    jumped_in: int | None = None  # the leader epoch of its last plain seek
    failures: int = 0  # failed corrections in a row
    rest_until: float = 0.0


def _followable(status: AppStatus) -> bool:
    """Return True when the leader is on a recording and moving through it.

    A recording that has played to its end sits at position 0, reported as
    playing or paused; following that would drag every follower to the start.
    One just started from the beginning reports 0 too, for a second or two
    while it loads, and is followed as soon as it moves.
    """
    return (
        status.is_active and status.recording_id is not None and bool(status.position)
    )


class FollowSession:
    """One leader, its followers, and the tasks that keep them together."""

    def __init__(
        self,
        leader: LeaderApp,
        followers: Mapping[str, Follower],
        *,
        dvr: RecordingDvr | None,
        clock: Clock,
        tolerance: float = 0.25,
        live_settle: float = 10.0,
        behind_live: float = 5.0,
        switch_lock: asyncio.Lock | None = None,
        on_problem: Callable[[str | None, str], None] | None = None,
        on_status: Callable[[str, str], None] | None = None,
    ) -> None:
        """Set up a session; nothing happens until `run` is awaited.

        `followers` maps a name to its app. `on_problem` is called with a
        follower's name, or None for the leader, and a sentence fit for a
        notification. `on_status` is called when a follower's status changes.
        """
        self._leader = leader
        self._followers = dict(followers)
        self._dvr = dvr
        self._clock = clock
        self._tolerance = tolerance
        self._live_settle = live_settle
        self._behind_live = behind_live
        self._switch_lock = switch_lock or asyncio.Lock()
        self._on_problem = on_problem
        self._on_status = on_status
        self.statuses: dict[str, str] = dict.fromkeys(self._followers, ABSENT)

        # What the leader loop publishes for the follower tasks.
        self._sample: AppStatus | None = None  # None: nothing to follow
        self._sample_number = 0
        self._published = asyncio.Event()
        # Goes up whenever the leader does something a follower must react to,
        # so a follower can tell "my correction failed" from "the leader moved".
        self._epoch = 0
        self._previous: AppStatus | None = None
        self._live: _Live | None = None
        self._no_dvr_told = False  # NO_DVR has been reported this visit to live TV
        self._guarded_leader = _GuardedLeader(leader)

    async def run(self) -> None:
        """Run until cancelled."""
        async with asyncio.TaskGroup() as group:
            group.create_task(self._lead())
            for name, follower in self._followers.items():
                group.create_task(self._follow(name, follower))

    # -- the leader -----------------------------------------------------------

    async def _lead(self) -> None:
        while True:
            started = self._clock.time()
            try:
                await self._read_leader()
            except Exception:
                _LOGGER.exception("Unexpected error reading the leader")
                self._publish(None)
            await self._clock.sleep(max(0.0, TICK - (self._clock.time() - started)))

    async def _read_leader(self) -> None:
        try:
            status = await self._leader.status()
        except ChannelsError:
            # An unreadable leader says nothing about what it is on, so what is
            # known about its live channel stays.
            self._publish(None)
            return
        on_live = (
            status.is_active
            and status.recording_id is None
            and status.channel_number is not None
        )
        if on_live:
            self._publish(None)
            await self._handle_live(status.channel_number)
            return
        self._live = None
        self._no_dvr_told = False
        self._publish(status if _followable(status) else None)

    def _publish(self, sample: AppStatus | None) -> None:
        """Hand the followers a new reading of the leader."""
        previous = self._previous
        if (sample is None) != (previous is None):
            self._epoch += 1
        elif sample is not None and previous is not None:
            moved = sample.position - previous.position
            if previous.state == STATE_PLAYING:
                moved -= sample.sampled_at - previous.sampled_at
            if (
                sample.recording_id != previous.recording_id
                or sample.state != previous.state
                or abs(moved) > LEADER_JUMP
            ):
                self._epoch += 1
        self._previous = sample
        self._sample = sample
        self._sample_number += 1
        published, self._published = self._published, asyncio.Event()
        published.set()

    async def _handle_live(self, channel: str) -> None:
        """Move a leader that is on live TV onto a recording, when it is time."""
        if self._live is None or self._live.channel != channel:
            self._live = _Live(channel, self._clock.time())
            first_look = True
        else:
            first_look = False
        live = self._live
        if live.gave_up:
            return
        if self._dvr is None:
            # Once per visit to live TV, and only when the leader has stayed.
            if (
                not first_look
                and not self._no_dvr_told
                and self._clock.time() - live.since >= self._live_settle
            ):
                self._no_dvr_told = True
                self._problem(None, NO_DVR)
            return
        try:
            if first_look:
                # A channel that is already being recorded costs nothing to
                # follow and frees a tuner, so it is not made to wait.
                recording = await self._dvr.in_progress_recording(channel)
                waiting = recording is None and not await self._dvr.has_active_job(
                    channel, self._clock.time()
                )
                if waiting:
                    return
            elif self._clock.time() - live.since < self._live_settle:
                return
            async with self._switch_lock:
                await switch_to_recording(
                    self._leader,
                    self._dvr,
                    clock=self._clock,
                    behind_live=self._behind_live,
                    channel=channel,
                )
        except ChannelChanged:
            # Nothing was sent. The next reading shows the new channel and
            # starts its settle time.
            return
        except ChannelsError as err:
            # No second try until the leader changes channel: a retry could
            # send the record toggle again.
            self._give_up(live, str(err))
        except Exception:
            _LOGGER.exception("Unexpected error moving the leader to a recording")
            self._give_up(live, UNEXPECTED)

    def _give_up(self, live: _Live, message: str) -> None:
        live.gave_up = True
        self._problem(None, message)

    # -- a follower -----------------------------------------------------------

    async def _next_sample(self, seen: int) -> tuple[int, AppStatus | None]:
        while self._sample_number == seen:
            await self._published.wait()
        return self._sample_number, self._sample

    async def _follow(self, name: str, follower: Follower) -> None:
        memory = _Memory()
        seen = 0
        while True:
            seen, lead = await self._next_sample(seen)
            if self._clock.time() < memory.rest_until:
                continue
            try:
                await self._step(name, follower, memory, lead)
            except ChannelsError as err:
                _LOGGER.debug("Correcting %s failed: %s", name, err)
                self._failed(name, memory)
            except Exception:
                _LOGGER.exception("Unexpected error following on %s", name)
                self._rest(name, memory, UNEXPECTED)

    async def _step(
        self, name: str, follower: Follower, memory: _Memory, lead: AppStatus | None
    ) -> None:
        """Look at one follower once and make at most one correction."""
        player = follower.player
        try:
            status = await player.status()
        except ChannelsError:
            # Another app is in front, or the TV is off. Leave it alone.
            memory.out_of_line = 0
            self._set_status(name, ABSENT)
            return
        if lead is None:
            # The leader is stopped, on live TV or gone: nothing to hold to.
            self._set_status(name, IN_SYNC)
            return

        left_at_end = status.position == 0 and lead.position > JUMP
        if (
            status.recording_id != lead.recording_id
            or status.position is None
            or left_at_end
        ):
            # From the end of a recording only a play command gets it going.
            self._set_status(name, CORRECTING)
            memory.out_of_line = 0
            started = await start_playback(
                player, lead.recording_id, START_TIMEOUT, self._clock
            )
            if started is None:
                self._failed(name, memory)
            memory.tighten = True
            return

        if lead.state == STATE_PAUSED and status.state == STATE_PLAYING:
            self._set_status(name, CORRECTING)
            await player.pause()
            return
        if lead.state == STATE_PLAYING and status.state == STATE_PAUSED:
            self._set_status(name, CORRECTING)
            memory.tighten = True
            await player.resume()
            return

        offset = status.position - lead.position
        if lead.state == STATE_PLAYING:
            # The leader has played on since it was read.
            offset -= status.sampled_at - lead.sampled_at
        error = offset - follower.target

        if lead.state == STATE_PAUSED:
            if abs(error) > PAUSED_SLACK:
                self._set_status(name, CORRECTING)
                if memory.jumped_in == self._epoch:
                    # Same as a playing jump: the last seek did not get it
                    # there and the leader has not moved since.
                    self._failed(name, memory)
                memory.jumped_in = self._epoch
                await player.seek(-error)
            else:
                memory.jumped_in = None
                self._set_status(name, IN_SYNC)
            return

        if abs(error) > JUMP:
            self._set_status(name, CORRECTING)
            memory.out_of_line = 0
            if memory.jumped_in == self._epoch:
                # The last seek did not get it there and the leader has not
                # moved since, so that seek failed.
                self._failed(name, memory)
            memory.jumped_in = self._epoch
            memory.tighten = True
            await player.seek(-error)
            return
        memory.jumped_in = None

        if not memory.tighten:
            if abs(error) <= self._tolerance:
                memory.out_of_line = 0
                memory.failures = 0
                self._set_status(name, IN_SYNC)
                return
            memory.out_of_line += 1
            if memory.out_of_line < CONFIRM:
                return

        # A coarse correction lands within a second, not within the tolerance,
        # so it is always followed by one fine-tune. A fine-tune that finds
        # the follower already close sends nothing.
        self._set_status(name, CORRECTING)
        memory.out_of_line = 0
        memory.tighten = False
        epoch = self._epoch
        try:
            result = await sync_follower(
                self._guarded_leader,
                player,
                clock=self._clock,
                tolerance=FINE_TOLERANCE,
                target=follower.target,
                start_timeout=START_TIMEOUT,
            )
        except LeaderError:
            return  # the leader left the recording; the next pass sorts it out
        if result.status == SYNCED:
            memory.failures = 0
            self._set_status(name, IN_SYNC)
        elif epoch == self._epoch:
            self._failed(name, memory)

    # -- bookkeeping ----------------------------------------------------------

    def _failed(self, name: str, memory: _Memory) -> None:
        """Count a failed correction; rest the follower after too many in a row."""
        memory.failures += 1
        if memory.failures >= REST_AFTER:
            self._rest(name, memory)

    def _rest(self, name: str, memory: _Memory, message: str = WILL_NOT_SETTLE) -> None:
        memory.failures = 0
        memory.out_of_line = 0
        memory.jumped_in = None
        memory.tighten = False
        memory.rest_until = self._clock.time() + REST_FOR
        self._set_status(name, RESTING)
        self._problem(name, message)

    def _set_status(self, name: str, status: str) -> None:
        if self.statuses[name] == status:
            return
        self.statuses[name] = status
        if self._on_status is not None:
            try:
                self._on_status(name, status)
            except Exception:
                _LOGGER.exception("The status callback failed")

    def _problem(self, follower: str | None, message: str) -> None:
        if self._on_problem is not None:
            try:
                self._on_problem(follower, message)
            except Exception:
                _LOGGER.exception("The problem callback failed")
