"""Tests for the media player entity."""

from datetime import timedelta

from homeassistant.components.media_player import (
    ATTR_INPUT_SOURCE,
    ATTR_INPUT_SOURCE_LIST,
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    ATTR_MEDIA_DURATION,
    ATTR_MEDIA_POSITION,
    ATTR_MEDIA_SEEK_POSITION,
    ATTR_MEDIA_SERIES_TITLE,
    ATTR_MEDIA_TITLE,
    ATTR_MEDIA_VOLUME_MUTED,
    DOMAIN as MP_DOMAIN,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.channels.lib import ChannelsConnectionError

from .conftest import OFFICE, make_app_client, setup_integration, status_of
from .fixtures import (
    FAVORITE_CHANNELS,
    STATUS_IN_PROGRESS,
    STATUS_LIVE,
    STATUS_STOPPED,
)


@pytest.fixture
def client(app_clients):
    app_clients["10.0.0.2"] = make_app_client()
    return app_clients["10.0.0.2"]


async def call(hass, domain, service, **data):
    await hass.services.async_call(
        domain, service, {ATTR_ENTITY_ID: OFFICE, **data}, blocking=True
    )


async def test_recording_state(hass, office_entry, client):
    await setup_integration(hass)

    state = hass.states.get(OFFICE)
    assert state.state == "playing"
    assert state.attributes[ATTR_MEDIA_CONTENT_ID] == "15017"
    assert state.attributes[ATTR_MEDIA_CONTENT_TYPE] == "episode"
    assert state.attributes[ATTR_MEDIA_TITLE] == "September 24, 2025"
    assert state.attributes[ATTR_MEDIA_SERIES_TITLE] == "Jeopardy!"
    assert state.attributes[ATTR_MEDIA_POSITION] == 311.9480165
    assert state.attributes[ATTR_MEDIA_DURATION] == 1800.384966
    assert state.attributes["recording_id"] == "15017"
    assert state.attributes[ATTR_INPUT_SOURCE_LIST] == ["KBBB", "KAAA"]


async def test_live_tv_state(hass, office_entry, client):
    client.status.return_value = status_of(STATUS_LIVE)

    await setup_integration(hass)

    state = hass.states.get(OFFICE)
    assert state.state == "playing"
    assert state.attributes[ATTR_MEDIA_CONTENT_ID] == "6.1"
    assert state.attributes[ATTR_MEDIA_CONTENT_TYPE] == "channel"
    assert state.attributes[ATTR_INPUT_SOURCE] == "KAAA"
    assert state.attributes["channel_number"] == "6.1"
    assert ATTR_MEDIA_POSITION not in state.attributes


async def test_in_progress_recording_has_no_duration(hass, office_entry, client):
    client.status.return_value = status_of(STATUS_IN_PROGRESS)

    await setup_integration(hass)

    state = hass.states.get(OFFICE)
    assert state.state == "paused"
    assert state.attributes[ATTR_MEDIA_VOLUME_MUTED] is True
    assert ATTR_MEDIA_DURATION not in state.attributes


async def test_stopped_is_idle(hass, office_entry, client):
    client.status.return_value = status_of(STATUS_STOPPED)

    await setup_integration(hass)

    assert hass.states.get(OFFICE).state == "idle"


async def test_app_not_in_front_at_startup_is_unavailable(hass, office_entry, client):
    client.status.side_effect = ChannelsConnectionError("asleep")

    await setup_integration(hass)

    assert hass.states.get(OFFICE).state == STATE_UNAVAILABLE


async def test_becomes_unavailable_and_comes_back(hass, office_entry, client):
    await setup_integration(hass)

    client.status.side_effect = ChannelsConnectionError("backgrounded")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done()
    assert hass.states.get(OFFICE).state == STATE_UNAVAILABLE

    client.status.side_effect = None
    client.favorite_channels.reset_mock()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=20))
    await hass.async_block_till_done()
    assert hass.states.get(OFFICE).state == "playing"
    client.favorite_channels.assert_awaited_once()


async def test_failing_favourites_do_not_make_the_tv_unavailable(
    hass, office_entry, client
):
    client.favorite_channels.side_effect = ChannelsConnectionError("busy")

    await setup_integration(hass)

    state = hass.states.get(OFFICE)
    assert state.state == "playing"
    assert not state.attributes.get(ATTR_INPUT_SOURCE_LIST)

    client.favorite_channels.side_effect = None
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done()

    state = hass.states.get(OFFICE)
    assert state.state == "playing"
    assert state.attributes[ATTR_INPUT_SOURCE_LIST] == ["KBBB", "KAAA"]

    client.favorite_channels.reset_mock()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=12))
    await hass.async_block_till_done()
    client.favorite_channels.assert_not_awaited()


