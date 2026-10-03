"""Tests for the follow session, against the simulated players."""

import asyncio
from contextlib import asynccontextmanager, suppress

import pytest

from custom_components.channels.lib.follow import (
    ABSENT,
    IN_SYNC,
    NO_DVR,
    REST_FOR,
    RESTING,
    UNEXPECTED,
    WILL_NOT_SETTLE,
    Follower,
    FollowSession,
)
from custom_components.channels.lib.models import (
    STATE_PAUSED,
    STATE_PLAYING,
    STATE_STOPPED,
    ChannelsConnectionError,
)

from .sim import ConcurrentClock, SimDvr, SimPlayer

REC = "15017"
OTHER = "15018"
CLOSE = 0.06  # what a fine-tune leaves, plus the simulator's pause overhead


def offset(leader: SimPlayer, follower: SimPlayer) -> float:
    """Seconds the follower is really ahead of the leader, at this instant."""
    return follower.position() - leader.position()


def commands(player: SimPlayer) -> list[str]:
    """The names of the commands the session has sent a player."""
    return [call.split()[0] for call in player.calls]


@pytest.fixture
def clock() -> ConcurrentClock:
    return ConcurrentClock()


@pytest.fixture
def dvr(clock) -> SimDvr:
    return SimDvr(clock)


@pytest.fixture
def leader(clock, dvr) -> SimPlayer:
    player = SimPlayer(clock, dvr=dvr)
    player.watch_recording(REC, 500.0)
    return player


@pytest.fixture
def office(clock, dvr) -> SimPlayer:
    player = SimPlayer(clock, dvr=dvr)
    player.watch_recording(REC, 500.0)
    return player


@pytest.fixture
def back_yard(clock, dvr) -> SimPlayer:
    player = SimPlayer(clock, dvr=dvr)
    player.watch_recording(REC, 500.0)
    return player


@pytest.fixture
def problems() -> list[tuple[str | None, str]]:
    return []


@pytest.fixture
def session(clock, dvr, leader, office, back_yard, problems) -> FollowSession:
    return FollowSession(
        leader,
        {"office": Follower(office), "back_yard": Follower(back_yard)},
        dvr=dvr,
        clock=clock,
        on_problem=lambda who, message: problems.append((who, message)),
    )


@asynccontextmanager
async def running(session: FollowSession, clock: ConcurrentClock):
    task = asyncio.create_task(session.run())
    try:
        yield
    finally:
        task.cancel()
        # A hold being cancelled still has to send its resume, on the clock.
        await clock.run_for(1)
        with suppress(asyncio.CancelledError):
            await task


async def test_followers_in_step_are_left_alone(
    clock, session, leader, office, back_yard
):
    async with running(session, clock):
        await clock.run_for(3600)

    assert office.calls == []
    assert back_yard.calls == []
    assert session.statuses == {"office": IN_SYNC, "back_yard": IN_SYNC}


async def test_reading_noise_alone_causes_no_correction(clock, dvr, leader, problems):
    noisy = SimPlayer(clock, dvr=dvr, position_noise=(0.13, -0.13, 0.05, -0.2, 0.0))
    noisy.watch_recording(REC, 500.0)
    session = FollowSession(leader, {"office": Follower(noisy)}, dvr=dvr, clock=clock)

    async with running(session, clock):
        await clock.run_for(3600)

    assert noisy.calls == []


async def test_one_stray_reading_does_not_move_a_follower_inside_the_tolerance(
    clock, dvr, leader
):
    """A follower 100 ms behind is left alone; one reading in five says 300 ms."""
    noisy = SimPlayer(clock, dvr=dvr, position_noise=(0.0, 0.0, -0.2, 0.0, 0.0))
    noisy.watch_recording(REC, 499.9)
    session = FollowSession(leader, {"office": Follower(noisy)}, dvr=dvr, clock=clock)

    async with running(session, clock):
        await clock.run_for(600)

    assert noisy.calls == []


