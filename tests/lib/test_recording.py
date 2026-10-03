"""Tests for moving an app from live TV onto a recording."""

import pytest

from custom_components.channels.lib.models import ChannelsConnectionError, FailedJob
from custom_components.channels.lib.recording import SwitchError, switch_to_recording

from .sim import FakeClock, SimDvr, SimPlayer

CHANNEL = "6.1"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def dvr(clock: FakeClock) -> SimDvr:
    return SimDvr(clock)


@pytest.fixture
def app(clock: FakeClock, dvr: SimDvr) -> SimPlayer:
    player = SimPlayer(clock, dvr=dvr)
    player.watch_live(CHANNEL)
    return player


def behind_live(clock: FakeClock, dvr: SimDvr, app: SimPlayer) -> float:
    recording = dvr.recordings[app.recording_id]
    return (clock.time() - recording.created_at) - app.position()


async def test_app_already_on_a_recording_is_left_alone(clock, dvr):
    app = SimPlayer(clock, dvr=dvr)
    app.watch_recording("15017", 100.0)

    result = await switch_to_recording(app, dvr, clock=clock)

    assert (result.recording_id, result.switched, result.started_recording) == (
        "15017",
        False,
        False,
    )
    assert app.calls == []


async def test_existing_recording_is_used_and_never_toggled(clock, dvr, app):
    recording = dvr.add(CHANNEL, age=600.0)

    result = await switch_to_recording(app, dvr, clock=clock)

    assert result.recording_id == recording.id
    assert result.switched is True
    assert result.started_recording is False
    assert "toggle_record" not in app.calls
    assert behind_live(clock, dvr, app) == pytest.approx(5.0, abs=0.5)
    assert dvr.recordings[recording.id].completed is False


async def test_existing_recording_younger_than_the_buffer_is_waited_for(
    clock, dvr, app
):
    dvr.add(CHANNEL, age=2.0)

    result = await switch_to_recording(app, dvr, clock=clock)

    assert result.switched is True
    assert "toggle_record" not in app.calls
    assert behind_live(clock, dvr, app) == pytest.approx(5.0, abs=0.5)


async def test_recording_is_started_when_there_is_none(clock, dvr, app):
    result = await switch_to_recording(app, dvr, clock=clock)

    assert result.switched is True
    assert result.started_recording is True
    assert app.calls.count("toggle_record") == 1
    assert app.recording_id == result.recording_id
    assert behind_live(clock, dvr, app) == pytest.approx(5.0, abs=0.5)
    assert "seek -2.000" not in app.calls


async def test_a_dropped_play_is_resent(clock, dvr, app):
    recording = dvr.add(CHANNEL, age=600.0)
    app.drop_next_play()

    result = await switch_to_recording(app, dvr, clock=clock)

    assert result.switched is True
    assert app.calls.count(f"play_recording {recording.id}") == 2
    assert app.recording_id == recording.id
    assert behind_live(clock, dvr, app) == pytest.approx(5.0, abs=0.5)


async def test_resume_point_well_behind_live_lands_behind_live(clock, dvr):
    # On a real TV this landed 8.5 s behind live: the seek was sent while the
    # app was still loading and was discarded.
    app = SimPlayer(clock, dvr=dvr, resume_position=550.0)
    app.watch_live(CHANNEL)
    dvr.add(CHANNEL, age=600.0)

    result = await switch_to_recording(app, dvr, clock=clock)

    assert result.switched is True
    assert behind_live(clock, dvr, app) == pytest.approx(5.0, abs=0.5)
    assert "seek -2.000" not in app.calls


async def test_a_seek_that_does_not_land_is_sent_again(clock, dvr, app):
    dvr.add(CHANNEL, age=600.0)
    original_seek = app.seek
    seeks = 0

    async def seek(seconds):
        nonlocal seeks
        seeks += 1
        if seeks == 1:
            return await original_seek(0.0)  # acknowledged, but lost
        return await original_seek(seconds)

    app.seek = seek

    result = await switch_to_recording(app, dvr, clock=clock)

    assert result.switched is True
    assert seeks == 2
    assert behind_live(clock, dvr, app) == pytest.approx(5.0, abs=0.5)


async def test_a_recording_on_another_channel_is_not_used(clock, dvr, app):
    other = dvr.add("3.1", age=600.0)

    result = await switch_to_recording(app, dvr, clock=clock)

    assert result.recording_id != other.id
    assert result.started_recording is True


