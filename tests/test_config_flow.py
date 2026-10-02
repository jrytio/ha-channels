"""Tests for the config and options flows."""

from ipaddress import ip_address

from homeassistant.config_entries import SOURCE_USER, SOURCE_ZEROCONF
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from custom_components.channels.config_flow import ChannelsConfigFlow
from custom_components.channels.const import (
    CONF_KIND,
    CONF_SYNC_OFFSET_MS,
    DOMAIN,
    KIND_APP,
    KIND_DVR,
)
from custom_components.channels.lib import ChannelsConnectionError

from .conftest import make_app_client, setup_integration

APP_FOUND = ZeroconfServiceInfo(
    ip_address=ip_address("10.0.0.1"),
    ip_addresses=[ip_address("10.0.0.1")],
    port=57000,
    hostname="Living-Area.local.",
    type="_channels_app._tcp.local.",
    name="Apple TV (2)._channels_app._tcp.local.",
    properties={},
)
DVR_FOUND = ZeroconfServiceInfo(
    ip_address=ip_address("10.0.0.9"),
    ip_addresses=[ip_address("10.0.0.9")],
    port=8089,
    hostname="dvr-nas6.local.",
    type="_channels_dvr._tcp.local.",
    name="nas6._channels_dvr._tcp.local.",
    properties={"version": "2026.08.07.0346"},
)


async def discover(hass, info):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=info
    )


async def test_discovered_app_is_named_after_its_host(hass):
    result = await discover(hass, APP_FOUND)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Living Room Channels"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Living Room Channels"
    assert result["data"] == {
        CONF_KIND: KIND_APP,
        CONF_HOST: "10.0.0.1",
        CONF_PORT: 57000,
    }
    assert result["result"].unique_id == "app_living-area.local"


async def test_discovery_suggests_a_name(hass):
    app = await discover(hass, APP_FOUND)
    dvr = await discover(hass, DVR_FOUND)

    def suggested(result):
        return next(iter(result["data_schema"].schema)).default()

    assert suggested(app) == "Living Area"
    assert suggested(dvr) == "Channels DVR nas6"


async def test_discovered_dvr_server(hass):
    result = await discover(hass, DVR_FOUND)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Channels DVR nas6"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_KIND] == KIND_DVR
    assert result["data"][CONF_PORT] == 8089
    assert result["result"].unique_id == "dvr_dvr-nas6.local"


async def test_discovered_app_that_does_not_answer_is_dropped(hass, app_clients):
    app_clients["10.0.0.1"] = make_app_client()
    app_clients["10.0.0.1"].status.side_effect = ChannelsConnectionError("no")

    result = await discover(hass, APP_FOUND)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_rediscovery_at_a_new_address_updates_the_host(hass, living_room_entry):
    hass.config_entries.async_update_entry(
        living_room_entry, unique_id="app_living-area.local"
    )
    moved = ZeroconfServiceInfo(
        ip_address=ip_address("10.0.0.77"),
        ip_addresses=[ip_address("10.0.0.77")],
        port=57000,
        hostname="Living-Area.local.",
        type="_channels_app._tcp.local.",
        name="Apple TV (2)._channels_app._tcp.local.",
        properties={},
    )

    result = await discover(hass, moved)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert living_room_entry.data[CONF_HOST] == "10.0.0.77"


async def test_device_added_by_hand_is_not_offered_again_by_discovery(
    hass, living_room_entry
):
    # living_room_entry stands for a manual entry at 10.0.0.1.
    result = await discover(hass, APP_FOUND)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_discovered_device_cannot_be_added_again_by_hand(hass):
    result = await discover(hass, APP_FOUND)
    await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Living Room Channels"}
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_KIND: KIND_APP, CONF_NAME: "Again", CONF_HOST: "10.0.0.1"},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_pending_discovery_cannot_add_a_device_that_was_since_added_by_hand(
    hass,
):
    pending = await discover(hass, APP_FOUND)
    assert pending["type"] is FlowResultType.FORM
    assert pending["step_id"] == "confirm"

    manual = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    manual = await hass.config_entries.flow.async_configure(
        manual["flow_id"],
        {CONF_KIND: KIND_APP, CONF_NAME: "By hand", CONF_HOST: "10.0.0.1"},
    )
    assert manual["type"] is FlowResultType.CREATE_ENTRY

    result = await hass.config_entries.flow.async_configure(
        pending["flow_id"], {CONF_NAME: "Living Room Channels"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_manual_entry_uses_the_default_port_for_its_kind(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_KIND: KIND_DVR, CONF_NAME: "Channels DVR", CONF_HOST: "10.0.0.9"},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_KIND: KIND_DVR,
        CONF_HOST: "10.0.0.9",
        CONF_PORT: 8089,
    }


async def test_manual_entry_that_does_not_answer_can_be_retried(hass, app_clients):
    app_clients["10.0.0.2"] = make_app_client()
    app_clients["10.0.0.2"].status.side_effect = ChannelsConnectionError("no")
    entered = {CONF_KIND: KIND_APP, CONF_NAME: "Office Channels", CONF_HOST: "10.0.0.2"}

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], entered)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    app_clients["10.0.0.2"].status.side_effect = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], entered)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PORT] == 57000


async def test_manual_entry_of_a_known_device_is_refused(hass):
    entered = {CONF_KIND: KIND_APP, CONF_NAME: "Office Channels", CONF_HOST: "10.0.0.2"}
    for expected in (FlowResultType.CREATE_ENTRY, FlowResultType.ABORT):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], entered
        )
        assert result["type"] is expected


async def test_app_options_set_the_sync_offset(hass, office_entry):
    await setup_integration(hass)

    result = await hass.config_entries.options.async_init(office_entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SYNC_OFFSET_MS: 80}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert office_entry.options == {CONF_SYNC_OFFSET_MS: 80}


async def test_only_apps_have_options(hass, office_entry, dvr_entry):
    assert ChannelsConfigFlow.async_supports_options_flow(office_entry) is True
    assert ChannelsConfigFlow.async_supports_options_flow(dvr_entry) is False


OTHER_DVR_FOUND = ZeroconfServiceInfo(
    ip_address=ip_address("10.0.0.10"),
    ip_addresses=[ip_address("10.0.0.10")],
    port=8089,
    hostname="dvr-other.local.",
    type="_channels_dvr._tcp.local.",
    name="other._channels_dvr._tcp.local.",
    properties={},
)


async def test_a_second_discovered_dvr_server_is_refused(hass, dvr_entry):
    result = await discover(hass, OTHER_DVR_FOUND)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_dvr_only"


async def test_a_second_dvr_server_entered_by_hand_is_refused(hass, dvr_entry):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_KIND: KIND_DVR, CONF_NAME: "Second", CONF_HOST: "10.0.0.10"},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_dvr_only"


async def test_the_same_dvr_server_at_a_new_address_updates_its_host(hass, dvr_entry):
    moved = ZeroconfServiceInfo(
        ip_address=ip_address("10.0.0.77"),
        ip_addresses=[ip_address("10.0.0.77")],
        port=8089,
        hostname="dvr-nas6.local.",
        type="_channels_dvr._tcp.local.",
        name="nas6._channels_dvr._tcp.local.",
        properties={},
    )

    result = await discover(hass, moved)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert dvr_entry.data[CONF_HOST] == "10.0.0.77"


async def test_an_app_can_be_added_while_a_dvr_server_exists(hass, dvr_entry):
    result = await discover(hass, APP_FOUND)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