async def test_leader_pause_and_resume(clock, session, leader, office, back_yard):
    async with running(session, clock):
        await clock.run_for(5)
        leader.user_pause()
        await clock.run_for(3)
        assert office.state == STATE_PAUSED
        assert back_yard.state == STATE_PAUSED
        assert abs(offset(leader, office)) <= 1.5

        await clock.run_for(20)
        leader.user_resume()
        await clock.run_for(3)
        assert office.state == STATE_PLAYING
        await clock.run_for(15)

    assert abs(offset(leader, office)) <= CLOSE
    assert abs(offset(leader, back_yard)) <= CLOSE


@pytest.mark.parametrize("jump", [-30.0, 180.0])
async def test_leader_seek_is_followed(clock, session, leader, office, back_yard, jump):
    async with running(session, clock):
        await clock.run_for(5)
        leader.user_seek(jump)
        await clock.run_for(3)
        assert abs(offset(leader, office)) <= 1.0
        assert abs(offset(leader, back_yard)) <= 1.0
        await clock.run_for(12)

    assert abs(offset(leader, office)) <= CLOSE
    assert abs(offset(leader, back_yard)) <= CLOSE


async def test_seeks_that_land_short_still_converge(clock, dvr, leader, problems):
    lossy = SimPlayer(clock, dvr=dvr, seek_losses=(0.18, 0.75, 0.38, 0.6, 0.08))
    lossy.watch_recording(REC, 500.0)
    session = FollowSession(
        leader,
        {"office": Follower(lossy)},
        dvr=dvr,
        clock=clock,
        on_problem=lambda who, message: problems.append((who, message)),
    )

    async with running(session, clock):
        await clock.run_for(5)
        leader.user_seek(180.0)
        await clock.run_for(20)

    assert abs(offset(leader, lossy)) <= CLOSE
    assert problems == []


@pytest.mark.parametrize("gap", [1, 3])
async def test_repeated_skips_do_not_rest_a_follower(
    clock, session, leader, office, problems, gap
):
    """Someone pressing skip again and again is not a follower failing to keep up.

    A second apart, each skip lands before the follower's last seek is
    checked. Three apart, each lands in the middle of its fine-tune.
    """
    async with running(session, clock):
        for _ in range(10):
            await clock.run_for(gap)
            leader.user_seek(30.0)
        await clock.run_for(20)

    assert abs(offset(leader, office)) <= CLOSE
    assert problems == []


async def test_leader_pausing_again_and_again_does_not_rest_a_follower(
    clock, session, leader, office, problems
):
    office.user_seek(-0.6)  # close enough to need a fine-tune, not a jump

    async with running(session, clock):
        for _ in range(30):
            await clock.run_for(1.5)
            leader.user_pause()
            await clock.run_for(1.5)
            leader.user_resume()
        await clock.run_for(20)

    assert problems == []
    assert abs(offset(leader, office)) <= CLOSE


async def test_leader_moves_to_another_recording(clock, session, leader, office):
    async with running(session, clock):
        await clock.run_for(5)
        leader.watch_recording(OTHER, 60.0)
        await clock.run_for(25)

    assert office.recording_id == OTHER
    assert abs(offset(leader, office)) <= CLOSE


async def test_follower_target_offset_is_held(clock, dvr, leader):
    follower = SimPlayer(clock, dvr=dvr)
    follower.watch_recording(REC, 470.0)
    session = FollowSession(
        leader, {"office": Follower(follower, target=-0.08)}, dvr=dvr, clock=clock
    )

    async with running(session, clock):
        await clock.run_for(20)

    assert abs(offset(leader, follower) + 0.08) <= CLOSE


async def test_follower_paused_with_its_own_remote_is_resumed(
    clock, session, leader, office, back_yard
):
    async with running(session, clock):
        await clock.run_for(5)
        office.user_pause()
        await clock.run_for(3)
        assert office.state == STATE_PLAYING
        await clock.run_for(15)

    assert abs(offset(leader, office)) <= CLOSE
    assert back_yard.calls == []