async def test_no_tuner_reports_the_dvr_error_and_leaves_live_tv_on(clock, dvr, app):
    dvr.tuner_free = False

    with pytest.raises(SwitchError, match="no tuner available"):
        await switch_to_recording(app, dvr, clock=clock)

    assert app.recording_id is None
    assert app.channel == CHANNEL


async def test_no_tuner_fails_fast(clock, dvr, app):
    dvr.tuner_free = False
    began = clock.time()

    with pytest.raises(SwitchError, match="no tuner available") as raised:
        await switch_to_recording(app, dvr, clock=clock)

    assert clock.time() - began < 2.0
    assert raised.value.started_recording is False


async def test_recording_that_never_appears_says_one_was_requested(clock, dvr, app):
    dvr.record = lambda channel: None  # the request vanishes

    with pytest.raises(SwitchError, match="has not appeared") as raised:
        await switch_to_recording(app, dvr, clock=clock)

    assert raised.value.started_recording is True


async def test_failure_after_the_toggle_says_a_recording_was_started(clock, dvr):
    app = SimPlayer(clock, dvr=dvr, starts=False)
    app.watch_live(CHANNEL)

    with pytest.raises(SwitchError) as raised:
        await switch_to_recording(app, dvr, clock=clock)

    assert raised.value.started_recording is True
    assert str(raised.value).startswith("A recording was started, but ")


async def test_connection_error_after_the_toggle_is_reported_with_the_recording(
    clock, dvr, app
):
    original = dvr.in_progress_recording
    calls = 0

    async def in_progress(channel):
        nonlocal calls
        calls += 1
        if calls > 1:  # the first call is the check before the toggle
            raise ChannelsConnectionError("the DVR is unreachable")
        return await original(channel)

    dvr.in_progress_recording = in_progress

    with pytest.raises(SwitchError) as raised:
        await switch_to_recording(app, dvr, clock=clock)

    assert raised.value.started_recording is True
    assert str(raised.value).startswith("A recording was started, but ")
    assert isinstance(raised.value.__cause__, ChannelsConnectionError)


async def test_channel_change_before_the_toggle_sends_no_toggle(clock, dvr, app):
    original = dvr.in_progress_recording

    async def in_progress(channel):
        result = await original(channel)
        app.channel = "3.1"  # the viewer changes channel during the DVR check
        return result

    dvr.in_progress_recording = in_progress

    with pytest.raises(SwitchError, match="channel changed") as raised:
        await switch_to_recording(app, dvr, clock=clock)

    assert raised.value.started_recording is False
    assert "toggle_record" not in app.calls


def file_appears_after(dvr: SimDvr, clock: FakeClock, seconds: float) -> None:
    """List a recording on CHANNEL once `seconds` have passed from now."""
    listed = dvr.in_progress_recording
    appears_at = clock.time() + seconds

    async def in_progress(channel):
        if clock.time() >= appears_at and not dvr.recordings:
            dvr.add(CHANNEL, age=1.0)
        return await listed(channel)

    dvr.in_progress_recording = in_progress


async def test_active_job_with_no_file_yet_is_waited_for_not_toggled(clock, dvr, app):
    dvr.add_job(CHANNEL, channel_key=True)
    file_appears_after(dvr, clock, 3.0)

    result = await switch_to_recording(app, dvr, clock=clock)

    assert result.switched is True
    assert result.started_recording is False
    assert "toggle_record" not in app.calls
    assert app.recording_id == result.recording_id
    assert behind_live(clock, dvr, app) == pytest.approx(5.0, abs=0.5)


async def test_active_job_whose_file_never_appears_is_an_error_without_a_toggle(
    clock, dvr, app
):
    dvr.add_job(CHANNEL, channel_key=True)
    began = clock.time()

    with pytest.raises(
        SwitchError,
        match="^The DVR is recording this channel but the recording has not appeared$",
    ) as raised:
        await switch_to_recording(app, dvr, clock=clock)

    assert raised.value.started_recording is False
    assert "toggle_record" not in app.calls
    assert clock.time() - began <= 10.0 + 1.0
    assert app.channel == CHANNEL


