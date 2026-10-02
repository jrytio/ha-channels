"""Tests for the sync engine, against the simulated player."""

import pytest

from custom_components.channels.lib.models import STATE_PAUSED, STATE_PLAYING
from custom_components.channels.lib.sync import (
    OUT_OF_TOLERANCE,
    SKIPPED,
    START_TIMEOUT,
    SYNCED,
    LeaderError,
    measure_offset,
    sync_follower,
    wait_until_reachable,
)

from .sim import FakeClock, SimDvr, SimPlayer

REC = "15017"


def true_offset(leader: SimPlayer, follower: SimPlayer) -> float:
    """Seconds the follower is really ahead of the leader, at this instant."""
    return follower.position() - leader.position()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def leader(clock: FakeClock) -> SimPlayer:
    player = SimPlayer(clock)
    player.watch_recording(REC, 500.0)
    return player


async def test_follower_on_nothing_is_started_and_lined_up(clock, leader):
    follower = SimPlayer(clock)

    result = await sync_follower(leader, follower, clock=clock)

    assert result.status == SYNCED
    assert follower.recording_id == REC
    assert follower.state == STATE_PLAYING
    assert abs(true_offset(leader, follower)) <= 0.06
    assert abs(result.offset_ms) <= 50


@pytest.mark.parametrize("start_offset", [45.0, -30.0, 0.4, -0.4, 2.0])
async def test_follower_already_on_the_recording(clock, leader, start_offset):
    follower = SimPlayer(clock)
    follower.watch_recording(REC, 500.0 + start_offset)

    result = await sync_follower(leader, follower, clock=clock)

    assert result.status == SYNCED
    assert abs(true_offset(leader, follower)) <= 0.06
    assert "play_recording" not in " ".join(follower.calls)


async def test_follower_within_tolerance_is_left_alone(clock, leader):
    follower = SimPlayer(clock)
    follower.watch_recording(REC, 500.02)

    result = await sync_follower(leader, follower, clock=clock)

    assert result.status == SYNCED
    assert result.rounds == 0
    assert follower.calls == []


async def test_seeks_that_land_short_still_converge(clock, leader):
    follower = SimPlayer(clock, seek_losses=(0.18, 0.75, 0.38, 0.6, 0.08))
    follower.watch_recording(REC, 400.0)

    result = await sync_follower(leader, follower, clock=clock)

    assert result.status == SYNCED
    assert abs(true_offset(leader, follower)) <= 0.06


async def test_resume_point_from_the_dvr_does_not_matter(clock, leader):
    follower = SimPlayer(clock, resume_position=319.0)

    result = await sync_follower(leader, follower, clock=clock)

    assert result.status == SYNCED
    assert abs(true_offset(leader, follower)) <= 0.06


async def test_paused_leader_leaves_the_follower_paused_at_its_position(clock):
    leader = SimPlayer(clock)
    leader.watch_recording(REC, 500.0)
    await leader.pause()
    follower = SimPlayer(clock)

    result = await sync_follower(leader, follower, clock=clock)

    assert result.status == SYNCED
    assert follower.state == STATE_PAUSED
    assert abs(true_offset(leader, follower)) <= 0.05


async def test_paused_follower_is_resumed_for_a_playing_leader(clock, leader):
    follower = SimPlayer(clock)
    follower.watch_recording(REC, 480.0)
    await follower.pause()

    result = await sync_follower(leader, follower, clock=clock)

    assert result.status == SYNCED
    assert follower.state == STATE_PLAYING


async def test_target_offset_holds_the_follower_behind(clock, leader):
    follower = SimPlayer(clock)

    result = await sync_follower(leader, follower, clock=clock, target=-0.08)

    assert result.status == SYNCED
    assert true_offset(leader, follower) == pytest.approx(-0.08, abs=0.06)


async def test_follower_that_never_starts_is_skipped(clock, leader):
    follower = SimPlayer(clock, starts=False)

    result = await sync_follower(leader, follower, clock=clock)

    assert result.status == SKIPPED
    assert result.reason == "It did not start the recording"


async def test_follower_at_the_end_of_a_finished_recording_is_started(clock, leader):
    follower = SimPlayer(clock)
    follower.finish_recording("15016")  # watched to the end the day before

    result = await sync_follower(leader, follower, clock=clock)

    assert result.status == SYNCED
    assert follower.calls.count(f"play_recording {REC}") == 2
    assert follower.recording_id == REC
    assert abs(true_offset(leader, follower)) <= 0.06


