"""Tests for setting entries up and tearing them down."""

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr

from custom_components.channels.const import DOMAIN
from custom_components.channels.lib import ChannelsConnectionError

from .conftest import make_app_client, setup_integration


async def test_app_that_is_not_in_front_still_sets_up(hass, office_entry, app_clients):
    app_clients["10.0.0.2"] = make_app_client()
    app_clients["10.0.0.2"].status.side_effect = ChannelsConnectionError("asleep")

    await setup_integration(hass)

    assert office_entry.state is ConfigEntryState.LOADED


async def test_dvr_server_gets_a_device_and_no_entities(hass, dvr_entry):
    await setup_integration(hass)

    assert dvr_entry.state is ConfigEntryState.LOADED
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, "dvr_dvr-nas6.local"), dvr_entry.entry_id
    )
    assert device.name == "Channels DVR nas6"
    assert device.sw_version == "2026.08.07.0346"
    assert hass.states.async_entity_ids() == []


async def test_dvr_server_that_does_not_answer_is_retried(hass, dvr_entry, dvr_client):
    dvr_client.status.side_effect = ChannelsConnectionError("down")

    await setup_integration(hass)

    assert dvr_entry.state is ConfigEntryState.SETUP_RETRY


async def test_entries_unload(hass, office_entry, dvr_entry):
    await setup_integration(hass)

    assert await hass.config_entries.async_unload(office_entry.entry_id)
    assert await hass.config_entries.async_unload(dvr_entry.entry_id)

    assert office_entry.state is ConfigEntryState.NOT_LOADED
    assert dvr_entry.state is ConfigEntryState.NOT_LOADED