async def test_active_job_listing_the_channel_only_in_channels_is_recognised(
    clock, dvr, app
):
    dvr.add_job(CHANNEL)  # scheduled: no `channel` key

    with pytest.raises(SwitchError, match="DVR is recording this channel"):
        await switch_to_recording(app, dvr, clock=clock)

    assert "toggle_record" not in app.calls


@pytest.mark.parametrize(
    "job",
    [
        {"starts_in": 60.0},  # later today
        {"starts_in": -3600.0, "lasts": 1800.0},  # already over
        {"failed": True},
        {"skipped": True},
    ],
    ids=["later", "over", "failed", "skipped"],
)
async def test_jobs_that_are_not_running_now_do_not_block_a_recording(
    clock, dvr, app, job
):
    dvr.add_job(CHANNEL, **job)

    result = await switch_to_recording(app, dvr, clock=clock)

    assert result.started_recording is True
    assert result.switched is True
    assert app.calls.count("toggle_record") == 1


async def test_a_recording_this_dvr_started_lists_an_active_job_until_stopped(
    clock, dvr, app
):
    dvr.record(CHANNEL)
    assert await dvr.has_active_job(CHANNEL, clock.time()) is True

    dvr.record(CHANNEL)
    assert await dvr.has_active_job(CHANNEL, clock.time()) is False


async def test_stopped_app_is_an_error(clock, dvr):
    with pytest.raises(SwitchError, match="Nothing is playing"):
        await switch_to_recording(SimPlayer(clock, dvr=dvr), dvr, clock=clock)


async def test_app_that_never_plays_the_recording(clock, dvr):
    app = SimPlayer(clock, dvr=dvr, starts=False)
    app.watch_live(CHANNEL)
    dvr.add(CHANNEL, age=600.0)

    with pytest.raises(SwitchError, match="did not start playing"):
        await switch_to_recording(app, dvr, clock=clock)


async def test_behind_live_below_the_minimum_is_raised_to_it(clock, dvr, app):
    dvr.add(CHANNEL, age=600.0)

    await switch_to_recording(app, dvr, clock=clock, behind_live=0.5)

    assert behind_live(clock, dvr, app) == pytest.approx(3.0, abs=0.5)


async def test_dvr_resume_point_does_not_change_where_it_lands(clock, dvr):
    app = SimPlayer(clock, dvr=dvr, resume_position=200.0)
    app.watch_live(CHANNEL)
    dvr.add(CHANNEL, age=600.0)

    await switch_to_recording(app, dvr, clock=clock)

    assert behind_live(clock, dvr, app) == pytest.approx(5.0, abs=0.5)


async def test_stall_after_the_seek_is_recovered_by_backing_off(clock, dvr, app):
    dvr.add(CHANNEL, age=600.0)
    original_seek = app.seek

    async def seek(seconds):
        status = await original_seek(seconds)
        if seconds > 0:
            app._since = clock.time() + 1000  # frozen until the next seek
        return status

    app.seek = seek

    result = await switch_to_recording(app, dvr, clock=clock)

    assert result.switched is True
    assert "seek -2.000" in app.calls


async def test_playback_that_never_moves_is_an_error(clock, dvr, app):
    dvr.add(CHANNEL, age=600.0)
    original_seek = app.seek

    async def seek(seconds):
        status = await original_seek(seconds)
        app._since = clock.time() + 1000
        return status

    app.seek = seek

    with pytest.raises(SwitchError, match="stalled"):
        await switch_to_recording(app, dvr, clock=clock)


async def test_last_back_off_is_checked_before_giving_up(clock, dvr, app):
    dvr.add(CHANNEL, age=600.0)
    original_seek = app.seek
    back_offs = 0

    async def seek(seconds):
        nonlocal back_offs
        status = await original_seek(seconds)
        if seconds < 0:
            back_offs += 1
        if back_offs < 3:
            app._since = clock.time() + 1000  # frozen until the third back-off
        return status

    app.seek = seek

    result = await switch_to_recording(app, dvr, clock=clock)

    assert result.switched is True
    assert back_offs == 3