async def test_follower_seeked_with_its_own_remote_is_pulled_back(
    clock, session, leader, office
):
    async with running(session, clock):
        await clock.run_for(5)
        office.user_seek(-20.0)
        await clock.run_for(15)

    assert abs(offset(leader, office)) <= CLOSE


async def test_follower_stopped_with_its_own_remote_is_restarted(
    clock, session, leader, office
):
    async with running(session, clock):
        await clock.run_for(5)
        office.user_stop()
        await clock.run_for(25)

    assert office.recording_id == REC
    assert abs(offset(leader, office)) <= CLOSE


async def test_follower_that_leaves_the_app_is_left_alone_until_it_returns(
    clock, session, leader, office, back_yard
):
    async with running(session, clock):
        await clock.run_for(5)
        office.leave_app()
        await clock.run_for(5)
        assert session.statuses["office"] == ABSENT
        leader.user_seek(120.0)
        await clock.run_for(30)
        assert office.calls == []
        assert abs(offset(leader, back_yard)) <= CLOSE

        office.return_to_app()
        await clock.run_for(20)

    assert office.state == STATE_PLAYING
    assert abs(offset(leader, office)) <= CLOSE


async def test_follower_left_at_the_end_of_a_recording_is_restarted(
    clock, session, leader, office
):
    office.finish_recording(REC)

    async with running(session, clock):
        await clock.run_for(30)

    assert office.state == STATE_PLAYING
    assert abs(offset(leader, office)) <= CLOSE


async def test_leader_stopped_leaves_followers_alone_until_it_plays_again(
    clock, session, leader, office
):
    async with running(session, clock):
        await clock.run_for(5)
        leader.user_stop()
        await clock.run_for(60)
        assert office.calls == []
        assert office.state == STATE_PLAYING

        leader.watch_recording(REC, 100.0)
        await clock.run_for(20)

    assert abs(offset(leader, office)) <= CLOSE


async def test_leader_left_at_the_end_is_not_followed(clock, session, leader, office):
    async with running(session, clock):
        await clock.run_for(5)
        leader.finish_recording(REC)
        await clock.run_for(60)

    assert office.calls == []
    assert office.state == STATE_PLAYING


async def test_leader_unreachable_leaves_followers_alone(
    clock, session, leader, office
):
    async with running(session, clock):
        await clock.run_for(5)
        leader.leave_app()
        await clock.run_for(60)

    assert office.calls == []


# -- the leader on live TV ------------------------------------------------------


async def test_live_channel_is_recorded_once_it_settles(
    clock, dvr, session, leader, office, back_yard
):
    async with running(session, clock):
        await clock.run_for(5)
        leader.watch_live("6.1")
        await clock.run_for(8)
        assert "toggle_record" not in commands(leader)
        await clock.run_for(40)

    assert commands(leader).count("toggle_record") == 1
    recording = await dvr.in_progress_recording("6.1")
    assert recording is not None
    assert leader.recording_id == recording.id
    assert office.recording_id == recording.id
    assert back_yard.recording_id == recording.id
    assert abs(offset(leader, office)) <= CLOSE


async def test_channel_surfing_records_nothing(clock, dvr, session, leader, office):
    async with running(session, clock):
        await clock.run_for(5)
        for channel in ("6.1", "3.1", "10.1", "6.1", "3.1"):
            leader.watch_live(channel)
            await clock.run_for(6)

    assert "toggle_record" not in commands(leader)
    assert office.calls == []
    assert dvr.recordings == {}


async def test_channel_already_recording_is_used_without_waiting(
    clock, dvr, session, leader, office
):
    existing = dvr.add("6.1", age=600.0)

    async with running(session, clock):
        await clock.run_for(5)
        leader.watch_live("6.1")
        await clock.run_for(6)
        assert leader.recording_id == existing.id
        await clock.run_for(20)

    assert "toggle_record" not in commands(leader)
    assert not dvr.recordings[existing.id].completed
    assert len(dvr.recordings) == 1
    assert office.recording_id == existing.id
    assert abs(offset(leader, office)) <= CLOSE


