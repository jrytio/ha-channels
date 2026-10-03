"""Tests for the follow actions and what they show in Home Assistant."""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import probatio as vol
import pytest
from pytest_homeassistant_custom_component.common import async_capture_events
import yaml

from custom_components.channels.const import (
    CONF_SYNC_OFFSET_MS,
    DOMAIN,
    EVENT_FOLLOW_PROBLEM,
)
from custom_components.channels.diagnostics import async_get_config_entry_diagnostics
from custom_components.channels.lib import ChannelsConnectionError
from custom_components.channels.services import (
    START_FOLLOW_SCHEMA,
    STOP_FOLLOW_SCHEMA,
)

from .conftest import (
    LIVING_ROOM,
    OFFICE,
    app_entry,
    make_app_client,
    setup_integration,
)

BACK_YARD = "media_player.back_yard_channels"


class FakeSession:
    """Stands in for the engine, which has its own tests."""

    instances: list[FakeSession] = []

    def __init__(self, leader, followers, **options):
        self.leader = leader
        self.followers = followers
        self.options = options
        self.statuses = dict.fromkeys(followers, "absent")
        self.cancelled = False
        self.ended = asyncio.Event()
        self.failure: Exception | None = None
        FakeSession.instances.append(self)

    async def run(self) -> None:
        try:
            await self.ended.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.failure:
            raise self.failure

    def finish(self) -> None:
        """End run() by returning."""
        self.ended.set()

    def fail(self) -> None:
        """End run() by raising."""
        self.failure = RuntimeError("the session broke")
        self.ended.set()

    def set_status(self, follower: str, status: str) -> None:
        self.statuses[follower] = status
        self.options["on_status"](follower, status)


@pytest.fixture
def sessions():
    FakeSession.instances = []
    with patch("custom_components.channels.follow.FollowSession", FakeSession):
        yield FakeSession.instances


@pytest.fixture
def clients(app_clients):
    app_clients["10.0.0.1"] = make_app_client()
    app_clients["10.0.0.2"] = make_app_client()
    return app_clients


async def start_follow(hass, **data):
    await hass.services.async_call(
        DOMAIN,
        "start_follow",
        {"leader": LIVING_ROOM, "followers": [OFFICE], **data},
        blocking=True,
    )
    await hass.async_block_till_done()


async def stop_follow(hass, leader=LIVING_ROOM):
    await hass.services.async_call(
        DOMAIN, "stop_follow", {"leader": leader}, blocking=True
    )
    await hass.async_block_till_done()


async def test_start_runs_a_session_with_the_leader_and_followers(
    hass, living_room_entry, office_entry, clients, dvr_entry, dvr_client, sessions
):
    await setup_integration(hass)

    await start_follow(hass)

    (session,) = sessions
    assert session.leader is clients["10.0.0.1"]
    assert list(session.followers) == [OFFICE]
    assert session.followers[OFFICE].target == 0
    assert session.options["dvr"] is dvr_client
    assert session.options["tolerance"] == pytest.approx(0.25)
    assert session.options["live_settle"] == 10
    assert session.options["behind_live"] == 5
    assert not session.cancelled


async def test_start_passes_the_call_options_and_each_tvs_offset(
    hass, living_room_entry, office_entry, clients, sessions
):
    hass.config_entries.async_update_entry(
        office_entry, options={CONF_SYNC_OFFSET_MS: 80}
    )
    await setup_integration(hass)

    await start_follow(hass, tolerance_ms=400, live_settle=20, behind_live=8)

    (session,) = sessions
    assert session.followers[OFFICE].target == pytest.approx(-0.08)
    assert session.options["tolerance"] == pytest.approx(0.4)
    assert session.options["live_settle"] == 20
    assert session.options["behind_live"] == 8
    assert session.options["dvr"] is None


async def test_follower_commands_reach_its_app(
    hass, living_room_entry, office_entry, clients, sessions
):
    await setup_integration(hass)
    await start_follow(hass)
    player = sessions[0].followers[OFFICE].player

    await player.status()
    await player.play_recording("15017")
    await player.seek(-3.5)
    await player.pause()
    await player.resume()

    office = clients["10.0.0.2"]
    office.play_recording.assert_awaited_once_with("15017")
    office.seek.assert_awaited_once_with(-3.5)
    office.pause.assert_awaited_once()
    office.resume.assert_awaited_once()