async def test_follower_at_the_end_of_the_leaders_own_recording_is_restarted(
    clock, leader
):
    follower = SimPlayer(clock)
    follower.finish_recording(REC)  # the leader is replaying what it just finished

    result = await sync_follower(leader, follower, clock=clock)

    assert result.status == SYNCED
    assert abs(true_offset(leader, follower)) <= 0.06
    assert f"play_recording {REC}" in follower.calls


async def test_play_is_not_resent_while_the_app_is_still_loading(clock, leader):
    follower = SimPlayer(clock, load_time=4.0)

    result = await sync_follower(leader, follower, clock=clock)

    assert result.status == SYNCED
    assert follower.calls.count(f"play_recording {REC}") == 1


async def test_follower_that_drops_every_play_is_skipped_within_the_limit(
    clock, leader
):
    follower = SimPlayer(clock, starts=False)
    began = clock.time()

    result = await sync_follower(leader, follower, clock=clock)

    assert result.status == SKIPPED
    assert result.reason == "It did not start the recording"
    assert clock.time() - began <= START_TIMEOUT + 1.0
    assert follower.calls.count(f"play_recording {REC}") > 1


async def test_first_correction_waits_until_the_follower_has_loaded(clock, leader):
    follower = SimPlayer(clock, resume_position=100.0)

    result = await sync_follower(leader, follower, clock=clock)

    assert result.status == SYNCED
    seeks = [at for at, call in follower.log if call.startswith("seek")]
    assert seeks
    assert min(seeks) >= follower.played_at + follower.load_time


async def test_stopped_leader_is_an_error(clock):
    with pytest.raises(LeaderError, match="not playing"):
        await sync_follower(SimPlayer(clock), SimPlayer(clock), clock=clock)


async def test_leader_on_live_tv_is_an_error(clock):
    leader = SimPlayer(clock)
    leader.watch_live("6.1")

    with pytest.raises(LeaderError, match="live TV"):
        await sync_follower(leader, SimPlayer(clock), clock=clock)


async def test_leader_stopping_mid_sync_is_an_error_not_a_hang(clock, leader):
    follower = SimPlayer(clock)
    follower.watch_recording(REC, 300.0)
    original_seek = follower.seek

    async def seek_then_leader_stops(seconds):
        leader.state, leader.recording_id = "stopped", None
        return await original_seek(seconds)

    follower.seek = seek_then_leader_stops

    with pytest.raises(LeaderError):
        await sync_follower(leader, follower, clock=clock)


async def test_leader_pausing_mid_sync_leaves_the_follower_paused_and_lined_up(
    clock, leader
):
    follower = SimPlayer(clock)
    follower.watch_recording(REC, 300.0)
    original_seek = follower.seek

    async def seek_then_leader_pauses(seconds):
        await leader.pause()
        return await original_seek(seconds)

    follower.seek = seek_then_leader_pauses

    result = await sync_follower(leader, follower, clock=clock)

    assert result.status == SYNCED
    assert leader.state == STATE_PAUSED
    assert follower.state == STATE_PAUSED
    assert abs(true_offset(leader, follower)) <= 0.06


async def test_leader_resuming_mid_sync_leaves_the_follower_playing_and_lined_up(
    clock,
):
    leader = SimPlayer(clock)
    leader.watch_recording(REC, 500.0)
    await leader.pause()
    follower = SimPlayer(clock)
    follower.watch_recording(REC, 300.0)
    await follower.pause()
    original_seek = follower.seek

    async def seek_then_leader_resumes(seconds):
        await leader.resume()
        return await original_seek(seconds)

    follower.seek = seek_then_leader_resumes

    result = await sync_follower(leader, follower, clock=clock)

    assert result.status == SYNCED
    assert leader.state == STATE_PLAYING
    assert follower.state == STATE_PLAYING
    assert abs(true_offset(leader, follower)) <= 0.06


async def test_leader_changing_recording_mid_sync_is_an_error(clock, leader):
    follower = SimPlayer(clock)
    follower.watch_recording(REC, 300.0)
    original_seek = follower.seek

    async def seek_then_leader_switches(seconds):
        leader.recording_id = "15099"
        return await original_seek(seconds)

    follower.seek = seek_then_leader_switches

    with pytest.raises(LeaderError, match="different recording"):
        await sync_follower(leader, follower, clock=clock)