async def test_job_without_a_file_yet_is_waited_for_not_recorded_again(
    clock, dvr, session, leader, office
):
    dvr.add_job("6.1", starts_in=-5.0)

    async def file_appears() -> None:
        await clock.sleep(4)
        dvr.add("6.1", age=4.0)

    async with running(session, clock):
        await clock.run_for(5)
        leader.watch_live("6.1")
        appearing = asyncio.create_task(file_appears())
        await clock.run_for(40)
        await appearing

    assert "toggle_record" not in commands(leader)
    assert len(dvr.recordings) == 1
    assert leader.recording_id is not None
    assert office.recording_id == leader.recording_id


async def test_no_tuner_free_is_reported_once_and_not_retried(
    clock, dvr, session, leader, office, problems
):
    dvr.tuner_free = False

    async with running(session, clock):
        await clock.run_for(5)
        leader.watch_live("6.1")
        await clock.run_for(120)

    assert commands(leader).count("toggle_record") == 1
    assert len(problems) == 1
    who, message = problems[0]
    assert who is None
    assert "no tuner available" in message
    assert office.calls == []


async def test_a_failed_channel_is_tried_again_after_a_channel_change(
    clock, dvr, session, leader, problems
):
    dvr.tuner_free = False

    async with running(session, clock):
        await clock.run_for(5)
        leader.watch_live("6.1")
        await clock.run_for(40)
        dvr.tuner_free = True
        leader.watch_live("3.1")
        await clock.run_for(40)

    assert leader.recording_id is not None
    assert len(problems) == 1


async def test_live_tv_without_a_dvr_is_reported_once(clock, leader, office, problems):
    session = FollowSession(
        leader,
        {"office": Follower(office)},
        dvr=None,
        clock=clock,
        on_problem=lambda who, message: problems.append((who, message)),
    )

    async with running(session, clock):
        await clock.run_for(5)
        leader.watch_live("6.1")
        await clock.run_for(8)
        assert problems == []
        await clock.run_for(52)

    assert problems == [(None, NO_DVR)]
    assert office.calls == []


# -- a follower that will not settle ------------------------------------------------


async def test_follower_that_never_settles_is_rested_and_the_other_is_unaffected(
    clock, dvr, leader, back_yard, problems
):
    stuck = SimPlayer(clock, dvr=dvr, starts=False)  # ignores every play command
    session = FollowSession(
        leader,
        {"office": Follower(stuck), "back_yard": Follower(back_yard)},
        dvr=dvr,
        clock=clock,
        on_problem=lambda who, message: problems.append((who, message)),
    )
    back_yard.user_seek(-40.0)

    async with running(session, clock):
        await clock.run_for(85)
        assert session.statuses["office"] == RESTING
        assert problems == [("office", WILL_NOT_SETTLE)]
        sent = len(stuck.calls)
        await clock.run_for(REST_FOR - 10)
        assert len(stuck.calls) == sent

    assert stuck.state == STATE_STOPPED
    assert abs(offset(leader, back_yard)) <= CLOSE
    assert session.statuses["back_yard"] == IN_SYNC


class DeafToSeeks(SimPlayer):
    """A player that acknowledges seeks and does not move."""

    async def seek(self, seconds: float):
        return await self._reply(f"seek {seconds:.3f}")


async def test_follower_whose_seeks_do_nothing_is_rested(clock, dvr, leader, problems):
    stuck = DeafToSeeks(clock, dvr=dvr)
    stuck.watch_recording(REC, 300.0)
    session = FollowSession(
        leader,
        {"office": Follower(stuck)},
        dvr=dvr,
        clock=clock,
        on_problem=lambda who, message: problems.append((who, message)),
    )

    async with running(session, clock):
        await clock.run_for(15)

    assert problems == [("office", WILL_NOT_SETTLE)]
    assert session.statuses["office"] == RESTING
    assert commands(stuck).count("seek") <= 6


class Faulty(SimPlayer):
    """A player whose status read fails in a way the engine does not expect."""

    async def status(self):
        raise RuntimeError("something nobody planned for")