async def test_failing_favourites_keep_the_previous_list(hass, office_entry, client):
    await setup_integration(hass)

    client.status.side_effect = ChannelsConnectionError("backgrounded")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done()
    client.status.side_effect = None
    client.favorite_channels.side_effect = ChannelsConnectionError("busy")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=20))
    await hass.async_block_till_done()

    state = hass.states.get(OFFICE)
    assert state.state == "playing"
    assert state.attributes[ATTR_INPUT_SOURCE_LIST] == ["KBBB", "KAAA"]


async def test_favourite_without_a_name_is_skipped(hass, office_entry, client):
    client.favorite_channels.return_value = [
        {"number": "2.1", "call_sign": "KCCC"},
        *FAVORITE_CHANNELS,
    ]
    await setup_integration(hass)

    assert hass.states.get(OFFICE).attributes[ATTR_INPUT_SOURCE_LIST] == [
        "KBBB",
        "KAAA",
    ]
    await call(hass, MP_DOMAIN, "select_source", source="KAAA")
    client.play_channel.assert_awaited_once_with("6.1")


async def test_pause_takes_the_reply_as_the_new_state(hass, office_entry, client):
    await setup_integration(hass)
    client.pause.return_value = status_of(STATUS_IN_PROGRESS)

    await call(hass, MP_DOMAIN, "media_pause")

    client.pause.assert_awaited_once()
    assert hass.states.get(OFFICE).state == "paused"


@pytest.mark.parametrize(
    ("service", "method"),
    [
        ("media_play", "resume"),
        ("media_stop", "stop"),
        ("media_next_track", "skip_forward"),
        ("media_previous_track", "skip_backward"),
    ],
)
async def test_transport_commands(hass, office_entry, client, service, method):
    await setup_integration(hass)

    await call(hass, MP_DOMAIN, service)

    getattr(client, method).assert_awaited_once()


async def test_mute_only_toggles_when_the_state_differs(hass, office_entry, client):
    await setup_integration(hass)

    await call(hass, MP_DOMAIN, "volume_mute", **{ATTR_MEDIA_VOLUME_MUTED: False})
    client.toggle_mute.assert_not_awaited()

    await call(hass, MP_DOMAIN, "volume_mute", **{ATTR_MEDIA_VOLUME_MUTED: True})
    client.toggle_mute.assert_awaited_once()


async def test_seek_to_a_position_becomes_a_relative_seek(hass, office_entry, client):
    await setup_integration(hass)

    await call(hass, MP_DOMAIN, "media_seek", **{ATTR_MEDIA_SEEK_POSITION: 400})

    client.seek.assert_awaited_once_with(pytest.approx(400 - 311.9480165))


async def test_seek_to_a_position_on_live_tv_is_refused(hass, office_entry, client):
    client.status.return_value = status_of(STATUS_LIVE)
    await setup_integration(hass)

    with pytest.raises(ServiceValidationError, match="Live TV"):
        await call(hass, MP_DOMAIN, "media_seek", **{ATTR_MEDIA_SEEK_POSITION: 400})


async def test_select_source_tunes_the_favourite(hass, office_entry, client):
    await setup_integration(hass)

    await call(hass, MP_DOMAIN, "select_source", source="KAAA")

    client.play_channel.assert_awaited_once_with("6.1")


async def test_select_unknown_source_is_refused(hass, office_entry, client):
    await setup_integration(hass)

    with pytest.raises(ServiceValidationError, match="not a favourite"):
        await call(hass, MP_DOMAIN, "select_source", source="HBO")


@pytest.mark.parametrize(
    ("media_type", "media_id", "method"),
    [
        ("channel", "6.1", "play_channel"),
        ("episode", "15017", "play_recording"),
        ("movie", "15020", "play_recording"),
    ],
)
async def test_play_media(hass, office_entry, client, media_type, media_id, method):
    await setup_integration(hass)

    await call(
        hass,
        MP_DOMAIN,
        "play_media",
        media_content_type=media_type,
        media_content_id=media_id,
    )

    getattr(client, method).assert_awaited_once_with(media_id)


async def test_play_unsupported_media_type_is_refused(hass, office_entry, client):
    await setup_integration(hass)

    with pytest.raises(ServiceValidationError, match="cannot play"):
        await call(
            hass,
            MP_DOMAIN,
            "play_media",
            media_content_type="music",
            media_content_id="x",
        )


async def test_command_that_fails_raises_a_readable_error(hass, office_entry, client):
    await setup_integration(hass)
    client.pause.side_effect = ChannelsConnectionError("Channels did not answer")

    with pytest.raises(HomeAssistantError, match="did not answer"):
        await call(hass, MP_DOMAIN, "media_pause")
