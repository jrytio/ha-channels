"""A simulated Channels app and DVR server, driven by a fake clock.

The player models what was measured on tvOS on 2026-10-01 and 2026-10-02:
seeks that land short, a pause that costs a little extra, a live edge it
cannot reach, a play command that is dropped at the end of a finished
recording, and a load time after a play during which seeks are discarded.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
import heapq
from itertools import count, cycle
from typing import Any

from custom_components.channels.lib.models import (
    STATE_PAUSED,
    STATE_PLAYING,
    STATE_STOPPED,
    AppStatus,
    ChannelsConnectionError,
    FailedJob,
    Recording,
)

EDGE_GAP = 1.8  # playback cannot sit closer than this to the live edge


class FakeClock:
    """A clock that jumps forward instead of waiting."""

    def __init__(self, start: float = 1_790_000_000.0) -> None:
        self.now = start

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += max(0.0, seconds)


class ConcurrentClock(FakeClock):
    """A fake clock for code that runs several tasks at once.

    `FakeClock.sleep` jumps the clock forward, which is only right while one
    task is running. Here `sleep` parks the caller until the clock reaches
    its wake time, and `run_for` drives everything: whenever no task can
    run, it jumps to the earliest wake time and wakes that sleeper.
    """

    def __init__(self, start: float = 1_790_000_000.0) -> None:
        super().__init__(start)
        self._sleepers: list[tuple[float, int, asyncio.Future[None]]] = []
        self._order = count()

    async def sleep(self, seconds: float) -> None:
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        wake = self.now + max(0.0, seconds)
        heapq.heappush(self._sleepers, (wake, next(self._order), future))
        await future

    async def _settle(self) -> None:
        """Let every task that can run do so, until all of them are parked."""
        loop = asyncio.get_running_loop()
        await asyncio.sleep(0)
        # The loop's queue of callbacks ready to run. Private, but it is the
        # one exact answer to "is anything still runnable?".
        while loop._ready:  # noqa: ASYNC110  there is no event to wait on
            await asyncio.sleep(0)

    async def run_for(self, seconds: float) -> None:
        """Advance the clock by `seconds`, running tasks as their sleeps end."""
        end = self.now + seconds
        await self._settle()
        while self._sleepers and self._sleepers[0][0] <= end:
            wake, _, future = heapq.heappop(self._sleepers)
            if future.done():  # its task was cancelled while asleep
                continue
            self.now = max(self.now, wake)
            future.set_result(None)
            await self._settle()
        self.now = end


class SimDvr:
    """A DVR server holding recordings, jobs and failed jobs."""

    def __init__(
        self,
        clock: FakeClock,
        *,
        tuner_free: bool = True,
        clock_offset: float = 0.0,
        latency: float = 0.0,
    ) -> None:
        self.clock = clock
        self.latency = latency  # how long each request takes to answer
        self.clock_offset = clock_offset  # how far the DVR's clock runs ahead
        self.tuner_free = tuner_free
        self.recordings: dict[str, Recording] = {}
        self.failed: list[FailedJob] = []
        # Jobs as GET /api/v1/jobs lists them; times in seconds, updated_at ms.
        self.jobs: list[dict[str, Any]] = []
        self._job_of: dict[str, dict[str, Any]] = {}  # recording id -> its job
        self._next_id = 15000

    def now(self) -> float:
        """The DVR's own idea of the time."""
        return self.clock.time() + self.clock_offset

    def add(self, channel: str, *, age: float, completed: bool = False) -> Recording:
        """Add a recording that started `age` seconds ago."""
        self._next_id += 1
        recording = Recording(
            id=str(self._next_id),
            channel=channel,
            completed=completed,
            created_at=self.now() - age,
        )
        self.recordings[recording.id] = recording
        return recording

    def add_job(
        self,
        channel: str,
        *,
        starts_in: float = -600.0,
        lasts: float = 3600.0,
        failed: bool = False,
        skipped: bool = False,
        channel_key: bool = False,
    ) -> dict[str, Any]:
        """Add a job whose programme window starts `starts_in` seconds from now.

        Scheduled jobs list their channel only in `channels`; a manual one also
        has `channel`, which `channel_key` adds.
        """
        start = int(self.now() + starts_in)
        job: dict[str, Any] = {
            "id": f"{start}-ch{channel}",
            "name": "Programme",
            "start_time": start,
            "end_time": int(start + lasts),
            "duration": int(lasts),
            "channels": [channel],
            "skipped": skipped,
            "failed": failed,
            "updated_at": int(self.now() * 1000),
        }
        if failed:
            job["error"] = "could not start stream: no tuner available"
        if channel_key:
            job["channel"] = channel
        self.jobs.append(job)
        return job

    def record(self, channel: str) -> None:
        """Handle a record toggle from an app: stop a running recording, else start."""
        running = [
            r
            for r in self.recordings.values()
            if r.channel == channel and not r.completed
        ]
        if running:
            for r in running:
                self.recordings[r.id] = replace(r, completed=True)
                # A stopped recording's job leaves the list.
                job = self._job_of.pop(r.id, None)
                if job in self.jobs:
                    self.jobs.remove(job)
        elif self.tuner_free:
            recording = self.add(channel, age=0.0)
            # A manual recording's job covers the programme now on air.
            self._job_of[recording.id] = self.add_job(
                channel, starts_in=-900.0, lasts=1800.0, channel_key=True
            )
        else:
            self.failed.append(
                FailedJob(
                    name="Programme",
                    channel=channel,
                    error="could not start stream: no tuner available",
                    updated_at=self.now(),
                )
            )

    async def _respond(self) -> None:
        # Only when asked for: a zero-length sleep still parks a task on the
        # concurrent clock, which would change every other test.
        if self.latency:
            await self.clock.sleep(self.latency)

    async def in_progress_recording(self, channel: str) -> Recording | None:
        await self._respond()
        live = [
            r
            for r in self.recordings.values()
            if r.channel == channel and not r.completed
        ]
        return max(live, key=lambda r: r.created_at, default=None)

    @staticmethod
    def _on_channel(job: dict[str, Any], channel: str) -> bool:
        return job.get("channel") == channel or channel in job.get("channels", [])

    async def has_active_job(self, channel: str, now: float) -> bool:
        await self._respond()
        return any(
            not job["failed"]
            and not job["skipped"]
            and job["start_time"] <= now < job["end_time"]
            and self._on_channel(job, channel)
            for job in self.jobs
        )

    async def recent_failed_job(self, channel: str, since: float) -> FailedJob | None:
        await self._respond()
        failed = [j for j in self.failed if j.channel == channel] + [
            FailedJob.from_dict(job)
            for job in self.jobs
            if job["failed"] and self._on_channel(job, channel)
        ]
        jobs = [j for j in failed if j.updated_at >= since]
        return max(jobs, key=lambda j: j.updated_at, default=None)