async def test_unexpected_error_on_one_follower_rests_it_and_spares_the_other(
    clock, dvr, leader, back_yard, problems
):
    faulty = Faulty(clock, dvr=dvr)
    session = FollowSession(
        leader,
        {"office": Follower(faulty), "back_yard": Follower(back_yard)},
        dvr=dvr,
        clock=clock,
        on_problem=lambda who, message: problems.append((who, message)),
    )
    back_yard.user_seek(-40.0)

    async with running(session, clock):
        await clock.run_for(20)

    assert problems == [("office", UNEXPECTED)]
    assert session.statuses["office"] == RESTING
    assert abs(offset(leader, back_yard)) <= CLOSE


async def test_unexpected_error_reading_the_leader_leaves_followers_alone(
    clock, dvr, office
):
    session = FollowSession(
        Faulty(clock, dvr=dvr), {"office": Follower(office)}, dvr=dvr, clock=clock
    )
    office.user_seek(-40.0)

    async with running(session, clock):
        await clock.run_for(30)

    assert office.calls == []
    assert session.statuses["office"] == IN_SYNC


async def test_status_changes_are_reported(clock, dvr, leader, office):
    seen: list[tuple[str, str]] = []
    session = FollowSession(
        leader,
        {"office": Follower(office)},
        dvr=dvr,
        clock=clock,
        on_status=lambda name, status: seen.append((name, status)),
    )

    async with running(session, clock):
        await clock.run_for(3)
        office.user_seek(-20.0)
        await clock.run_for(15)

    assert seen[0] == ("office", IN_SYNC)
    assert ("office", "correcting") in seen
    assert seen[-1] == ("office", IN_SYNC)


async def test_cancelling_a_session_stops_every_task(clock, session, office):
    task = asyncio.create_task(session.run())
    await clock.run_for(5)
    task.cancel()
    await clock.run_for(1)
    with suppress(asyncio.CancelledError):
        await task

    office.user_seek(-20.0)
    await clock.run_for(30)

    assert office.calls == []


# -- fixes from review ---------------------------------------------------------------


async def test_cancelling_mid_fine_tune_does_not_leave_a_tv_paused(clock, dvr, leader):
    follower = SimPlayer(clock, dvr=dvr)
    follower.watch_recording(REC, 500.6)
    session = FollowSession(
        leader, {"office": Follower(follower)}, dvr=dvr, clock=clock
    )

    task = asyncio.create_task(session.run())
    for _ in range(300):
        await clock.run_for(0.1)
        if follower.state == STATE_PAUSED:
            break
    assert follower.state == STATE_PAUSED
    task.cancel()
    await clock.run_for(2)
    with suppress(asyncio.CancelledError):
        await task

    assert follower.state == STATE_PLAYING
    assert commands(follower)[-1] == "resume"


async def test_a_failed_live_switch_is_not_retried_after_a_leader_read_blip(
    clock, dvr, session, leader, problems
):
    dvr.tuner_free = False

    async with running(session, clock):
        await clock.run_for(5)
        leader.watch_live("6.1")
        await clock.run_for(40)
        assert commands(leader).count("toggle_record") == 1
        assert len(problems) == 1
        leader.leave_app()
        await clock.run_for(3)
        leader.return_to_app()
        leader.watch_live("6.1")
        await clock.run_for(60)

    assert commands(leader).count("toggle_record") == 1
    assert len(problems) == 1


class ExplodingDvr(SimDvr):
    """A DVR whose in-progress lookup fails in a way nobody planned for."""

    def __init__(self, clock) -> None:
        super().__init__(clock)
        self.lookups = 0

    async def in_progress_recording(self, channel):
        self.lookups += 1
        raise RuntimeError("something nobody planned for")


async def test_unexpected_error_moving_the_leader_to_a_recording_is_not_retried(
    clock, leader, office, problems
):
    dvr = ExplodingDvr(clock)
    session = FollowSession(
        leader,
        {"office": Follower(office)},
        dvr=dvr,
        clock=clock,
        on_problem=lambda who, message: problems.append((who, message)),
    )

    async with running(session, clock):
        await clock.run_for(5)
        leader.watch_live("6.1")
        await clock.run_for(60)

    assert problems == [(None, UNEXPECTED)]
    assert "toggle_record" not in commands(leader)
    assert dvr.lookups == 1