async def test_stale_failed_job_is_not_mistaken_for_this_attempt(clock):
    dvr = SimDvr(clock, clock_offset=5.0)
    app = SimPlayer(clock, dvr=dvr)
    app.watch_live(CHANNEL)
    # An earlier attempt failed 2 s ago by the DVR's clock: ahead of ours.
    dvr.failed.append(
        FailedJob(
            name="Programme",
            channel=CHANNEL,
            error="could not start stream: no tuner available",
            updated_at=dvr.now() - 2.0,
        )
    )
    listed = dvr.in_progress_recording
    polls_after_toggle = 0

    async def slow_to_list(channel):
        nonlocal polls_after_toggle
        if "toggle_record" in app.calls:
            polls_after_toggle += 1
            if polls_after_toggle == 1:
                return None  # the new file is not listed yet
        return await listed(channel)

    dvr.in_progress_recording = slow_to_list

    result = await switch_to_recording(app, dvr, clock=clock)

    assert result.started_recording is True
    assert result.switched is True
    assert app.recording_id == result.recording_id


async def test_new_failure_is_still_reported_when_an_old_one_exists(clock):
    dvr = SimDvr(clock, tuner_free=False, clock_offset=5.0)
    app = SimPlayer(clock, dvr=dvr)
    app.watch_live(CHANNEL)
    dvr.failed.append(
        FailedJob(
            name="Programme",
            channel=CHANNEL,
            error="an old failure",
            updated_at=dvr.now() - 60.0,
        )
    )
    began = clock.time()

    with pytest.raises(SwitchError, match="no tuner available") as raised:
        await switch_to_recording(app, dvr, clock=clock)

    assert clock.time() - began < 2.0
    assert raised.value.started_recording is False


async def test_buffer_wait_is_capped_when_the_dvr_clock_is_ahead(clock):
    dvr = SimDvr(clock, clock_offset=30.0)
    app = SimPlayer(clock, dvr=dvr)
    app.watch_live(CHANNEL)
    dvr.add(CHANNEL, age=0.0)
    began = clock.time()

    result = await switch_to_recording(app, dvr, clock=clock)

    assert result.switched is True
    # behind_live (5 s) for the buffer, about 2 s until playback advances,
    # a second each for the landing and stall checks, and a few request
    # latencies; uncapped it would be 35 s.
    assert clock.time() - began < 5.0 + 5.0


async def test_two_apps_on_one_channel_switched_in_turn_share_one_recording(
    clock, dvr, app
):
    other = SimPlayer(clock, dvr=dvr)
    other.watch_live(CHANNEL)

    first = await switch_to_recording(app, dvr, clock=clock)
    second = await switch_to_recording(other, dvr, clock=clock)

    assert len(dvr.recordings) == 1
    (recording,) = dvr.recordings.values()
    assert recording.completed is False
    assert app.calls.count("toggle_record") + other.calls.count("toggle_record") == 1
    assert first.started_recording is True
    assert second.started_recording is False
    assert first.recording_id == second.recording_id == recording.id


async def test_channel_change_during_the_failed_job_lookup_sends_no_toggle(clock):
    dvr = SimDvr(clock, latency=0.1)
    app = SimPlayer(clock, dvr=dvr)
    app.watch_live(CHANNEL)
    elsewhere = dvr.add("3.1", age=600.0)  # a recording running on another channel
    looked_up = dvr.recent_failed_job

    async def recent_failed_job(channel, since):
        failed = await looked_up(channel, since)
        if "toggle_record" not in app.calls:
            app.channel = "3.1"  # the viewer changes channel while the DVR answers
        return failed

    dvr.recent_failed_job = recent_failed_job

    with pytest.raises(
        SwitchError,
        match="^The channel changed before a recording could be started$",
    ) as raised:
        await switch_to_recording(app, dvr, clock=clock)

    assert "toggle_record" not in app.calls
    assert dvr.recordings[elsewhere.id].completed is False
    assert raised.value.started_recording is False


class ChangesChannelAsItRecords(SimPlayer):
    """A player the viewer moves to another channel just as the toggle arrives."""

    async def toggle_record(self):
        self.channel = "3.1"
        return await super().toggle_record()


async def test_a_toggle_that_reached_another_channel_is_reported(clock, dvr):
    app = ChangesChannelAsItRecords(clock, dvr=dvr)
    app.watch_live(CHANNEL)

    with pytest.raises(SwitchError) as raised:
        await switch_to_recording(app, dvr, clock=clock)

    assert str(raised.value) == (
        "The channel changed as the recording was requested; "
        "the record command went to channel 3.1. Check the DVR"
    )
    assert raised.value.started_recording is True
    assert app.calls.count("toggle_record") == 1
