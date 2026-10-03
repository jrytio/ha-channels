"""Tests for the simulator's own machinery that other tests lean on."""

import asyncio

import pytest

from custom_components.channels.lib.models import (
    STATE_PAUSED,
    STATE_PLAYING,
    STATE_STOPPED,
    ChannelsConnectionError,
)

from .sim import ConcurrentClock, SimPlayer

REC = "15017"


async def test_concurrent_sleeps_overlap_instead_of_adding_up():
    clock = ConcurrentClock(start=1000.0)
    woke: list[tuple[str, float]] = []

    async def sleeper(name: str, seconds: float) -> None:
        await clock.sleep(seconds)
        woke.append((name, clock.time()))

    tasks = [
        asyncio.create_task(sleeper("slow", 3.0)),
        asyncio.create_task(sleeper("fast", 1.0)),
    ]
    await clock.run_for(10)
    await asyncio.gather(*tasks)

    assert woke == [("fast", 1001.0), ("slow", 1003.0)]
    assert clock.time() == 1010.0


async def test_run_for_stops_at_its_end_and_leaves_later_sleepers_asleep():
    clock = ConcurrentClock(start=1000.0)
    woke: list[float] = []

    async def sleeper() -> None:
        await clock.sleep(5.0)
        woke.append(clock.time())

    task = asyncio.create_task(sleeper())
    await clock.run_for(4)
    assert woke == []
    assert clock.time() == 1004.0

    await clock.run_for(1)
    await task
    assert woke == [1005.0]


async def test_a_task_woken_by_another_runs_before_the_clock_moves_on():
    clock = ConcurrentClock(start=1000.0)
    ready = asyncio.Event()
    seen: list[float] = []

    async def waiter() -> None:
        await ready.wait()
        seen.append(clock.time())

    async def setter() -> None:
        await clock.sleep(2.0)
        ready.set()
        await clock.sleep(5.0)

    tasks = [asyncio.create_task(waiter()), asyncio.create_task(setter())]
    await clock.run_for(10)
    await asyncio.gather(*tasks)

    assert seen == [1002.0]


async def test_a_cancelled_sleeper_does_not_stall_the_clock():
    clock = ConcurrentClock(start=1000.0)
    task = asyncio.create_task(clock.sleep(3.0))
    await clock.run_for(1)
    task.cancel()

    await clock.run_for(10)

    assert clock.time() == 1011.0


async def test_remote_actions_change_the_player_without_being_logged():
    clock = ConcurrentClock()
    player = SimPlayer(clock)
    player.watch_recording(REC, 100.0)

    player.user_pause()
    assert player.state == STATE_PAUSED
    await clock.run_for(5)
    assert player.position() == pytest.approx(100.0)

    player.user_resume()
    await clock.run_for(5)
    assert player.state == STATE_PLAYING
    assert player.position() == pytest.approx(105.0)

    player.user_seek(-20.0)
    assert player.position() == pytest.approx(85.0)

    player.user_stop()
    assert player.state == STATE_STOPPED
    assert player.recording_id is None
    assert player.calls == []


async def test_a_player_that_left_the_app_does_not_answer_until_it_returns():
    clock = ConcurrentClock()
    player = SimPlayer(clock)
    player.watch_recording(REC, 100.0)

    player.leave_app()
    reading = asyncio.create_task(player.status())
    await clock.run_for(1)
    with pytest.raises(ChannelsConnectionError):
        await reading

    player.return_to_app()
    reading = asyncio.create_task(player.status())
    await clock.run_for(1)
    status = await reading
    assert status.state == STATE_PAUSED
    assert status.position == pytest.approx(100.0)