async def test_paused_follower_far_from_a_paused_leader_is_seeked_once(
    clock, dvr, leader, problems
):
    follower = SimPlayer(clock, dvr=dvr)
    follower.watch_recording(REC, 480.0)
    session = FollowSession(
        leader, {"office": Follower(follower)}, dvr=dvr, clock=clock
    )
    leader.user_pause()
    follower.user_pause()

    async with running(session, clock):
        await clock.run_for(6)

    assert abs(offset(leader, follower)) <= 1.5
    assert follower.state == STATE_PAUSED
    assert commands(follower).count("seek") == 1


async def test_paused_follower_whose_seeks_do_nothing_is_rested(
    clock, dvr, leader, problems
):
    stuck = DeafToSeeks(clock, dvr=dvr)
    stuck.watch_recording(REC, 480.0)
    session = FollowSession(
        leader,
        {"office": Follower(stuck)},
        dvr=dvr,
        clock=clock,
        on_problem=lambda who, message: problems.append((who, message)),
    )
    leader.user_pause()
    stuck.user_pause()

    async with running(session, clock):
        await clock.run_for(15)

    assert problems == [("office", WILL_NOT_SETTLE)]
    assert session.statuses["office"] == RESTING
    assert commands(stuck).count("seek") <= 6


async def test_channel_surfing_without_a_dvr_reports_nothing_until_it_settles(
    clock, leader, office, problems
):
    session = FollowSession(
        leader,
        {"office": Follower(office)},
        dvr=None,
        clock=clock,
        on_problem=lambda who, message: problems.append((who, message)),
    )

    async with running(session, clock):
        await clock.run_for(5)
        for channel in ("6.1", "3.1", "10.1", "6.1", "3.1"):
            leader.watch_live(channel)
            await clock.run_for(6)
        assert problems == []
        await clock.run_for(30)

    assert problems == [(None, NO_DVR)]


async def test_each_visit_to_live_tv_without_a_dvr_is_reported(
    clock, leader, office, problems
):
    session = FollowSession(
        leader,
        {"office": Follower(office)},
        dvr=None,
        clock=clock,
        on_problem=lambda who, message: problems.append((who, message)),
    )

    async with running(session, clock):
        await clock.run_for(5)
        leader.watch_live("6.1")
        await clock.run_for(20)
        assert problems == [(None, NO_DVR)]
        leader.watch_recording(REC, 100.0)
        await clock.run_for(10)
        leader.watch_live("6.1")
        await clock.run_for(20)

    assert problems == [(None, NO_DVR), (None, NO_DVR)]


async def test_leader_reaching_the_end_mid_fine_tune_does_not_drag_the_follower(
    clock, dvr, leader, problems
):
    follower = SimPlayer(clock, dvr=dvr)
    follower.watch_recording(REC, 500.6)
    session = FollowSession(
        leader,
        {"office": Follower(follower)},
        dvr=dvr,
        clock=clock,
        on_problem=lambda who, message: problems.append((who, message)),
    )

    async with running(session, clock):
        for _ in range(300):
            await clock.run_for(0.1)
            if follower.calls:
                break
        assert follower.calls
        leader.finish_recording(REC)
        await clock.run_for(30)

    assert follower.position() > 400
    assert problems == []


async def test_callbacks_that_raise_do_not_end_the_session(clock, dvr, leader, office):
    def explode(*_args) -> None:
        raise RuntimeError("a callback with a bug")

    session = FollowSession(
        leader,
        {"office": Follower(office)},
        dvr=dvr,
        clock=clock,
        on_problem=explode,
        on_status=explode,
    )
    office.user_seek(-20.0)

    async with running(session, clock):
        await clock.run_for(20)

    assert abs(offset(leader, office)) <= CLOSE
    assert session.statuses["office"] == IN_SYNC


# -- fixes from the final review --------------------------------------------------


