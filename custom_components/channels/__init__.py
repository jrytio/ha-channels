"""The Channels integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .const import CONF_KIND, DOMAIN, KIND_APP, MANUFACTURER
from .coordinator import ChannelsAppCoordinator
from .follow import async_stop_follow
from .lib import AppClient, ChannelsError, DvrClient
from .services import async_setup_services

APP_PLATFORMS = [Platform.MEDIA_PLAYER]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type ChannelsConfigEntry = ConfigEntry[ChannelsAppCoordinator | DvrClient]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions once, for every entry."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ChannelsConfigEntry) -> bool:
    """Set up one Channels app or one DVR server."""
    session = async_get_clientsession(hass)
    host, port = entry.data[CONF_HOST], entry.data[CONF_PORT]

    if entry.data[CONF_KIND] == KIND_APP:
        coordinator = ChannelsAppCoordinator(
            hass, entry, AppClient(host, session, port)
        )
        # The app is often not in the foreground when Home Assistant starts.
        # Set up regardless; the entity shows as unavailable until it answers.
        await coordinator.async_refresh()
        entry.runtime_data = coordinator
        await hass.config_entries.async_forward_entry_setups(entry, APP_PLATFORMS)
        return True

    client = DvrClient(host, session, port)
    try:
        status = await client.status()
    except ChannelsError as err:
        raise ConfigEntryNotReady(str(err)) from err
    entry.runtime_data = client
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.unique_id)},
        manufacturer=MANUFACTURER,
        model="Channels DVR Server",
        name=entry.title,
        sw_version=status.get("version"),
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ChannelsConfigEntry) -> bool:
    """Unload an entry."""
    if entry.data[CONF_KIND] == KIND_APP:
        # Its task ends with the entry anyway; this also clears the record of it.
        async_stop_follow(hass, entry.entry_id)
        return await hass.config_entries.async_unload_platforms(entry, APP_PLATFORMS)
    return True