async def test_follower_whose_entry_is_unloaded_does_not_answer(
    hass, living_room_entry, office_entry, clients, sessions
):
    await setup_integration(hass)
    await start_follow(hass)
    player = sessions[0].followers[OFFICE].player
    clients["10.0.0.2"].status.reset_mock()

    await hass.config_entries.async_unload(office_entry.entry_id)

    with pytest.raises(ChannelsConnectionError):
        await player.status()
    with pytest.raises(ChannelsConnectionError):
        await player.pause()
    clients["10.0.0.2"].status.assert_not_awaited()
    clients["10.0.0.2"].pause.assert_not_awaited()


async def test_followers_not_set_up_and_the_leader_itself_are_left_out(
    hass, living_room_entry, office_entry, clients, sessions
):
    await setup_integration(hass)

    await start_follow(hass, followers=[OFFICE, BACK_YARD, LIVING_ROOM, OFFICE])

    assert list(sessions[0].followers) == [OFFICE]


async def test_a_tv_cannot_follow_two_leaders_or_lead_while_following(
    hass, living_room_entry, office_entry, clients, sessions
):
    app_entry(hass, "Back Yard", "10.0.0.3")
    await setup_integration(hass)
    await start_follow(hass)

    with pytest.raises(ServiceValidationError, match="already following"):
        await start_follow(hass, leader=BACK_YARD, followers=[OFFICE])
    with pytest.raises(ServiceValidationError, match="itself following"):
        await start_follow(hass, leader=OFFICE, followers=[BACK_YARD])

    assert len(sessions) == 1
    assert not sessions[0].cancelled


async def test_a_leader_cannot_be_made_a_follower(
    hass, living_room_entry, office_entry, clients, sessions
):
    app_entry(hass, "Back Yard", "10.0.0.3")
    await setup_integration(hass)
    await start_follow(hass)

    with pytest.raises(ServiceValidationError, match="leading"):
        await start_follow(hass, leader=BACK_YARD, followers=[LIVING_ROOM])

    assert len(sessions) == 1
    assert not sessions[0].cancelled


async def test_no_followers_left_starts_no_session(
    hass, living_room_entry, office_entry, clients, sessions
):
    await setup_integration(hass)

    await start_follow(hass, followers=[BACK_YARD])

    assert sessions == []
    assert "followed_by" not in hass.states.get(LIVING_ROOM).attributes


async def test_no_followers_left_ends_the_leaders_session(
    hass, living_room_entry, office_entry, clients, sessions
):
    await setup_integration(hass)
    await start_follow(hass)

    await start_follow(hass, followers=[LIVING_ROOM])

    assert len(sessions) == 1
    assert sessions[0].cancelled
    assert "following" not in hass.states.get(OFFICE).attributes
    assert "followed_by" not in hass.states.get(LIVING_ROOM).attributes


@pytest.mark.parametrize("how", ["finish", "fail"])
async def test_a_session_that_ends_by_itself_is_forgotten(
    hass, living_room_entry, office_entry, clients, sessions, caplog, how
):
    app_entry(hass, "Back Yard", "10.0.0.3")
    await setup_integration(hass)
    await start_follow(hass)

    getattr(sessions[0], how)()
    # Background tasks are not waited for by async_block_till_done.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await hass.async_block_till_done()

    assert "following" not in hass.states.get(OFFICE).attributes
    assert "followed_by" not in hass.states.get(LIVING_ROOM).attributes
    assert ("the session broke" in caplog.text) == (how == "fail")
    await start_follow(hass, leader=BACK_YARD, followers=[OFFICE])
    assert len(sessions) == 2


async def test_a_replaced_sessions_end_does_not_remove_its_successor(
    hass, living_room_entry, office_entry, clients, sessions
):
    await setup_integration(hass)

    await start_follow(hass)
    await start_follow(hass)
    await hass.async_block_till_done()

    assert sessions[0].cancelled
    assert hass.states.get(LIVING_ROOM).attributes["followed_by"] == [OFFICE]


async def test_starting_again_replaces_the_session(
    hass, living_room_entry, office_entry, clients, sessions
):
    await setup_integration(hass)

    await start_follow(hass)
    await start_follow(hass)

    first, second = sessions
    assert first.cancelled
    assert not second.cancelled


async def test_stop_ends_the_session(
    hass, living_room_entry, office_entry, clients, sessions
):
    await setup_integration(hass)
    await start_follow(hass)

    await stop_follow(hass)

    assert sessions[0].cancelled
    assert "following" not in hass.states.get(OFFICE).attributes


async def test_stop_with_no_session_does_nothing(
    hass, living_room_entry, office_entry, clients, sessions
):
    await setup_integration(hass)

    await stop_follow(hass)

    assert sessions == []