class SimPlayer:
    """A Channels app."""

    def __init__(
        self,
        clock: FakeClock,
        *,
        dvr: SimDvr | None = None,
        latency: float = 0.004,
        position_noise: tuple[float, ...] = (0.0,),
        pause_overhead: float = 0.06,
        seek_losses: tuple[float, ...] = (0.0,),
        resume_position: float = 0.0,
        starts: bool = True,
        reachable_at: float | None = None,
        load_time: float = 1.5,
    ) -> None:
        self.clock = clock
        self.dvr = dvr
        self.latency = latency
        self._noise = cycle(position_noise)
        self.pause_overhead = pause_overhead
        self.resume_position = resume_position
        self.starts = starts
        self.reachable_at = reachable_at
        self.load_time = load_time
        self.state = STATE_STOPPED
        self.recording_id: str | None = None
        self.channel: str | None = None
        self.calls: list[str] = []
        self.log: list[tuple[float, str]] = []  # (time sent, call)
        self.played_at: float | None = None  # when a play last took effect
        self._losses = cycle(seek_losses)
        self._base = 0.0
        self._since = clock.time()
        # Loading after a play: until `_loading_until` the position is frozen
        # and seeks only change what is reported; then it snaps back to
        # `_load_position` and plays from there.
        self._loading_until: float | None = None
        self._load_position = 0.0
        self._at_end = False  # paused at 0 after a recording played to its end
        self._drop_plays = 0
        self.in_front = True  # False while another app is in front on the TV

    # -- test setup helpers -------------------------------------------------

    def watch_recording(self, recording_id: str, position: float) -> None:
        self.recording_id, self.channel = recording_id, None
        self.state = STATE_PLAYING
        self._base, self._since = position, self.clock.time()
        self._loading_until, self._at_end = None, False

    def watch_live(self, channel: str) -> None:
        self.recording_id, self.channel = None, channel
        self.state = STATE_PLAYING
        self._loading_until, self._at_end = None, False

    def finish_recording(self, recording_id: str) -> None:
        """Leave the app as a recording that played to its end leaves it.

        It reports paused at 0 on that recording, and drops the next play.
        """
        self.recording_id, self.channel = recording_id, None
        self.state = STATE_PAUSED
        self._base, self._since = 0.0, self.clock.time()
        self._loading_until, self._at_end = None, True

    def drop_next_play(self, count: int = 1) -> None:
        """Make the next `count` play commands have no effect."""
        self._drop_plays += count

    # -- what a person does with the TV's own remote ---------------------------
    # None of these is recorded in `calls`: those are the engine's commands.

    def user_pause(self) -> None:
        if self.state == STATE_PLAYING:
            self._base, self.state = self.position(), STATE_PAUSED

    def user_resume(self) -> None:
        if self.state == STATE_PAUSED:
            self.state, self._since = STATE_PLAYING, self.clock.time()

    def user_seek(self, seconds: float) -> None:
        self._move_to(self.position() + seconds)

    def user_stop(self) -> None:
        self.recording_id, self.channel, self.state = None, None, STATE_STOPPED

    def leave_app(self) -> None:
        """Put another app in front: playback pauses and the API stops answering."""
        self.user_pause()
        self.in_front = False

    def return_to_app(self) -> None:
        self.in_front = True

    def position(self) -> float | None:
        """Return the true position right now (what is reported, while loading)."""
        self._finish_loading()
        if self.recording_id is None:
            return None
        position = self._base
        if self.state == STATE_PLAYING and self._loading_until is None:
            position += max(0.0, self.clock.time() - self._since)
        return self._clamp(position)

    # -- internals ----------------------------------------------------------

    def _clamp(self, position: float) -> float:
        recording = self.dvr.recordings.get(self.recording_id) if self.dvr else None
        if recording is not None and not recording.completed:
            now = self.dvr.now() if self.dvr else self.clock.time()
            edge = now - recording.created_at - EDGE_GAP
            position = min(position, edge)
        return max(0.0, position)

    def _move_to(self, position: float) -> None:
        self._base, self._since = self._clamp(position), self.clock.time()

    def _finish_loading(self) -> None:
        """Once loading is over, discard any seek made during it."""
        if self._loading_until is None or self.clock.time() < self._loading_until:
            return
        self._base, self._since = self._load_position, self._loading_until
        self._loading_until = None

    async def _reply(
        self, call: str | None = None, *, report_noise: bool = False
    ) -> AppStatus:
        if call:
            self.calls.append(call)
            self.log.append((self.clock.time(), call))
        await self.clock.sleep(self.latency)
        if not self.in_front or (
            self.reachable_at is not None and self.clock.time() < self.reachable_at
        ):
            raise ChannelsConnectionError("the app is not in the foreground")
        position = self.position()
        if position is not None and report_noise:
            # Distorts only what is reported; true playback is untouched.
            position += next(self._noise)
        return AppStatus(
            state=self.state,
            muted=False,
            sampled_at=self.clock.time() - self.latency / 2,
            position=position,
            recording_id=self.recording_id,
            channel_number=self.channel,
        )

    # -- the AppClient surface the engine uses --------------------------------

    async def status(self) -> AppStatus:
        return await self._reply(report_noise=True)

    async def play_recording(self, recording_id: str) -> AppStatus:
        call = f"play_recording {recording_id}"
        if self._at_end:
            # The reply shows the old state; then the app stops, on nothing.
            reply = await self._reply(call)
            self.recording_id, self.state, self._at_end = None, STATE_STOPPED, False
            return reply
        if self._drop_plays:
            self._drop_plays -= 1
            return await self._reply(call)
        if self.starts:
            self.recording_id, self.channel = recording_id, None
            self.state = STATE_PLAYING
            self._move_to(self.resume_position)
            self._load_position = self._base
            self._loading_until = self.clock.time() + self.load_time
            self.played_at = self.clock.time()
        return await self._reply(call)

    async def seek(self, seconds: float) -> AppStatus:
        # From the end-of-recording state a seek is assumed to do nothing. That
        # is unmeasured on the real app; it is the conservative assumption.
        if self.recording_id is not None and not self._at_end:
            # While loading this moves only what is reported; see _finish_loading.
            loading_until = self._loading_until
            self._move_to(self.position() + seconds - next(self._losses))
            self._loading_until = loading_until
        return await self._reply(f"seek {seconds:.3f}")

    async def pause(self) -> AppStatus:
        if self.state == STATE_PLAYING:
            self._base, self.state = self.position(), STATE_PAUSED
        return await self._reply("pause")

    async def resume(self) -> AppStatus:
        if self.state == STATE_PAUSED:
            self.state = STATE_PLAYING
            if self._at_end:
                # Measured: it reports playing, but the position never leaves 0.
                self._base, self._since = 0.0, float("inf")
                return await self._reply("resume")
            # Playback restarts a moment after the command arrives.
            self._since = self.clock.time() + self.pause_overhead
        return await self._reply("resume")

    async def toggle_record(self) -> AppStatus:
        if self.dvr is not None and self.channel is not None:
            self.dvr.record(self.channel)
        return await self._reply("toggle_record")