class ResumeFails(SimPlayer):
    """A player whose resume fails once `failing` is set."""

    failing = False

    async def resume(self):
        if self.failing:
            self.calls.append("resume")
            self.log.append((self.clock.time(), "resume"))
            raise ChannelsConnectionError("the app is not in the foreground")
        return await super().resume()


async def test_a_session_cancelled_mid_hold_ends_even_if_the_resume_fails(
    clock, dvr, leader
):
    follower = ResumeFails(clock, dvr=dvr)
    follower.watch_recording(REC, 500.6)
    session = FollowSession(
        leader, {"office": Follower(follower)}, dvr=dvr, clock=clock
    )

    task = asyncio.create_task(session.run())
    for _ in range(300):
        await clock.run_for(0.1)
        if follower.state == STATE_PAUSED:
            break
    assert follower.state == STATE_PAUSED
    follower.failing = True
    task.cancel()
    await clock.run_for(2)

    assert task.done()
    sent = len(follower.calls)
    await clock.run_for(60)
    assert len(follower.calls) == sent
    with suppress(asyncio.CancelledError):
        await task


class Surfer(SimPlayer):
    """A leader whose viewer moves on from a channel soon after it is read there.

    Once the session has read it on `first`, it moves to `then`: `delay`
    seconds later, or, with `delay` None, straight after the reading that
    finds it has been there `stays` seconds.
    """

    first: str = ""
    then: str = ""
    delay: float | None = None
    stays: float = 0.0
    moved_at: float | None = None
    _seen: float | None = None

    async def _move_later(self) -> None:
        await self.clock.sleep(self.delay)
        self.watch_live(self.then)
        self.moved_at = self.clock.time()

    async def status(self):
        reply = await super().status()
        if self.moved_at is None and reply.channel_number == self.first:
            now = self.clock.time()
            if self._seen is None:
                self._seen = now
                if self.delay is not None:
                    self._mover = asyncio.create_task(self._move_later())
            if self.delay is None and now - self._seen >= self.stays:
                self.watch_live(self.then)
                self.moved_at = now
        return reply


def toggles(player: SimPlayer) -> list[float]:
    """When the session sent the record toggle to a player."""
    return [at for at, call in player.log if call == "toggle_record"]


async def test_surfing_past_a_recorded_channel_waits_on_the_next_one(clock, problems):
    dvr = SimDvr(clock, latency=0.3)
    dvr.add("6.1", age=600.0)
    leader = Surfer(clock, dvr=dvr)
    leader.watch_recording(REC, 500.0)
    leader.first, leader.then, leader.delay = "6.1", "3.1", 0.1
    office = SimPlayer(clock, dvr=dvr)
    office.watch_recording(REC, 500.0)
    session = FollowSession(
        leader,
        {"office": Follower(office)},
        dvr=dvr,
        clock=clock,
        on_problem=lambda who, message: problems.append((who, message)),
    )

    async with running(session, clock):
        await clock.run_for(5)
        leader.watch_live("6.1")
        await clock.run_for(40)

    assert leader.moved_at is not None
    assert toggles(leader)
    assert min(toggles(leader)) >= leader.moved_at + 10.0
    assert problems == []


async def test_a_channel_change_as_the_settle_time_ends_starts_it_again(
    clock, problems
):
    dvr = SimDvr(clock, latency=0.3)
    leader = Surfer(clock, dvr=dvr)
    leader.watch_recording(REC, 500.0)
    leader.first, leader.then, leader.stays = "3.1", "10.1", 10.0
    office = SimPlayer(clock, dvr=dvr)
    office.watch_recording(REC, 500.0)
    session = FollowSession(
        leader,
        {"office": Follower(office)},
        dvr=dvr,
        clock=clock,
        on_problem=lambda who, message: problems.append((who, message)),
    )

    async with running(session, clock):
        await clock.run_for(5)
        leader.watch_live("3.1")
        await clock.run_for(40)

    assert leader.moved_at is not None
    assert toggles(leader)
    assert min(toggles(leader)) >= leader.moved_at + 10.0
    assert problems == []