async def test_sample_times_that_differ_are_corrected_for(clock):
    leader = SimPlayer(clock, latency=0.004)
    leader.watch_recording(REC, 500.0)
    follower = SimPlayer(clock, latency=0.09)
    follower.watch_recording(REC, 300.0)

    result = await sync_follower(leader, follower, clock=clock)

    assert result.status == SYNCED
    assert abs(true_offset(leader, follower)) <= 0.06


async def test_measure_offset_ignores_one_bad_sample(clock, leader):
    follower = SimPlayer(clock, position_noise=(0.4, 0.0, 0.0, 0.0, 0.0))
    follower.watch_recording(REC, 500.02)

    offset = await measure_offset(leader, follower, clock)

    assert offset == pytest.approx(true_offset(leader, follower), abs=0.05)


def count_reads(player: SimPlayer, on_read) -> None:
    """Call `on_read(n)` after the player's status() has been read n times."""
    original = player.status
    reads = 0

    async def status():
        nonlocal reads
        result = await original()
        reads += 1
        await on_read(reads)
        return result

    player.status = status


@pytest.mark.parametrize("pause_after_read", [5, 6, 7])
async def test_leader_pausing_during_a_measurement_is_not_reported_as_synced_on_stale_samples(  # noqa: E501
    clock, leader, pause_after_read
):
    follower = SimPlayer(clock)
    follower.watch_recording(REC, 500.02)

    async def on_read(n):
        if n == pause_after_read:
            await leader.pause()

    count_reads(leader, on_read)

    result = await sync_follower(leader, follower, clock=clock)

    assert result.status == SYNCED
    assert leader.state == STATE_PAUSED
    assert follower.state == STATE_PAUSED
    assert abs(true_offset(leader, follower)) <= 0.06


async def test_leader_that_keeps_toggling_is_skipped_not_hung(clock, leader):
    follower = SimPlayer(clock)
    follower.watch_recording(REC, 500.02)

    async def on_read(n):
        # Flip the leader once inside every measurement window.
        if n % 7 == 4:
            if leader.state == STATE_PLAYING:
                await leader.pause()
            else:
                await leader.resume()

    count_reads(leader, on_read)

    result = await sync_follower(leader, follower, clock=clock)

    assert result.status == SKIPPED
    assert result.reason == "The leader kept pausing and resuming during the sync"


async def test_a_follower_that_cannot_be_held_steady_is_out_of_tolerance(clock, leader):
    # Every pause costs far more than the engine allows for.
    follower = SimPlayer(clock, pause_overhead=0.4)

    result = await sync_follower(leader, follower, clock=clock)

    assert result.status == OUT_OF_TOLERANCE
    assert result.rounds == 5
    assert abs(result.offset_ms) > 50


async def test_in_progress_recording_near_the_live_edge(clock):
    dvr = SimDvr(clock)
    recording = dvr.add("6.1", age=120.0)
    leader = SimPlayer(clock, dvr=dvr)
    leader.watch_recording(recording.id, 120.0 - 5.0)  # 5 s behind live
    follower = SimPlayer(clock, dvr=dvr)

    result = await sync_follower(leader, follower, clock=clock)

    assert result.status == SYNCED
    assert abs(true_offset(leader, follower)) <= 0.06


async def test_result_as_dict_omits_what_does_not_apply(clock, leader):
    synced = await sync_follower(leader, SimPlayer(clock), clock=clock)
    skipped = await sync_follower(leader, SimPlayer(clock, starts=False), clock=clock)

    assert set(synced.as_dict()) == {"status", "offset_ms", "rounds"}
    assert skipped.as_dict() == {
        "status": SKIPPED,
        "reason": "It did not start the recording",
    }


async def test_wait_until_reachable_returns_once_the_app_answers(clock):
    player = SimPlayer(clock, reachable_at=clock.time() + 3.0)

    assert await wait_until_reachable(player, 30.0, clock) is True
    assert clock.time() >= player.reachable_at


async def test_wait_until_reachable_gives_up_after_the_timeout(clock):
    player = SimPlayer(clock, reachable_at=clock.time() + 60.0)

    assert await wait_until_reachable(player, 5.0, clock) is False
