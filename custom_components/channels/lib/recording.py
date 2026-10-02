"""Move a Channels app from live TV onto the recording of what it is watching."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .clock import Clock
from .models import STATE_STOPPED, AppStatus, ChannelsError, FailedJob, Recording
from .playback import start_playback

MIN_BEHIND_LIVE = 3.0  # playback stalls closer than about 1.8 s to the live edge
RECORD_START_TIMEOUT = 10.0
PLAY_START_TIMEOUT = 15.0
POLL = 0.5
STALL_BACKOFF = 2.0
STALL_CHECKS = 3
LANDING_WAIT = 1.0  # a seek on a running recording has landed within 1 s
LANDING_TOLERANCE = 2.0  # a landed seek is within about 0.3 s; a lost one is not
LANDING_RESEEKS = 2  # a lost seek is sent again at most this many times


class SwitchError(ChannelsError):
    """The app could not be moved onto a recording.

    `started_recording` is True when a recording may be running because of this
    attempt, so the caller can tell the user and avoid toggling it off.
    """

    def __init__(self, message: str, *, started_recording: bool = False) -> None:
        super().__init__(message)
        self.started_recording = started_recording


class RecordingApp(Protocol):
    """The parts of AppClient this module uses."""

    async def status(self) -> AppStatus: ...
    async def play_recording(self, recording_id: str) -> AppStatus: ...
    async def seek(self, seconds: float) -> AppStatus: ...
    async def toggle_record(self) -> AppStatus: ...


class RecordingDvr(Protocol):
    """The parts of DvrClient this module uses."""

    async def in_progress_recording(self, channel: str) -> Recording | None: ...
    async def has_active_job(self, channel: str, now: float) -> bool: ...
    async def recent_failed_job(
        self, channel: str, since: float
    ) -> FailedJob | None: ...


@dataclass(frozen=True, slots=True)
class SwitchResult:
    """What switch_to_recording did."""

    recording_id: str
    switched: bool
    started_recording: bool


async def _start_recording(
    app: RecordingApp, dvr: RecordingDvr, channel: str, clock: Clock
) -> Recording:
    """Toggle recording on and wait for the file. Failures here are post-toggle."""
    # Whether a failed job is new is judged on the DVR's clock alone: remember
    # the latest one now and accept only a later one as this attempt's failure.
    try:
        previous = await dvr.recent_failed_job(channel, 0.0)
    except ChannelsError as err:
        raise SwitchError(f"The DVR could not be read: {err}") from err
    baseline = previous.updated_at if previous is not None else None

    asked_at = clock.time()
    try:
        await app.toggle_record()
        while clock.time() < asked_at + RECORD_START_TIMEOUT:
            await clock.sleep(POLL)
            recording = await dvr.in_progress_recording(channel)
            if recording is not None:
                return recording
            failed = await dvr.recent_failed_job(channel, 0.0)
            if failed is not None and (
                baseline is None or failed.updated_at > baseline
            ):
                raise SwitchError(f"The recording did not start: {failed.error}")
    except SwitchError:
        raise
    except ChannelsError as err:
        raise SwitchError(
            f"A recording was started, but {err}", started_recording=True
        ) from err
    raise SwitchError(
        "A recording was requested but has not appeared; it may still be starting",
        started_recording=True,
    )


async def _wait_for_file(dvr: RecordingDvr, channel: str, clock: Clock) -> Recording:
    """Wait for the file of a recording the DVR is already making."""
    deadline = clock.time() + RECORD_START_TIMEOUT
    while clock.time() < deadline:
        await clock.sleep(POLL)
        recording = await dvr.in_progress_recording(channel)
        if recording is not None:
            return recording
    raise SwitchError(
        "The DVR is recording this channel but the recording has not appeared"
    )


async def _seek_and_check_landing(
    app: RecordingApp, status: AppStatus, wanted: float, clock: Clock
) -> None:
    """Seek from `status` to `wanted`, sending it again if it did not land."""
    wanted_at = clock.time()
    await app.seek(wanted - status.position)
    for reseeks in range(LANDING_RESEEKS + 1):
        await clock.sleep(LANDING_WAIT)
        status = await app.status()
        target = wanted + (clock.time() - wanted_at)
        if (
            status.position is None
            or abs(status.position - target) <= LANDING_TOLERANCE
            or reseeks == LANDING_RESEEKS
        ):
            return
        await app.seek(target - status.position)


async def _play_and_settle(
    app: RecordingApp, recording: Recording, behind_live: float, clock: Clock
) -> None:
    """Play the recording, land `behind_live` behind its edge and check it moves."""
    # A file younger than the buffer cannot be played that far back yet. The
    # viewer stays on live TV while it fills.
    age = clock.time() - recording.created_at
    if age < behind_live:
        await clock.sleep(min(behind_live, behind_live - age))

    # Seek only once playback is really running: a seek sent while the app is
    # still loading is acknowledged and then discarded.
    status = await start_playback(app, recording.id, PLAY_START_TIMEOUT, clock)
    if status is None:
        raise SwitchError("The TV did not start playing the recording")

    # An in-progress file reports no duration, so the live edge is its age.
    wanted = max(0.0, (clock.time() - recording.created_at) - behind_live)
    if abs(wanted - status.position) > POLL:
        await _seek_and_check_landing(app, status, wanted, clock)

    # Near the live edge a seek can stall. Check, then back off and check again.
    for back_offs in range(STALL_CHECKS + 1):
        before = await app.status()
        await clock.sleep(1.0)
        after = await app.status()
        if (
            before.position is not None
            and after.position is not None
            and after.position - before.position >= 0.5
        ):
            return
        if back_offs < STALL_CHECKS:
            await app.seek(-STALL_BACKOFF)
    raise SwitchError("Playback stalled near the live edge of the recording")


async def switch_to_recording(
    app: RecordingApp,
    dvr: RecordingDvr,
    *,
    clock: Clock,
    behind_live: float = 5.0,
) -> SwitchResult:
    """Put an app that is on live TV onto the recording of that programme.

    Uses the recording already in progress on that channel, or starts one.
    Lands `behind_live` seconds behind the live edge. Does nothing when the
    app is already playing a recording.
    """
    behind_live = max(behind_live, MIN_BEHIND_LIVE)
    status = await app.status()
    if status.state == STATE_STOPPED:
        raise SwitchError("Nothing is playing in Channels on this TV")
    if status.recording_id is not None:
        return SwitchResult(status.recording_id, False, False)
    if status.channel_number is None:
        raise SwitchError("Channels is not on a live channel or a recording")

    channel = status.channel_number
    recording = await dvr.in_progress_recording(channel)
    started_recording = False
    if recording is None and await dvr.has_active_job(channel, clock.time()):
        # The DVR is recording but its file is not listed yet. A toggle now
        # could stop that recording, so wait for the file instead.
        recording = await _wait_for_file(dvr, channel, clock)
    if recording is None:
        # Only now is toggle_record safe: with a recording already running it
        # would stop it. Make sure the app is still on the channel we checked.
        started_recording = True
        if (await app.status()).channel_number != channel:
            raise SwitchError("The channel changed before a recording could be started")
        recording = await _start_recording(app, dvr, channel, clock)

    try:
        await _play_and_settle(app, recording, behind_live, clock)
    except SwitchError as err:
        if not started_recording or err.started_recording:
            raise
        reason = str(err)
        raise SwitchError(
            f"A recording was started, but {reason[:1].lower()}{reason[1:]}",
            started_recording=True,
        ) from err
    except ChannelsError as err:
        if not started_recording:
            raise
        raise SwitchError(
            f"A recording was started, but {err}", started_recording=True
        ) from err
    return SwitchResult(recording.id, True, started_recording)