async def test_attributes_show_who_follows_whom(
    hass, living_room_entry, office_entry, clients, sessions
):
    await setup_integration(hass)
    assert "followed_by" not in hass.states.get(LIVING_ROOM).attributes

    await start_follow(hass)

    assert hass.states.get(LIVING_ROOM).attributes["followed_by"] == [OFFICE]
    office = hass.states.get(OFFICE).attributes
    assert office["following"] == LIVING_ROOM
    assert office["follow_status"] == "absent"


async def test_a_status_change_is_shown_at_once(
    hass, living_room_entry, office_entry, clients, sessions
):
    await setup_integration(hass)
    await start_follow(hass)

    sessions[0].set_status(OFFICE, "correcting")
    await hass.async_block_till_done()

    assert hass.states.get(OFFICE).attributes["follow_status"] == "correcting"


async def test_a_problem_is_fired_as_an_event(
    hass, living_room_entry, office_entry, clients, sessions
):
    events = async_capture_events(hass, EVENT_FOLLOW_PROBLEM)
    await setup_integration(hass)
    await start_follow(hass)

    sessions[0].options["on_problem"](None, "The recording did not start")
    sessions[0].options["on_problem"](OFFICE, "It could not be kept in step")
    await hass.async_block_till_done()

    assert [event.data for event in events] == [
        {
            "leader": LIVING_ROOM,
            "follower": None,
            "message": "The recording did not start",
        },
        {
            "leader": LIVING_ROOM,
            "follower": OFFICE,
            "message": "It could not be kept in step",
        },
    ]


async def test_unloading_the_leader_ends_its_session(
    hass, living_room_entry, office_entry, clients, sessions
):
    await setup_integration(hass)
    await start_follow(hass)

    await hass.config_entries.async_unload(living_room_entry.entry_id)
    await hass.async_block_till_done()

    assert sessions[0].cancelled
    assert "following" not in hass.states.get(OFFICE).attributes


async def test_leader_that_is_not_a_channels_player_is_refused(
    hass, living_room_entry, office_entry, clients, sessions
):
    await setup_integration(hass)

    with pytest.raises(ServiceValidationError):
        await start_follow(hass, leader="media_player.kitchen_speaker")
    with pytest.raises(ServiceValidationError):
        await stop_follow(hass, leader="media_player.kitchen_speaker")
    assert sessions == []


async def test_leader_whose_entry_is_not_loaded_is_refused(
    hass, living_room_entry, office_entry, clients, sessions
):
    await setup_integration(hass)
    await hass.config_entries.async_unload(living_room_entry.entry_id)

    with pytest.raises(HomeAssistantError, match="not loaded"):
        await start_follow(hass)
    assert sessions == []


@pytest.mark.parametrize(
    "data",
    [
        {"tolerance_ms": 50},
        {"tolerance_ms": 1000},
        {"live_settle": -1},
        {"behind_live": 1},
    ],
)
async def test_fields_out_of_range_are_refused(
    hass, living_room_entry, office_entry, clients, sessions, data
):
    await setup_integration(hass)

    with pytest.raises(vol.Invalid):
        await start_follow(hass, **data)
    assert sessions == []


async def test_diagnostics_list_the_leaders_session(
    hass, living_room_entry, office_entry, clients, sessions
):
    await setup_integration(hass)
    info = await async_get_config_entry_diagnostics(hass, living_room_entry)
    assert info["follow_session"] is None

    await start_follow(hass)
    sessions[0].set_status(OFFICE, "in_sync")

    info = await async_get_config_entry_diagnostics(hass, living_room_entry)
    assert info["follow_session"] == {OFFICE: "in_sync"}


INTEGRATION = Path(__file__).parent.parent / "custom_components/channels"


async def test_every_action_and_field_is_described(hass):
    """An action or field with no description shows as a raw key in the UI."""
    await setup_integration(hass)
    described = yaml.safe_load((INTEGRATION / "services.yaml").read_text())
    strings = json.loads((INTEGRATION / "translations/en.json").read_text())["services"]

    registered = set(hass.services.async_services_for_domain(DOMAIN))
    assert registered == set(described) == set(strings)
    for name, action in described.items():
        assert strings[name]["name"]
        assert strings[name]["description"]
        fields = set(action.get("fields", {}))
        assert fields == set(strings[name].get("fields", {})), name
    for schema, name in (
        (START_FOLLOW_SCHEMA, "start_follow"),
        (STOP_FOLLOW_SCHEMA, "stop_follow"),
    ):
        assert {str(key) for key in schema.schema} == set(described[name]["fields"])
