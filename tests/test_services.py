"""Tests for the sync and switch actions."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE
from homeassistant.exceptions import ServiceValidationError
import probatio as vol
import pytest
import yaml

from custom_components.channels.const import CONF_SYNC_OFFSET_MS, DOMAIN
from custom_components.channels.lib import (
    ChannelsConnectionError,
    LeaderError,
    SwitchError,
    SwitchResult,
    SyncResult,
)

from .conftest import (
    LIVING_ROOM,
    OFFICE,
    app_entry,
    make_app_client,
    setup_integration,
    status_of,
)
from .fixtures import STATUS_LIVE

SYNCED = SyncResult("synced", offset_ms=-33.7, rounds=3)


@pytest.fixture
def clients(app_clients):
    app_clients["10.0.0.1"] = make_app_client()
    app_clients["10.0.0.2"] = make_app_client()
    return app_clients


@pytest.fixture
def engine():
    """Stand in for the engine; it has its own tests."""
    with (
        patch(
            "custom_components.channels.services.sync_follower",
            AsyncMock(return_value=SYNCED),
        ) as sync,
        patch(
            "custom_components.channels.services.wait_until_reachable",
            AsyncMock(return_value=True),
        ) as reachable,
    ):
        yield sync, reachable


async def sync_playback(hass, **data):
    return await hass.services.async_call(
        DOMAIN,
        "sync_playback",
        {"leader": LIVING_ROOM, "followers": [OFFICE], **data},
        blocking=True,
        return_response=True,
    )


async def test_sync_reports_each_follower(
    hass, living_room_entry, office_entry, clients, engine
):
    sync, reachable = engine
    await setup_integration(hass)

    response = await sync_playback(hass)

    assert response == {
        "error": None,
        "followers": {OFFICE: {"status": "synced", "offset_ms": -33.7, "rounds": 3}},
    }
    leader, follower = sync.await_args.args
    assert leader is clients["10.0.0.1"]
    assert follower is clients["10.0.0.2"]
    assert sync.await_args.kwargs["tolerance"] == 0.05
    assert sync.await_args.kwargs["target"] == 0
    assert reachable.await_args.args[1] == 30


async def test_sync_passes_the_tvs_offset_and_the_call_options(
    hass, living_room_entry, office_entry, clients, engine
):
    sync, reachable = engine
    hass.config_entries.async_update_entry(
        office_entry, options={CONF_SYNC_OFFSET_MS: 80}
    )
    await setup_integration(hass)

    await sync_playback(hass, tolerance_ms=100, follower_timeout=5)

    assert sync.await_args.kwargs["target"] == pytest.approx(-0.08)
    assert sync.await_args.kwargs["tolerance"] == pytest.approx(0.1)
    assert reachable.await_args.args[1] == 5


async def test_unavailable_follower_is_still_synced_once_it_answers(
    hass, living_room_entry, office_entry, clients, engine
):
    sync, _ = engine
    clients["10.0.0.2"].status.side_effect = ChannelsConnectionError("asleep")
    await setup_integration(hass)
    assert hass.states.get(OFFICE).state == STATE_UNAVAILABLE

    response = await sync_playback(hass)

    assert response["followers"][OFFICE]["status"] == "synced"
    sync.assert_awaited_once()


async def test_follower_that_never_answers_is_skipped(
    hass, living_room_entry, office_entry, clients, engine
):
    sync, reachable = engine
    reachable.return_value = False
    await setup_integration(hass)

    response = await sync_playback(hass)

    assert response["followers"][OFFICE] == {
        "status": "skipped",
        "reason": "Channels did not answer on that TV",
    }
    sync.assert_not_awaited()


async def test_follower_with_no_entity_yet_is_reported_as_not_set_up(
    hass, living_room_entry, clients, engine
):
    await setup_integration(hass)

    response = await sync_playback(
        hass, followers=["media_player.back_yard_channels", "media_player.kitchen"]
    )

    assert response["error"] is None
    assert response["followers"] == {
        "media_player.back_yard_channels": {"status": "not_set_up"},
        "media_player.kitchen": {"status": "not_set_up"},
    }


async def test_follower_whose_entry_is_not_loaded_is_skipped_not_ignored(
    hass, living_room_entry, office_entry, clients, engine
):
    sync, _ = engine
    await setup_integration(hass)
    assert await hass.config_entries.async_unload(office_entry.entry_id)

    response = await sync_playback(hass)

    assert response["followers"][OFFICE] == {
        "status": "skipped",
        "reason": "Its Channels entry is not loaded",
    }
    sync.assert_not_awaited()


async def test_leader_whose_entry_is_not_loaded_is_reported(
    hass, living_room_entry, office_entry, clients, engine
):
    sync, _ = engine
    await setup_integration(hass)
    assert await hass.config_entries.async_unload(living_room_entry.entry_id)

    response = await sync_playback(hass)

    assert response == {
        "error": "The leader's Channels entry is not loaded",
        "followers": {},
    }
    sync.assert_not_awaited()


async def test_leader_in_the_follower_list_and_duplicates(
    hass, living_room_entry, office_entry, clients, engine
):
    sync, _ = engine
    await setup_integration(hass)

    response = await sync_playback(hass, followers=[LIVING_ROOM, OFFICE, OFFICE])

    assert response["followers"][LIVING_ROOM]["reason"] == "It is the leader"
    assert response["followers"][OFFICE]["status"] == "synced"
    sync.assert_awaited_once()


async def test_engine_error_for_one_follower_is_reported_not_raised(
    hass, living_room_entry, office_entry, clients, engine
):
    sync, _ = engine
    sync.side_effect = LeaderError("The leader is not playing anything")
    await setup_integration(hass)

    response = await sync_playback(hass)

    assert response["followers"][OFFICE] == {
        "status": "skipped",
        "reason": "The leader is not playing anything",
    }


UNEXPECTED = "An unexpected error occurred; see the Home Assistant log"
BEDROOM = "media_player.bedroom_channels"


async def test_unexpected_error_for_one_follower_does_not_stop_the_others(
    hass, living_room_entry, office_entry, clients, engine, caplog
):
    sync, _ = engine
    app_entry(hass, "Bedroom", "10.0.0.3")
    clients["10.0.0.3"] = make_app_client()

    async def engine_that_breaks_on_the_office(leader, follower, **kwargs):
        if follower is clients["10.0.0.2"]:
            raise RuntimeError("bug")
        return SYNCED

    sync.side_effect = engine_that_breaks_on_the_office
    await setup_integration(hass)

    response = await sync_playback(hass, followers=[OFFICE, BEDROOM])

    assert response["followers"][OFFICE] == {"status": "skipped", "reason": UNEXPECTED}
    assert response["followers"][BEDROOM]["status"] == "synced"
    assert "RuntimeError: bug" in caplog.text


async def test_leader_on_live_tv_is_reported_in_the_response(
    hass, living_room_entry, office_entry, clients, engine
):
    sync, _ = engine
    clients["10.0.0.1"].status.return_value = status_of(STATUS_LIVE)
    await setup_integration(hass)

    response = await sync_playback(hass)

    assert "live TV" in response["error"]
    assert response["followers"] == {}
    sync.assert_not_awaited()


async def test_leader_that_is_not_a_channels_player_is_rejected(
    hass, office_entry, clients, engine
):
    await setup_integration(hass)

    with pytest.raises(ServiceValidationError, match="not a Channels media player"):
        await sync_playback(hass, leader="media_player.apple_tv_office")


async def switch(hass, **data):
    return await hass.services.async_call(
        DOMAIN,
        "switch_to_recording",
        {ATTR_ENTITY_ID: LIVING_ROOM, **data},
        blocking=True,
        return_response=True,
    )


async def test_switch_without_a_dvr_server_reports_why(
    hass, living_room_entry, clients
):
    clients["10.0.0.1"].status.return_value = status_of(STATUS_LIVE)
    await setup_integration(hass)

    response = await switch(hass)

    assert response[LIVING_ROOM] == {
        "recording_id": None,
        "switched": False,
        "started_recording": False,
        "error": "No Channels DVR server is set up",
    }


async def test_switch_without_a_dvr_server_leaves_a_tv_on_a_recording_alone(
    hass, living_room_entry, clients
):
    await setup_integration(hass)

    response = await switch(hass)

    assert response[LIVING_ROOM] == {
        "recording_id": "15017",
        "switched": False,
        "started_recording": False,
        "error": None,
    }
    clients["10.0.0.1"].play_recording.assert_not_awaited()


async def test_switch_without_a_dvr_server_on_an_unreachable_tv_says_so(
    hass, living_room_entry, clients
):
    clients["10.0.0.1"].status.side_effect = ChannelsConnectionError(
        "Channels at 10.0.0.1 did not answer in time"
    )
    await setup_integration(hass)

    response = await switch(hass)

    assert response[LIVING_ROOM]["error"] == (
        "Channels at 10.0.0.1 did not answer in time"
    )
    assert response[LIVING_ROOM]["switched"] is False


async def test_switch_reports_what_it_did(
    hass, living_room_entry, dvr_entry, clients, dvr_client
):
    await setup_integration(hass)

    with patch(
        "custom_components.channels.services.switch_to_recording",
        AsyncMock(return_value=SwitchResult("15018", True, True)),
    ) as switcher:
        response = await switch(hass, behind_live=8)

    assert response[LIVING_ROOM] == {
        "recording_id": "15018",
        "switched": True,
        "started_recording": True,
        "error": None,
    }
    assert switcher.await_args.args == (clients["10.0.0.1"], dvr_client)
    assert switcher.await_args.kwargs["behind_live"] == 8


async def test_switch_failure_carries_the_reason(
    hass, living_room_entry, dvr_entry, clients
):
    await setup_integration(hass)

    with patch(
        "custom_components.channels.services.switch_to_recording",
        AsyncMock(side_effect=SwitchError("The recording did not start: no tuner")),
    ):
        response = await switch(hass)

    assert response[LIVING_ROOM]["error"] == "The recording did not start: no tuner"
    assert response[LIVING_ROOM]["switched"] is False


async def test_switch_failure_after_a_recording_started_says_so(
    hass, living_room_entry, dvr_entry, clients
):
    await setup_integration(hass)
    error = SwitchError(
        "A recording was started, but the TV did not start playing it",
        started_recording=True,
    )

    with patch(
        "custom_components.channels.services.switch_to_recording",
        AsyncMock(side_effect=error),
    ):
        response = await switch(hass)

    assert response[LIVING_ROOM] == {
        "recording_id": None,
        "switched": False,
        "started_recording": True,
        "error": "A recording was started, but the TV did not start playing it",
    }


async def test_unexpected_switch_error_is_reported_for_that_tv_only(
    hass, living_room_entry, office_entry, dvr_entry, clients, caplog
):
    await setup_integration(hass)

    async def switch_that_breaks_on_the_living_room(app, dvr, **kwargs):
        if app is clients["10.0.0.1"]:
            raise RuntimeError("bug")
        return SwitchResult("15018", True, False)

    with patch(
        "custom_components.channels.services.switch_to_recording",
        switch_that_breaks_on_the_living_room,
    ):
        response = await switch(hass, **{ATTR_ENTITY_ID: [LIVING_ROOM, OFFICE]})

    assert response[LIVING_ROOM] == {
        "recording_id": None,
        "switched": False,
        "started_recording": False,
        "error": UNEXPECTED,
    }
    assert response[OFFICE]["switched"] is True
    assert "RuntimeError: bug" in caplog.text


async def test_switch_rejects_a_buffer_below_the_minimum(
    hass, living_room_entry, dvr_entry, clients
):
    await setup_integration(hass)

    with pytest.raises(vol.Invalid):
        await switch(hass, behind_live=1)


async def test_switch_on_an_unavailable_tv_reports_instead_of_raising(
    hass, living_room_entry, dvr_entry, clients
):
    clients["10.0.0.1"].status.side_effect = ChannelsConnectionError(
        "Channels did not answer"
    )
    await setup_integration(hass)
    assert hass.states.get(LIVING_ROOM).state == STATE_UNAVAILABLE

    response = await switch(hass)

    assert "did not answer" in response[LIVING_ROOM]["error"]
    assert response[LIVING_ROOM]["switched"] is False


async def test_switch_on_something_that_is_not_a_channels_player_is_rejected(
    hass, living_room_entry, clients
):
    await setup_integration(hass)

    with pytest.raises(ServiceValidationError, match="not a Channels media player"):
        await switch(hass, **{ATTR_ENTITY_ID: "media_player.apple_tv_office"})


async def test_switch_on_a_tv_whose_entry_is_not_loaded_reports_it(
    hass, living_room_entry, office_entry, dvr_entry, clients
):
    await setup_integration(hass)
    assert await hass.config_entries.async_unload(office_entry.entry_id)

    with patch(
        "custom_components.channels.services.switch_to_recording",
        AsyncMock(return_value=SwitchResult("15018", True, False)),
    ) as switcher:
        response = await switch(hass, **{ATTR_ENTITY_ID: [LIVING_ROOM, OFFICE]})

    assert response[OFFICE] == {
        "recording_id": None,
        "switched": False,
        "started_recording": False,
        "error": "Its Channels entry is not loaded",
    }
    assert response[LIVING_ROOM]["switched"] is True
    switcher.assert_awaited_once()


INTEGRATION = Path(__file__).parent.parent / "custom_components/channels"


def test_switch_picker_offers_only_channels_players():
    """The schema takes entity IDs only, so the UI must not offer areas."""
    services = yaml.safe_load((INTEGRATION / "services.yaml").read_text())
    switch_service = services["switch_to_recording"]

    assert "target" not in switch_service
    assert switch_service["fields"]["entity_id"] == {
        "required": True,
        "selector": {
            "entity": {
                "integration": "channels",
                "domain": "media_player",
                "multiple": True,
            }
        },
    }
    strings = json.loads((INTEGRATION / "translations/en.json").read_text())
    assert strings["services"]["switch_to_recording"]["fields"]["entity_id"] == {
        "name": "Players",
        "description": "The Channels players to move onto a recording.",
    }


async def test_switch_accepts_a_script_style_target(
    hass, living_room_entry, dvr_entry, clients
):
    await setup_integration(hass)

    with patch(
        "custom_components.channels.services.switch_to_recording",
        AsyncMock(return_value=SwitchResult("15018", True, False)),
    ):
        response = await hass.services.async_call(
            DOMAIN,
            "switch_to_recording",
            {},
            target={ATTR_ENTITY_ID: [LIVING_ROOM]},
            blocking=True,
            return_response=True,
        )

    assert response[LIVING_ROOM]["switched"] is True


async def test_switch_accepts_several_tvs(
    hass, living_room_entry, office_entry, dvr_entry, clients
):
    await setup_integration(hass)

    with patch(
        "custom_components.channels.services.switch_to_recording",
        AsyncMock(return_value=SwitchResult("15018", True, False)),
    ):
        response = await switch(hass, **{ATTR_ENTITY_ID: [LIVING_ROOM, OFFICE]})

    assert set(response) == {LIVING_ROOM, OFFICE}
    assert response[OFFICE]["recording_id"] == "15018"


class OneAtATime:
    """A stand-in switch that records how many calls overlap."""

    def __init__(self) -> None:
        self.running = 0
        self.most = 0
        self.calls = 0

    async def __call__(self, app, dvr, **kwargs) -> SwitchResult:
        self.running += 1
        self.calls += 1
        self.most = max(self.most, self.running)
        try:
            for _ in range(3):
                await asyncio.sleep(0)
        finally:
            self.running -= 1
        return SwitchResult("15018", True, False)


async def test_switch_runs_several_tvs_one_at_a_time(
    hass, living_room_entry, office_entry, dvr_entry, clients
):
    await setup_integration(hass)
    switcher = OneAtATime()

    with patch("custom_components.channels.services.switch_to_recording", switcher):
        response = await switch(hass, **{ATTR_ENTITY_ID: [LIVING_ROOM, OFFICE]})

    assert switcher.most == 1
    assert switcher.calls == 2
    assert response[LIVING_ROOM]["recording_id"] == "15018"
    assert response[OFFICE]["recording_id"] == "15018"


async def test_overlapping_switch_calls_are_run_one_at_a_time(
    hass, living_room_entry, office_entry, dvr_entry, clients
):
    await setup_integration(hass)
    switcher = OneAtATime()

    with patch("custom_components.channels.services.switch_to_recording", switcher):
        first, second = await asyncio.gather(
            switch(hass, **{ATTR_ENTITY_ID: LIVING_ROOM}),
            switch(hass, **{ATTR_ENTITY_ID: OFFICE}),
        )

    assert switcher.most == 1
    assert switcher.calls == 2
    assert first[LIVING_ROOM]["switched"] is True
    assert second[OFFICE]["switched"] is True


async def test_sync_rejects_a_tolerance_the_engine_cannot_reach(
    hass, living_room_entry, office_entry, clients, engine
):
    await setup_integration(hass)

    with pytest.raises(vol.Invalid):
        await sync_playback(hass, tolerance_ms=20)


async def entity_call(hass, service, **data):
    await hass.services.async_call(
        DOMAIN, service, {ATTR_ENTITY_ID: OFFICE, **data}, blocking=True
    )


async def test_seek_by_accepts_fractions_and_negatives(hass, office_entry, clients):
    await setup_integration(hass)

    await entity_call(hass, "seek_by", seconds=-1.5)

    clients["10.0.0.2"].seek.assert_awaited_once_with(-1.5)


async def test_seek_forward_and_backward(hass, office_entry, clients):
    await setup_integration(hass)

    await entity_call(hass, "seek_forward")
    await entity_call(hass, "seek_backward")

    clients["10.0.0.2"].seek_forward.assert_awaited_once()
    clients["10.0.0.2"].seek_backward.assert_awaited_once()
