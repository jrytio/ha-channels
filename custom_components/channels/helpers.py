"""Lookups shared by the entity and the actions."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import CONF_KIND, DOMAIN, KIND_APP, KIND_DVR
from .lib import DvrClient


def async_get_dvr_client(hass: HomeAssistant) -> DvrClient | None:
    """Return the client of the loaded DVR server entry, if any.

    The config flow guarantees at most one DVR server entry.
    """
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        if entry.data[CONF_KIND] == KIND_DVR:
            return entry.runtime_data
    return None


def async_get_app_entry(hass: HomeAssistant, entity_id: str) -> ConfigEntry | None:
    """Return the app entry behind a Channels media player entity.

    The entry may not be loaded; check with `is_loaded` before using its
    runtime data.
    """
    registry_entry = er.async_get(hass).async_get(entity_id)
    if registry_entry is None or registry_entry.platform != DOMAIN:
        return None
    entry = hass.config_entries.async_get_entry(registry_entry.config_entry_id)
    if entry is None or entry.data[CONF_KIND] != KIND_APP:
        return None
    return entry


def is_loaded(entry: ConfigEntry) -> bool:
    """Return True when the entry is set up and its runtime data can be used."""
    return entry.state is ConfigEntryState.LOADED
